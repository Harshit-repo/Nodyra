"""Model monitoring, observability, and lifecycle management nodes for Nodyra."""

from __future__ import annotations

import json
import re
import statistics
from typing import Any

from nodyra.artifacts import write_text
from nodyra.datasets import is_dataset_ref
from nodyra.sdk import node
from nodyra_nodes.datasets import materialize_dataset, records_to_dataset

ML_CATEGORY = "Machine Learning"
MAX_LLM_MONITOR_SAMPLES = 1_000


def _to_records(val: Any) -> list[dict]:
    if is_dataset_ref(val):
        return materialize_dataset(val, cap=100_000, allow_truncate=True)
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


def _is_registry_ref(val: Any) -> bool:
    return isinstance(val, dict) and val.get("__nodyra_model_registry__") is True


def _require_numpy():
    try:
        import numpy as np

        return np
    except ImportError as exc:
        raise RuntimeError(
            "This node requires numpy. Add numpy to your workflow environment."
        ) from exc


def _openai_client(api_key: str):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "llm_judge mode requires openai. Add it to your workflow environment."
        ) from exc
    if not api_key:
        raise ValueError("openai_api_key is required for llm_judge mode.")
    return OpenAI(api_key=api_key)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(int(len(s) * p / 100), len(s) - 1)]


# ============================================================
# cost_budget_gate
# ============================================================


@node(
    name="Cost Budget Gate",
    id="cost_budget_gate",
    category=ML_CATEGORY,
    icon="dollar-sign",
    description=(
        "Branch based on whether token or dollar cost is within budget. "
        "Routes to 'pass' if under budget, 'fail' if over. "
        "Can auto-compute cost from token counts when price_per_1k_tokens is set."
    ),
    inputs=["input"],
    outputs=["pass", "fail"],
    params={
        "budget_usd": {
            "description": "Budget threshold in USD.",
        },
        "metric_field": {
            "description": "Field in the input dict containing the cost value.",
        },
        "operator": {
            "choices": ["<=", "<", ">=", ">", "=="],
            "description": "Comparison operator for passing condition.",
        },
        "price_per_1k_tokens": {
            "description": (
                "Auto-compute cost from total_tokens at this price per 1K tokens (0 = disabled)."
            ),
        },
    },
)
def cost_budget_gate(
    input: Any,
    budget_usd: float = 10.0,
    metric_field: str = "total_cost_usd",
    operator: str = "<=",
    price_per_1k_tokens: float = 0.0,
) -> dict[str, Any]:
    """Branch based on whether cost is within budget."""
    if not isinstance(input, dict):
        raise ValueError(f"Expected a dict input with cost fields, got {type(input).__name__}.")

    cost = input.get(metric_field)

    if cost is None and float(price_per_1k_tokens) > 0:
        tokens = input.get("total_tokens", 0) or 0
        cost = float(tokens) * float(price_per_1k_tokens) / 1000.0

    if cost is None:
        raise ValueError(
            f"Field '{metric_field}' not found in input and price_per_1k_tokens not set. "
            f"Available fields: {list(input.keys())}"
        )

    cost = float(cost)
    threshold = float(budget_usd)
    ops = {
        "<=": cost <= threshold,
        "<": cost < threshold,
        ">=": cost >= threshold,
        ">": cost > threshold,
        "==": cost == threshold,
    }
    passed = ops.get(operator, cost <= threshold)

    result = {
        "metric_field": metric_field,
        "cost_usd": cost,
        "budget_usd": threshold,
        "operator": operator,
        "passed": passed,
        "input": input,
    }
    return {"pass": result} if passed else {"fail": result}


# ============================================================
# latency_slo_gate
# ============================================================


