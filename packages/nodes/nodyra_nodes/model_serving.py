"""Model serving, deployment, and endpoint testing nodes for Nodyra."""

from __future__ import annotations

import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from nodyra.artifacts import write_text
from nodyra.datasets import is_dataset_ref
from nodyra.sdk import node
from nodyra_nodes.datasets import materialize_dataset, records_to_dataset

ML_CATEGORY = "Machine Learning"
MAX_ENDPOINT_REQUESTS = 1_000
MAX_ENDPOINT_CONCURRENCY = 50
MAX_SHADOW_PROMPTS = 1_000


def _require_requests():
    try:
        import requests

        return requests
    except ImportError as exc:
        raise RuntimeError(
            "This node requires 'requests'. Add it to your workflow environment."
        ) from exc


def _to_records(val: Any) -> list[dict]:
    if is_dataset_ref(val):
        return materialize_dataset(val, cap=10_000, allow_truncate=True)
    if isinstance(val, list):
        return [r for r in val if isinstance(r, dict)]
    if isinstance(val, dict):
        # Trigger payloads and pinned data commonly wrap records under
        # "rows"/"records"; accept those so a manual_trigger can seed the node.
        for key in ("records", "rows"):
            nested = val.get(key)
            if isinstance(nested, list):
                return [r for r in nested if isinstance(r, dict)]
        return [val]
    return []


def _parse_json(s: str, default: Any) -> Any:
    if not s or not s.strip():
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


def _auth_headers(api_key: str) -> dict:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _bounded_int(name: str, value: Any, default: int, max_value: int) -> int:
    try:
        result = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result < 1:
        raise ValueError(f"{name} must be >= 1")
    if result > max_value:
        raise ValueError(f"{name} must be <= {max_value}")
    return result


def _check_models_list(req, base_url: str, api_key: str, timeout: float) -> dict:
    url = base_url.rstrip("/") + "/v1/models"
    try:
        t0 = time.time()
        r = req.get(url, headers=_auth_headers(api_key), timeout=timeout)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            models = [m.get("id", "") for m in r.json().get("data", [])]
            return {"ok": True, "latency_ms": ms, "models": models}
        return {"ok": False, "latency_ms": ms, "models": [], "error": r.text[:300]}
    except Exception as e:
        return {"ok": False, "latency_ms": None, "models": [], "error": str(e)[:300]}


def _send_completion(
    req,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
) -> dict:
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": False,
    }
    t0 = time.time()
    try:
        r = req.post(url, headers=_auth_headers(api_key), json=payload, timeout=timeout)
        ms = (time.time() - t0) * 1000
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        content = ""
        if data.get("choices"):
            content = data["choices"][0].get("message", {}).get("content", "")
        return {
            "success": True,
            "latency_ms": ms,
            "content": content,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "status_code": r.status_code,
        }
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {
            "success": False,
            "latency_ms": ms,
            "content": "",
            "error": str(e)[:300],
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(int(len(s) * p / 100), len(s) - 1)]


# ============================================================
# model_deployment_spec
# ============================================================


@node(
    name="Generate Model Deployment Spec",
    id="model_deployment_spec",
    category=ML_CATEGORY,
    icon="file-code",
    description=(
        "Generate a Docker Compose, Kubernetes, or shell-command deployment spec "
        "for a local OpenAI-compatible model server (Ollama, vLLM, etc.). "
        "Returns the spec as a text artifact and the base URL for downstream nodes."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "runtime": {
            "choices": ["ollama", "vllm", "openai-compatible"],
            "description": "Model server runtime.",
        },
        "model": {
            "description": "Model name or Hugging Face repo id.",
        },
        "port": {
            "description": "Port to expose the server on.",
        },
        "gpu": {
            "description": "Add GPU resource requirements to the spec.",
        },
        "replicas": {
            "group": "Options",
            "description": "Number of server replicas (Kubernetes).",
        },
        "context_length": {
            "group": "Options",
            "description": "Context window length in tokens.",
        },
        "format": {
            "group": "Options",
            "choices": ["docker-compose", "kubernetes", "shell-commands"],
            "description": "Output format for the deployment spec.",
        },
        "extra_env": {
            "group": "Options",
            "description": "Extra environment variables as a JSON object.",
        },
    },
    param_groups={"Options": []},
)
def model_deployment_spec(
    input: Any = None,
    runtime: str = "ollama",
    model: str = "llama3.2",
    port: int = 11434,
    gpu: bool = False,
    replicas: int = 1,
    context_length: int = 4096,
    format: str = "docker-compose",
    extra_env: str = "{}",
) -> dict[str, Any]:
    """Generate a deployment specification for a local OpenAI-compatible model server."""
    extra = _parse_json(extra_env, {})
    safe_model = model.replace("/", "-").replace(":", "-")

    if runtime == "ollama":
        env_vars = {
            "OLLAMA_NUM_PARALLEL": "2",
            "OLLAMA_CONTEXT_LENGTH": str(int(context_length)),
        }
        env_vars.update(extra)

        if format == "docker-compose":
            env_lines = "\n".join(f"      - {k}={v}" for k, v in env_vars.items())
            gpu_block = (
                (
                    "\n    deploy:\n      resources:\n        reservations:\n"
                    "          devices:\n            - driver: nvidia\n"
                    "              count: 1\n              capabilities: [gpu]"
                )
                if gpu
                else ""
            )
            spec = (
                f'version: "3.9"\nservices:\n  ollama:\n'
                f"    image: ollama/ollama:latest\n"
                f'    ports:\n      - "{port}:11434"\n'
                f"    volumes:\n      - ollama_data:/root/.ollama\n"
                f"    environment:\n{env_lines}{gpu_block}\n\n"
                f"volumes:\n  ollama_data:\n"
            )
            commands = (
                f"docker-compose up -d\n"
                f"docker exec -it $(docker-compose ps -q ollama) ollama pull {model}"
            )
        elif format == "kubernetes":
            env_lines = "\n".join(
                f'        - name: {k}\n          value: "{v}"' for k, v in env_vars.items()
            )
            spec = (
                f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n"
                f"  name: ollama-{safe_model}\nspec:\n  replicas: {replicas}\n"
                f"  selector:\n    matchLabels:\n      app: ollama-{safe_model}\n"
                f"  template:\n    metadata:\n      labels:\n        app: ollama-{safe_model}\n"
                f"    spec:\n      containers:\n      - name: ollama\n"
                f"        image: ollama/ollama:latest\n"
                f"        ports:\n        - containerPort: 11434\n"
                f"        env:\n{env_lines}\n"
            )
            commands = (
                f"kubectl apply -f spec.yaml\n"
                f"kubectl exec -it deployment/ollama-{safe_model} -- ollama pull {model}"
            )
        else:
            env_exports = "\n".join(f"export {k}={v}" for k, v in env_vars.items())
            gpu_flag = "--gpus all " if gpu else ""
            spec = (
                f"#!/bin/bash\n{env_exports}\n"
                f"docker run -d --name ollama-{safe_model} {gpu_flag}-p {port}:11434 "
                f"ollama/ollama\ndocker exec ollama-{safe_model} ollama pull {model}"
            )
            commands = spec

    elif runtime == "vllm":
        env_vars = {"VLLM_API_KEY": "change-me"}
        env_vars.update(extra)
        if format == "docker-compose":
            env_lines = "\n".join(f"      - {k}={v}" for k, v in env_vars.items())
            gpu_block = (
                (
                    "\n    deploy:\n      resources:\n        reservations:\n"
                    "          devices:\n            - driver: nvidia\n"
                    "              count: 1\n              capabilities: [gpu]"
                )
                if gpu
                else ""
            )
            spec = (
                f'version: "3.9"\nservices:\n  vllm:\n'
                f"    image: vllm/vllm-openai:latest\n"
                f'    ports:\n      - "{port}:8000"\n'
                f"    environment:\n{env_lines}\n"
                f'    command: ["--model", "{model}", "--host", "0.0.0.0", '
                f'"--port", "8000", "--max-model-len", "{context_length}"]\n'
                f"{gpu_block}\n"
            )
            commands = f"docker-compose up -d\n# Endpoint: http://localhost:{port}/v1"
        else:
            gpu_flag = "--num-gpus 1" if gpu else ""
            spec = (
                f"# vLLM server for {model}\n"
                f"vllm serve {model} --port {port} {gpu_flag} "
                f"--max-model-len {int(context_length)}"
            )
            commands = spec
    else:
        spec = (
            f"# OpenAI-compatible endpoint for {model}\n"
            f"# Serve on port {port} with an OpenAI-compatible /v1 API.\n"
            f"# Set OPENAI_BASE_URL=http://localhost:{port}/v1 in your clients."
        )
        commands = ""

    artifact_ref = write_text(spec, name=f"deployment-spec-{runtime}-{safe_model}.txt")

    return {
        "runtime": runtime,
        "model": model,
        "port": int(port),
        "format": format,
        "base_url": f"http://localhost:{port}/v1",
        "spec_artifact": artifact_ref,
        "setup_commands": commands,
    }