@node(
    name="Latency SLO Gate",
    id="latency_slo_gate",
    category=ML_CATEGORY,
    icon="clock",
    description=(
        "Branch based on whether latency metrics meet an SLO threshold. "
        "Auto-detects p50/p95/p99/mean/max latency fields from the input dict. "
        "Routes to 'pass' if within SLO, 'fail' if breached."
    ),
    inputs=["input"],
    outputs=["pass", "fail"],
    params={
        "slo_ms": {
            "description": "Latency SLO threshold in milliseconds.",
        },
        "percentile": {
            "choices": ["p50", "p95", "p99", "mean", "max"],
            "description": "Which latency percentile to check.",
        },
        "metric_field": {
            "description": "Override field name (blank = auto-detect from percentile).",
        },
        "operator": {
            "choices": ["<=", "<"],
            "description": "Pass condition operator.",
        },
    },
)
def latency_slo_gate(
    input: Any,
    slo_ms: float = 2000.0,
    percentile: str = "p95",
    metric_field: str = "",
    operator: str = "<=",
) -> dict[str, Any]:
    """Branch based on whether latency metrics are within an SLO threshold."""
    if not isinstance(input, dict):
        raise ValueError(f"Expected a dict input with latency fields, got {type(input).__name__}.")

    if metric_field:
        field = metric_field
    else:
        p = (percentile or "p95").lower().strip()
        candidates = {
            "p50": ["p50_latency_ms", "p50_ms", "median_latency_ms"],
            "p95": ["p95_latency_ms", "p95_ms"],
            "p99": ["p99_latency_ms", "p99_ms"],
            "mean": ["mean_latency_ms", "avg_latency_ms"],
            "max": ["max_latency_ms"],
        }
        field_candidates = candidates.get(p, [f"{p}_latency_ms"])
        field = next((c for c in field_candidates if c in input), field_candidates[0])

    latency = input.get(field)
    if latency is None:
        numeric_fields = [
            k for k, v in input.items() if isinstance(v, (int, float)) and "latency" in k.lower()
        ]
        if numeric_fields:
            field = numeric_fields[0]
            latency = input[field]
        else:
            raise ValueError(
                f"Latency field '{field}' not found in input. "
                f"Available fields: {list(input.keys())}"
            )

    latency = float(latency)
    threshold = float(slo_ms)
    passed = latency <= threshold if operator in ("<=", "<") else latency >= threshold

    result = {
        "percentile": percentile,
        "latency_ms": latency,
        "slo_ms": threshold,
        "operator": operator,
        "passed": passed,
        "input": input,
    }
    return {"pass": result} if passed else {"fail": result}


# ============================================================
# response_quality_monitor
# ============================================================


@node(
    name="Response Quality Monitor",
    id="response_quality_monitor",
    category=ML_CATEGORY,
    icon="shield-check",
    description=(
        "Sample and score production model responses for quality issues. "
        "rule_check mode checks length, forbidden/required patterns, and JSON validity. "
        "llm_judge mode scores sampled responses using an LLM judge."
    ),
    inputs=["input"],
    outputs=["main", "rows"],
    params={
        "mode": {
            "choices": ["rule_check", "llm_judge"],
            "description": "Evaluation mode.",
        },
        "response_column": {
            "description": "Column containing the model response text.",
        },
        "prompt_column": {
            "description": "Column containing the prompt (used by llm_judge).",
        },
        "sample_rate": {
            "description": "Fraction of rows to evaluate (0.0–1.0).",
        },
        "max_length_chars": {
            "group": "Rule Checks",
            "description": "Max allowed response length in characters (0 = no limit).",
        },
        "min_length_chars": {
            "group": "Rule Checks",
            "description": "Min required response length in characters (0 = no limit).",
        },
        "forbidden_patterns": {
            "group": "Rule Checks",
            "description": "JSON array of forbidden regex patterns.",
        },
        "required_patterns": {
            "group": "Rule Checks",
            "description": "JSON array of required regex patterns (all must match).",
        },
        "check_json_valid": {
            "group": "Rule Checks",
            "description": "Require response to be valid JSON.",
        },
        "openai_api_key": {
            "group": "LLM Judge",
            "description": "OpenAI API key (llm_judge mode only).",
        },
        "judge_model": {
            "group": "LLM Judge",
            "description": "Model to use as judge.",
        },
        "judge_rubric": {
            "group": "LLM Judge",
            "description": "Rubric / scoring instructions for the judge.",
        },
        "score_threshold": {
            "group": "LLM Judge",
            "description": "Minimum acceptable score (1–5).",
        },
    },
    param_groups={"Rule Checks": [], "LLM Judge": []},
)
def response_quality_monitor(
    input: Any,
    mode: str = "rule_check",
    response_column: str = "response",
    prompt_column: str = "prompt",
    sample_rate: float = 1.0,
    max_length_chars: int = 0,
    min_length_chars: int = 0,
    forbidden_patterns: str = "[]",
    required_patterns: str = "[]",
    check_json_valid: bool = False,
    openai_api_key: str = "",
    judge_model: str = "gpt-4o-mini",
    judge_rubric: str = "Is this response helpful, accurate, and safe? Score 1-5.",
    score_threshold: float = 3.0,
) -> dict[str, Any]:
    """Sample and score production model responses for quality issues."""
    import math

    rows = _to_records(input)
    if not rows:
        raise ValueError("Input must be a non-empty DatasetRef or list of records.")
    if response_column not in rows[0]:
        raise ValueError(
            f"response_column '{response_column}' not found. Available: {list(rows[0].keys())}"
        )

    n_sample = max(1, math.ceil(len(rows) * min(1.0, max(0.0, float(sample_rate)))))
    sample = rows[:n_sample]

    forbidden = [re.compile(p, re.IGNORECASE) for p in _parse_json(forbidden_patterns, [])]
    required = [re.compile(p, re.IGNORECASE) for p in _parse_json(required_patterns, [])]

    result_rows: list[dict] = []

    if mode == "rule_check":
        for row in sample:
            resp = str(row.get(response_column, ""))
            issues: list[str] = []
            if int(max_length_chars) > 0 and len(resp) > int(max_length_chars):
                issues.append(f"too_long ({len(resp)} > {max_length_chars})")
            if int(min_length_chars) > 0 and len(resp) < int(min_length_chars):
                issues.append(f"too_short ({len(resp)} < {min_length_chars})")
            for pat in forbidden:
                if pat.search(resp):
                    issues.append(f"forbidden:{pat.pattern[:40]}")
            for pat in required:
                if not pat.search(resp):
                    issues.append(f"missing_required:{pat.pattern[:40]}")
            if check_json_valid:
                try:
                    json.loads(resp)
                except json.JSONDecodeError:
                    issues.append("invalid_json")
            result_rows.append(
                {
                    **row,
                    "_quality_pass": len(issues) == 0,
                    "_quality_issues": "; ".join(issues) if issues else "",
                    "_quality_mode": "rule_check",
                }
            )

    elif mode == "llm_judge":
        if len(sample) > MAX_LLM_MONITOR_SAMPLES:
            raise ValueError(
                "response_quality_monitor: sampled row count "
                f"{len(sample)} exceeds cap {MAX_LLM_MONITOR_SAMPLES}"
            )
        from concurrent.futures import ThreadPoolExecutor, as_completed

        client = _openai_client(openai_api_key)

        def _judge_one(row: dict) -> dict:
            resp = str(row.get(response_column, ""))
            prompt_text = str(row.get(prompt_column, "")) if prompt_column in row else ""
            system = (
                f"You are a quality judge. {judge_rubric}\n"
                'Reply with JSON: {"score": <1-5>, "issues": "<brief note or empty>"}'
            )
            user_msg = f"Prompt: {prompt_text[:500]}\nResponse: {resp[:1000]}"
            try:
                r = client.chat.completions.create(
                    model=judge_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    max_tokens=100,
                    temperature=0,
                )
                raw = r.choices[0].message.content or "{}"
                data = json.loads(raw.strip())
                score = float(data.get("score", 0))
                issues = data.get("issues", "")
            except Exception as e:
                score = 0.0
                issues = f"judge_error: {str(e)[:100]}"
            return {
                **row,
                "_quality_score": score,
                "_quality_pass": score >= float(score_threshold),
                "_quality_issues": issues,
                "_quality_mode": "llm_judge",
            }

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_judge_one, r) for r in sample]
            result_rows = [f.result() for f in as_completed(futures)]
    else:
        raise ValueError(f"Unknown mode '{mode}'. Choose rule_check or llm_judge.")

    n_pass = sum(1 for r in result_rows if r.get("_quality_pass"))
    scores = [r["_quality_score"] for r in result_rows if "_quality_score" in r]

    summary: dict = {
        "n_sampled": len(result_rows),
        "n_total": len(rows),
        "sample_rate": float(sample_rate),
        "mode": mode,
        "n_pass": n_pass,
        "n_fail": len(result_rows) - n_pass,
        "pass_rate": n_pass / max(len(result_rows), 1),
    }
    if scores:
        summary["mean_score"] = statistics.mean(scores)
        summary["min_score"] = min(scores)

    dataset_ref = records_to_dataset(result_rows, name="quality-monitor-results.parquet")
    return {"main": summary, "rows": dataset_ref}


# ============================================================
# prompt_drift_monitor
# ============================================================