# ============================================================
# model_endpoint_probe
# ============================================================


@node(
    name="Probe Model Endpoint",
    id="model_endpoint_probe",
    category=ML_CATEGORY,
    icon="activity",
    description=(
        "Probe an OpenAI-compatible model endpoint for health, available models, "
        "and response latency. Routes to 'pass' if healthy, 'fail' if unhealthy "
        "and fail_if_unhealthy is enabled, or 'main' otherwise."
    ),
    inputs=["input"],
    outputs=["main", "pass", "fail"],
    requirements=["requests>=2.28"],
    params={
        "base_url": {
            "description": "Base URL without /v1 (e.g. http://localhost:11434).",
        },
        "model": {
            "description": "Model name. Leave blank to auto-select first available.",
        },
        "api_key": {
            "description": "API key / bearer token (leave blank if not needed).",
        },
        "test_prompt": {
            "description": "Prompt to send as a test completion.",
        },
        "max_tokens": {
            "description": "Max tokens for the test completion response.",
        },
        "timeout_seconds": {
            "description": "Request timeout in seconds.",
        },
        "check_models_list": {
            "description": "Check /v1/models endpoint.",
        },
        "check_completion": {
            "description": "Send a test /v1/chat/completions request.",
        },
        "fail_if_unhealthy": {
            "description": "Route to fail output when endpoint is unhealthy.",
        },
    },
)
def model_endpoint_probe(
    input: Any = None,
    base_url: str = "http://localhost:11434",
    model: str = "",
    api_key: str = "",
    test_prompt: str = "Hello",
    max_tokens: int = 16,
    timeout_seconds: float = 30,
    check_models_list: bool = True,
    check_completion: bool = True,
    fail_if_unhealthy: bool = False,
) -> dict[str, Any]:
    """Probe an OpenAI-compatible model endpoint for health and latency."""
    req = _require_requests()
    checks: list[dict] = []
    is_healthy = True
    active_model = (model or "").strip()

    if check_models_list:
        result = _check_models_list(req, base_url, api_key, float(timeout_seconds))
        checks.append({"check": "models_list", **result})
        if not result["ok"]:
            is_healthy = False
        elif not active_model and result["models"]:
            active_model = result["models"][0]

    completion_result: dict = {}
    if check_completion and active_model:
        completion_result = _send_completion(
            req,
            base_url,
            api_key,
            active_model,
            test_prompt,
            int(max_tokens),
            float(timeout_seconds),
        )
        checks.append({"check": "completion", **completion_result})
        if not completion_result.get("success"):
            is_healthy = False

    summary = {
        "is_healthy": is_healthy,
        "base_url": base_url,
        "model": active_model,
        "n_checks": len(checks),
        "checks_passed": sum(1 for c in checks if c.get("ok", c.get("success", False))),
        "completion_latency_ms": completion_result.get("latency_ms"),
        "models_latency_ms": next(
            (c["latency_ms"] for c in checks if c.get("check") == "models_list"), None
        ),
    }

    if fail_if_unhealthy and not is_healthy:
        return {"fail": summary}
    return {"pass": summary} if is_healthy else {"main": summary}


# ============================================================
# model_endpoint_benchmark
# ============================================================