@node(
    name="Prompt Drift Monitor",
    id="prompt_drift_monitor",
    category=ML_CATEGORY,
    icon="trending-up",
    description=(
        "Detect distribution shift in prompts compared to a baseline dataset. "
        "Uses vocabulary overlap, length distribution, and character n-gram statistics. "
        "Routes to 'pass'/'fail' when a baseline is provided, 'main' otherwise."
    ),
    inputs=["input", "baseline"],
    outputs=["main", "pass", "fail"],
    requirements=["numpy>=1.24"],
    params={
        "text_column": {
            "description": "Column containing the prompt/text to analyze.",
        },
        "drift_threshold": {
            "description": "Drift score threshold (0–1). Fail if drift_score exceeds this.",
        },
        "min_baseline_rows": {
            "description": "Minimum rows required in the baseline dataset.",
        },
        "fail_on_drift": {
            "description": "Route to fail output if drift is detected.",
        },
    },
)
def prompt_drift_monitor(
    input: Any,
    baseline: Any = None,
    text_column: str = "prompt",
    drift_threshold: float = 0.3,
    min_baseline_rows: int = 10,
    fail_on_drift: bool = True,
) -> dict[str, Any]:
    """Detect distribution shift in prompts compared to a baseline dataset."""
    np = _require_numpy()

    current_rows = _to_records(input)
    if not current_rows:
        raise ValueError("input must be a non-empty DatasetRef or list of records.")
    if text_column not in current_rows[0]:
        raise ValueError(
            f"text_column '{text_column}' not found. Available: {list(current_rows[0].keys())}"
        )

    current_texts = [str(r.get(text_column, "")) for r in current_rows]

    has_baseline = baseline is not None
    baseline_texts: list[str] = []
    if has_baseline:
        baseline_rows = _to_records(baseline)
        if len(baseline_rows) < int(min_baseline_rows):
            raise ValueError(
                f"Baseline has {len(baseline_rows)} rows; need at least {min_baseline_rows}."
            )
        baseline_texts = [str(r.get(text_column, "")) for r in baseline_rows]

    def _text_stats(texts: list[str]) -> dict:
        if not texts:
            return {}
        lengths = np.array([len(t) for t in texts])
        words_per = np.array([len(t.split()) for t in texts])
        vocab = set(w.lower() for t in texts for w in t.split() if w.isalpha())
        bigrams: dict[str, int] = {}
        for t in texts:
            for i in range(len(t) - 1):
                bg = t[i : i + 2].lower()
                bigrams[bg] = bigrams.get(bg, 0) + 1
        top_bigrams = dict(sorted(bigrams.items(), key=lambda x: -x[1])[:50])
        return {
            "n": len(texts),
            "mean_length": float(np.mean(lengths)),
            "std_length": float(np.std(lengths)),
            "p50_length": float(np.percentile(lengths, 50)),
            "p95_length": float(np.percentile(lengths, 95)),
            "mean_words": float(np.mean(words_per)),
            "vocab_size": len(vocab),
            "vocab": vocab,
            "bigram_dist": top_bigrams,
        }

    current_stats = _text_stats(current_texts)

    drift_score = 0.0
    drift_signals: list[str] = []
    length_drift = vocab_drift = bigram_drift = 0.0

    if has_baseline:
        baseline_stats = _text_stats(baseline_texts)

        length_drift = abs(current_stats["mean_length"] - baseline_stats["mean_length"]) / max(
            baseline_stats["mean_length"], 1.0
        )

        curr_vocab = current_stats["vocab"]
        base_vocab = baseline_stats["vocab"]
        intersection = len(curr_vocab & base_vocab)
        union = len(curr_vocab | base_vocab)
        vocab_jaccard = intersection / max(union, 1)
        vocab_drift = 1.0 - vocab_jaccard

        all_bigrams = set(list(current_stats["bigram_dist"]) + list(baseline_stats["bigram_dist"]))
        curr_vec = np.array(
            [current_stats["bigram_dist"].get(b, 0) for b in all_bigrams],
            dtype=float,
        )
        base_vec = np.array(
            [baseline_stats["bigram_dist"].get(b, 0) for b in all_bigrams],
            dtype=float,
        )
        curr_norm = float(np.linalg.norm(curr_vec))
        base_norm = float(np.linalg.norm(base_vec))
        if curr_norm > 0 and base_norm > 0:
            cosine_sim = float(np.dot(curr_vec, base_vec) / (curr_norm * base_norm))
            bigram_drift = 1.0 - max(0.0, cosine_sim)
        else:
            bigram_drift = 0.0

        drift_score = float(np.mean([length_drift, vocab_drift, bigram_drift]))

        if length_drift > 0.2:
            drift_signals.append(f"length_shift (delta={length_drift:.2f})")
        if vocab_drift > 0.3:
            drift_signals.append(f"vocab_drift (jaccard={vocab_jaccard:.2f})")
        if bigram_drift > 0.2:
            drift_signals.append(f"bigram_drift ({bigram_drift:.2f})")

    report_lines = [
        "# Prompt Drift Monitor Report",
        f"\n## Current Batch ({current_stats['n']} prompts)",
        f"- Mean length: {current_stats['mean_length']:.0f} chars",
        f"- P95 length: {current_stats['p95_length']:.0f} chars",
        f"- Vocabulary size: {current_stats['vocab_size']}",
        f"- Mean words: {current_stats['mean_words']:.1f}",
    ]
    if has_baseline:
        report_lines += [
            f"\n## Drift vs Baseline ({baseline_stats['n']} prompts)",
            f"- Drift score: {drift_score:.3f} (threshold={drift_threshold})",
            f"- Signals: {', '.join(drift_signals) if drift_signals else 'none'}",
            f"- Length drift: {length_drift:.3f}",
            f"- Vocab drift: {vocab_drift:.3f}",
            f"- Bigram drift: {bigram_drift:.3f}",
        ]

    report_ref = write_text("\n".join(report_lines), name="prompt-drift-report.md")

    result = {
        "drift_score": drift_score,
        "drift_threshold": float(drift_threshold),
        "drift_detected": drift_score > float(drift_threshold),
        "drift_signals": drift_signals,
        "has_baseline": has_baseline,
        "current_n": current_stats["n"],
        "current_mean_length": current_stats["mean_length"],
        "report": report_ref,
    }

    if has_baseline and fail_on_drift and drift_score > float(drift_threshold):
        return {"fail": result}
    return {"pass": result} if has_baseline else {"main": result}