@node(
    name="Benchmark Model Endpoint",
    id="model_endpoint_benchmark",
    category=ML_CATEGORY,
    icon="bar-chart",
    description=(
        "Run a concurrent load benchmark against an OpenAI-compatible model endpoint. "
        "Measures p50/p95/p99 latency, tokens/sec, and error rate. "
        "Returns a summary dict and a DatasetRef of per-request results."
    ),
    inputs=["input"],
    outputs=["main", "rows"],
    requirements=["requests>=2.28"],
    params={
        "base_url": {
            "description": "Base URL without /v1.",
        },
        "model": {
            "description": "Model name to benchmark.",
        },
        "api_key": {
            "description": "API key / bearer token.",
        },
        "prompts": {
            "description": "JSON array of test prompts. Used in round-robin order.",
            "multiline": True,
        },
        "n_requests": {
            "description": "Total number of requests to send.",
        },
        "concurrency": {
            "description": "Maximum concurrent requests.",
        },
        "max_tokens": {
            "description": "Max tokens per response.",
        },
        "timeout_seconds": {
            "description": "Per-request timeout in seconds.",
        },
    },
)
def model_endpoint_benchmark(
    input: Any = None,
    base_url: str = "http://localhost:11434",
    model: str = "llama3.2",
    api_key: str = "",
    prompts: str = (
        '["Summarize AI in one sentence.", "What is machine learning?", "Explain transformers."]'
    ),
    n_requests: int = 10,
    concurrency: int = 2,
    max_tokens: int = 64,
    timeout_seconds: float = 60,
) -> dict[str, Any]:
    """Run a concurrent load benchmark against an OpenAI-compatible endpoint."""
    prompt_list = _parse_json(prompts, ["Hello"])
    if not prompt_list:
        prompt_list = ["Hello"]
    request_count = _bounded_int("n_requests", n_requests, 10, MAX_ENDPOINT_REQUESTS)
    worker_count = min(
        _bounded_int("concurrency", concurrency, 2, MAX_ENDPOINT_CONCURRENCY),
        request_count,
    )
    req = _require_requests()

    tasks = [prompt_list[i % len(prompt_list)] for i in range(request_count)]
    rows: list[dict] = []

    def _run(idx: int, prompt: str) -> dict:
        result = _send_completion(
            req,
            base_url,
            api_key,
            model,
            prompt,
            int(max_tokens),
            float(timeout_seconds),
        )
        return {"request_idx": idx, "prompt": prompt[:120], **result}

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_run, i, p): i for i, p in enumerate(tasks)}
        for f in as_completed(futures):
            rows.append(f.result())

    rows.sort(key=lambda r: r["request_idx"])

    latencies = [r["latency_ms"] for r in rows if r.get("success") and r.get("latency_ms")]
    errors = [r for r in rows if not r.get("success")]
    total_tokens = sum(r.get("total_tokens", 0) for r in rows)
    total_time_s = sum(r.get("latency_ms", 0) for r in rows) / 1000.0

    summary = {
        "n_requests": len(rows),
        "n_success": len(rows) - len(errors),
        "n_errors": len(errors),
        "error_rate": len(errors) / max(len(rows), 1),
        "total_tokens": total_tokens,
        "tokens_per_second": total_tokens / max(total_time_s, 0.001),
        "p50_latency_ms": _pct(latencies, 50) if latencies else None,
        "p95_latency_ms": _pct(latencies, 95) if latencies else None,
        "p99_latency_ms": _pct(latencies, 99) if latencies else None,
        "mean_latency_ms": statistics.mean(latencies) if latencies else None,
        "min_latency_ms": min(latencies) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
        "model": model,
        "base_url": base_url,
        "concurrency": worker_count,
    }

    dataset_ref = records_to_dataset(rows, name="benchmark-results.parquet")
    return {"main": summary, "rows": dataset_ref}


# ============================================================
# shadow_compare_endpoint
# ============================================================