# ============================================================
# model_registry_query
# ============================================================


@node(
    name="Model Registry Query",
    id="model_registry_query",
    category=ML_CATEGORY,
    icon="database",
    description=(
        "Query and filter model registry entries from a DatasetRef or list of records. "
        "Returns a summary and a filtered DatasetRef of matching entries."
    ),
    inputs=["input"],
    outputs=["main", "rows"],
    params={
        "filter_status": {
            "choices": ["", "draft", "candidate", "staging", "production", "archived"],
            "description": "Filter by status (blank = all).",
        },
        "filter_provider": {
            "description": "Filter by provider name substring (blank = all).",
        },
        "filter_base_model": {
            "description": "Filter by base model substring (blank = all).",
        },
        "sort_by": {
            "choices": ["model_id", "status", "provider", "base_model"],
            "description": "Sort field.",
        },
        "limit": {
            "description": "Maximum number of results to return.",
        },
    },
)
def model_registry_query(
    input: Any,
    filter_status: str = "",
    filter_provider: str = "",
    filter_base_model: str = "",
    sort_by: str = "model_id",
    limit: int = 100,
) -> dict[str, Any]:
    """Query and filter model registry entries."""
    records = _to_records(input)
    if not records or not any(records):
        raise ValueError(
            "input must be a non-empty DatasetRef or list of registry entries — "
            "each row needs at least one column (got an empty object)."
        )

    results = list(records)

    if filter_status:
        results = [r for r in results if r.get("status", "").lower() == filter_status.lower()]
    if filter_provider:
        results = [r for r in results if filter_provider.lower() in r.get("provider", "").lower()]
    if filter_base_model:
        results = [
            r for r in results if filter_base_model.lower() in r.get("base_model", "").lower()
        ]

    results = sorted(results, key=lambda r: str(r.get(sort_by, "")))
    results = results[: int(limit)]

    status_counts: dict[str, int] = {}
    for r in records:
        s = r.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    dataset_ref = records_to_dataset(results, name="registry-results.parquet") if results else None

    return {
        "main": {
            "total_registered": len(records),
            "total_matched": len(results),
            "filter_status": filter_status or None,
            "filter_provider": filter_provider or None,
            "status_counts": status_counts,
        },
        "rows": dataset_ref,
    }


# ============================================================
# model_promote
# ============================================================


@node(
    name="Promote Model",
    id="model_promote",
    category=ML_CATEGORY,
    icon="arrow-up-circle",
    description=(
        "Promote a model registry entry to staging or production status. "
        "Accepts a ModelRegistryRef dict or any dict with a model_id field. "
        "Optionally requires eval evidence before promoting."
    ),
    inputs=["input", "registry"],
    outputs=["main"],
    params={
        "target_status": {
            "choices": ["staging", "production"],
            "description": "Status to promote the model to.",
        },
        "model_id": {
            "description": "Model ID to promote (blank = use input model_id field).",
        },
        "notes": {
            "description": "Promotion rationale (stored on the registry entry).",
        },
        "require_eval_result": {
            "description": "Require metrics or eval evidence in input before promoting.",
        },
    },
)
def model_promote(
    input: Any,
    registry: Any = None,
    target_status: str = "production",
    model_id: str = "",
    notes: str = "",
    require_eval_result: bool = False,
) -> dict[str, Any]:
    """Promote a model entry to staging or production status."""
    valid_statuses = {"draft", "candidate", "staging", "production", "archived"}
    if target_status not in valid_statuses:
        raise ValueError(f"target_status must be one of {sorted(valid_statuses)}.")

    entry = dict(input) if isinstance(input, dict) else {}

    resolved_id = (model_id or "").strip() or entry.get("model_id", "")
    if not resolved_id:
        raise ValueError("model_id is required. Set it in params or pass a dict with model_id.")

    if require_eval_result:
        has_eval = entry.get("__nodyra_eval_result__") is True or bool(entry.get("metrics"))
        if not has_eval:
            raise ValueError(
                "require_eval_result=True but no EvalResultRef or metrics found in input. "
                "Connect an eval_report or llm_rule_eval output before promoting."
            )

    previous_status = entry.get("status", "candidate")
    promoted_entry = {
        **entry,
        "__nodyra_model_registry__": True,
        "version": entry.get("version", 1),
        "model_id": resolved_id,
        "provider": entry.get("provider", ""),
        "base_model": entry.get("base_model", ""),
        "status": target_status,
        "previous_status": previous_status,
        "promotion_notes": notes,
        "metrics": entry.get("metrics", {}),
        "metadata": entry.get("metadata", {}),
    }

    return {
        "model_id": resolved_id,
        "previous_status": previous_status,
        "new_status": target_status,
        "notes": notes,
        "registry_entry": promoted_entry,
    }


# ============================================================
# model_rollback
# ============================================================


@node(
    name="Rollback Model",
    id="model_rollback",
    category=ML_CATEGORY,
    icon="rotate-ccw",
    description=(
        "Revert a model's status, removing it from production or staging. "
        "Accepts a ModelRegistryRef dict or any dict with a model_id field. "
        "Optionally looks up a previous version from a version_history DatasetRef."
    ),
    inputs=["input", "version_history"],
    outputs=["main"],
    params={
        "rollback_to_status": {
            "choices": ["candidate", "staging", "draft", "archived"],
            "description": "Status to revert the model to.",
        },
        "model_id": {
            "description": "Model ID to rollback (blank = use input model_id field).",
        },
        "reason": {
            "description": "Rollback reason (stored on the registry entry for audit).",
        },
    },
)
def model_rollback(
    input: Any,
    version_history: Any = None,
    rollback_to_status: str = "candidate",
    model_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Revert a model's status, removing it from production or staging."""
    valid_rollback = {"draft", "candidate", "staging", "archived"}
    if rollback_to_status not in valid_rollback:
        raise ValueError(
            f"rollback_to_status must be one of {sorted(valid_rollback)}. "
            "To re-promote, use the Promote Model node."
        )

    entry = dict(input) if isinstance(input, dict) else {}
    resolved_id = (model_id or "").strip() or entry.get("model_id", "")
    if not resolved_id:
        raise ValueError("model_id is required. Set it in params or pass a dict with model_id.")

    current_status = entry.get("status", "production")

    previous_version = None
    if version_history is not None:
        try:
            history = _to_records(version_history)
            candidates = [
                r
                for r in history
                if r.get("model_id") == resolved_id and r.get("status") != "production"
            ]
            if candidates:
                previous_version = candidates[-1]
        except Exception:
            pass

    rolled_back_entry = {
        **entry,
        "__nodyra_model_registry__": True,
        "model_id": resolved_id,
        "status": rollback_to_status,
        "previous_status": current_status,
        "rollback_reason": reason,
    }

    return {
        "model_id": resolved_id,
        "rolled_back_from": current_status,
        "rolled_back_to": rollback_to_status,
        "reason": reason,
        "previous_version": previous_version,
        "registry_entry": rolled_back_entry,
    }