@node(
    name="Shadow Compare Endpoints",
    id="shadow_compare_endpoint",
    category=ML_CATEGORY,
    icon="git-compare",
    description=(
        "Send the same prompts to a primary and a shadow endpoint and compare "
        "latency, success rate, and response content. Useful for A/B testing "
        "model versions before cutover."
    ),
    inputs=["input"],
    outputs=["main", "rows"],
    requirements=["requests>=2.28"],
    params={
        "primary_url": {
            "description": "Primary endpoint base URL (without /v1).",
        },
        "primary_model": {
            "description": "Primary model name.",
        },
        "primary_api_key": {
            "description": "Primary endpoint API key.",
        },
        "shadow_url": {
            "group": "Shadow Endpoint",
            "description": "Shadow endpoint base URL (without /v1).",
        },
        "shadow_model": {
            "group": "Shadow Endpoint",
            "description": "Shadow model name.",
        },
        "shadow_api_key": {
            "group": "Shadow Endpoint",
            "description": "Shadow endpoint API key.",
        },
        "prompts": {
            "description": "JSON array of test prompts (or use input DatasetRef).",
            "multiline": True,
        },
        "prompt_column": {
            "description": "Column name when input is a DatasetRef.",
        },
        "max_tokens": {
            "description": "Max tokens per response.",
        },
        "timeout_seconds": {
            "description": "Per-request timeout in seconds.",
        },
        "concurrency": {
            "description": "Concurrent request pairs.",
        },
    },
    param_groups={"Shadow Endpoint": []},
)
def shadow_compare_endpoint(
    input: Any = None,
    primary_url: str = "http://localhost:11434",
    primary_model: str = "llama3.2",
    primary_api_key: str = "",
    shadow_url: str = "http://localhost:11435",
    shadow_model: str = "llama3.1",
    shadow_api_key: str = "",
    prompts: str = '["What is RAG?", "Explain fine-tuning.", "What are embeddings?"]',
    prompt_column: str = "prompt",
    max_tokens: int = 128,
    timeout_seconds: float = 60,
    concurrency: int = 2,
) -> dict[str, Any]:
    """Send the same prompts to two endpoints and compare latency and responses."""
    if input is not None:
        try:
            records = _to_records(input)
            prompt_list = [str(r[prompt_column]) for r in records if prompt_column in r]
        except Exception:
            prompt_list = _parse_json(prompts, [])
    else:
        prompt_list = _parse_json(prompts, [])

    if not prompt_list:
        raise ValueError("No prompts found. Provide via 'prompts' param or input DatasetRef.")
    if len(prompt_list) > MAX_SHADOW_PROMPTS:
        raise ValueError(f"prompts must contain <= {MAX_SHADOW_PROMPTS} items")
    worker_count = min(
        _bounded_int("concurrency", concurrency, 2, MAX_ENDPOINT_CONCURRENCY),
        len(prompt_list),
    )
    req = _require_requests()

    comparison_rows: list[dict] = []

    def _compare_one(idx: int, prompt: str) -> dict:
        p = _send_completion(
            req,
            primary_url,
            primary_api_key,
            primary_model,
            prompt,
            int(max_tokens),
            float(timeout_seconds),
        )
        s = _send_completion(
            req,
            shadow_url,
            shadow_api_key,
            shadow_model,
            prompt,
            int(max_tokens),
            float(timeout_seconds),
        )
        latency_diff = (
            ((s.get("latency_ms") or 0) - (p.get("latency_ms") or 0))
            if p.get("latency_ms") and s.get("latency_ms")
            else None
        )
        return {
            "idx": idx,
            "prompt": prompt[:200],
            "primary_success": p.get("success", False),
            "primary_latency_ms": p.get("latency_ms"),
            "primary_response": p.get("content", "")[:500],
            "primary_tokens": p.get("total_tokens", 0),
            "shadow_success": s.get("success", False),
            "shadow_latency_ms": s.get("latency_ms"),
            "shadow_response": s.get("content", "")[:500],
            "shadow_tokens": s.get("total_tokens", 0),
            "latency_diff_ms": latency_diff,
        }

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_compare_one, i, p): i for i, p in enumerate(prompt_list)}
        for f in as_completed(futures):
            comparison_rows.append(f.result())

    comparison_rows.sort(key=lambda r: r["idx"])

    p_lat = [r["primary_latency_ms"] for r in comparison_rows if r["primary_latency_ms"]]
    s_lat = [r["shadow_latency_ms"] for r in comparison_rows if r["shadow_latency_ms"]]
    diffs = [r["latency_diff_ms"] for r in comparison_rows if r["latency_diff_ms"] is not None]

    summary = {
        "n_prompts": len(comparison_rows),
        "primary_model": primary_model,
        "primary_success_rate": sum(1 for r in comparison_rows if r["primary_success"])
        / max(len(comparison_rows), 1),
        "primary_p50_ms": _pct(p_lat, 50) if p_lat else None,
        "primary_p95_ms": _pct(p_lat, 95) if p_lat else None,
        "shadow_model": shadow_model,
        "shadow_success_rate": sum(1 for r in comparison_rows if r["shadow_success"])
        / max(len(comparison_rows), 1),
        "shadow_p50_ms": _pct(s_lat, 50) if s_lat else None,
        "shadow_p95_ms": _pct(s_lat, 95) if s_lat else None,
        "mean_latency_diff_ms": statistics.mean(diffs) if diffs else None,
        "shadow_faster_pct": sum(1 for d in diffs if d < 0) / max(len(diffs), 1) if diffs else None,
    }

    dataset_ref = records_to_dataset(comparison_rows, name="shadow-compare-results.parquet")
    return {"main": summary, "rows": dataset_ref}
