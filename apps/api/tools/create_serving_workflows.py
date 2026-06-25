"""Create model serving and monitoring workflow demos."""

from __future__ import annotations

import sys

import requests

API = "http://localhost:8000"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
GLOBAL_ENV = "4b75702411d74bd799371b0f587220a8"


def make_node(nid, ntype, label=None, params=None, x=0, y=120):
    return {
        "id": nid,
        "type": ntype,
        "label": label,
        "params": params or {},
        "position": {"x": x, "y": y},
        "disabled": False,
        "outputs_override": None,
        "on_error": "stop",
        "retry_on_fail": False,
        "retries": 1,
        "retry_wait_seconds": 0,
        "retry_backoff": False,
        "always_output_data": False,
        "timeout_seconds": None,
        "tool_mode": False,
        "tool_name": None,
        "tool_description": "",
    }


def make_edge(eid, src, src_out, tgt, tgt_in):
    return {
        "id": eid,
        "source": src,
        "source_output": src_out,
        "target": tgt,
        "target_input": tgt_in,
    }


def create_workflow(name, graph, env_id=GLOBAL_ENV):
    r = requests.post(f"{API}/workflows", headers=HEADERS, json={"name": name})
    r.raise_for_status()
    wf_id = r.json()["id"]
    r2 = requests.patch(
        f"{API}/workflows/{wf_id}", headers=HEADERS, json={"environment_id": env_id, "graph": graph}
    )
    r2.raise_for_status()
    print(f"  Created {name!r}  id={wf_id}")
    return wf_id


# ============================================================
# Workflow 8: Model Serving Pipeline
# Deployment spec + endpoint probe (graceful offline handling)
# ============================================================

PROBE_SUMMARY_CODE = """
probe = input
is_healthy = probe.get('is_healthy', False)
latency = probe.get('completion_latency_ms')
models = probe.get('models', [])
print(f'Endpoint health: {is_healthy}')
print(f'Available models: {models}')
if latency:
    print(f'Completion latency: {latency:.0f}ms')
output = probe
"""

wf8_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "spec",
            "model_deployment_spec",
            x=240,
            params={
                "runtime": "ollama",
                "model": "llama3.2",
                "port": 11434,
                "format": "docker-compose",
                "gpu": False,
                "context_length": 4096,
            },
        ),
        make_node(
            "probe",
            "model_endpoint_probe",
            x=560,
            params={
                "base_url": "http://localhost:11434",
                "model": "llama3.2",
                "api_key": "",
                "test_prompt": "Hello",
                "max_tokens": 8,
                "timeout_seconds": 5,
                "check_models_list": True,
                "check_completion": True,
                "fail_if_unhealthy": False,
            },
        ),
        make_node(
            "healthy", "code", label="Log Healthy", params={"code": PROBE_SUMMARY_CODE}, x=840, y=60
        ),
        make_node(
            "offline",
            "code",
            label="Log Offline",
            params={"code": "print('Endpoint offline or unhealthy')\noutput = input"},
            x=840,
            y=200,
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "spec", "input"),
        make_edge("e2", "trig", "main", "probe", "input"),
        make_edge("e3", "probe", "pass", "healthy", "input"),
        make_edge("e4", "probe", "fail", "offline", "input"),
        make_edge("e5", "probe", "main", "offline", "input"),
    ],
}


# ============================================================
# Workflow 9: Response Quality and Cost Monitoring
# Simulates LLM responses, runs quality + cost checks
# ============================================================

SIMULATE_RESPONSES_CODE = """
import random
responses = [
    {"prompt": "What is machine learning?", "response": "Machine learning trains models from data to make predictions.", "total_tokens": 120, "latency_ms": 450},
    {"prompt": "Explain neural networks.", "response": "Neural networks are computational models inspired by the brain with layers of nodes.", "total_tokens": 180, "latency_ms": 520},
    {"prompt": "What is overfitting?", "response": "I don't know.", "total_tokens": 20, "latency_ms": 200},
    {"prompt": "Describe transformers.", "response": "Transformers use attention mechanisms to process sequences in parallel. They are the basis of GPT, BERT, and other modern models.", "total_tokens": 280, "latency_ms": 680},
    {"prompt": "What is RAG?", "response": "RAG retrieval augmented generation combines retrieval with language model generation to provide factual answers grounded in external documents.", "total_tokens": 320, "latency_ms": 750},
]
output = responses
"""

COST_SUMMARY_CODE = """
data = input
cost_usd = data.get('cost_usd', 0)
budget = data.get('budget_usd', 0)
n = input.get('input', {}).get('total_tokens', 0)
print(f'Cost gate PASSED: ${cost_usd:.4f} <= ${budget:.2f} budget')
output = data
"""

LATENCY_SUMMARY_CODE = """
data = input
latency = data.get('latency_ms', 0)
slo = data.get('slo_ms', 0)
print(f'Latency gate PASSED: {latency:.0f}ms <= {slo:.0f}ms SLO')
output = data
"""

COST_FAIL_CODE = """
data = input
cost_usd = data.get('cost_usd', 0)
budget = data.get('budget_usd', 0)
print(f'Cost gate FAILED: ${cost_usd:.4f} > ${budget:.2f} budget')
output = data
"""

QUALITY_SUMMARY_CODE = """
summary = input
pass_rate = summary.get('pass_rate', 0)
n_fail = summary.get('n_fail', 0)
print(f'Quality monitor: pass_rate={pass_rate:.0%}, {n_fail} issues found')
output = summary
"""

LATENCY_STATS_CODE = """
rows = input
if isinstance(rows, list):
    latencies = [r.get('latency_ms', 0) for r in rows if isinstance(r, dict)]
    if latencies:
        p95 = sorted(latencies)[int(len(latencies)*0.95)]
        avg = sum(latencies) / len(latencies)
        print(f'Response latency stats: avg={avg:.0f}ms, p95={p95:.0f}ms')
output = {"p95_latency_ms": sorted(latencies)[int(len(latencies)*0.95)] if isinstance(rows, list) and rows else 0, "mean_latency_ms": sum([r.get("latency_ms",0) for r in rows if isinstance(r, dict)]) / max(len(rows),1) if isinstance(rows, list) else 0, "total_tokens": sum([r.get("total_tokens",0) for r in rows if isinstance(r, dict)]), "total_cost_usd": sum([r.get("total_tokens",0) for r in rows if isinstance(r, dict)]) * 0.002 / 1000}
"""

wf9_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "sim",
            "code",
            label="Simulate Responses",
            params={"code": SIMULATE_RESPONSES_CODE},
            x=240,
        ),
        make_node("ds", "records_to_dataset", x=480),
        make_node(
            "quality",
            "response_quality_monitor",
            x=720,
            params={
                "mode": "rule_check",
                "response_column": "response",
                "min_length_chars": 20,
                "forbidden_patterns": '["I don\'t know", "N/A"]',
                "sample_rate": 1.0,
            },
        ),
        make_node(
            "quality_log",
            "code",
            label="Log Quality Results",
            params={"code": QUALITY_SUMMARY_CODE},
            x=960,
        ),
        make_node(
            "latency_stats",
            "code",
            label="Compute Latency Stats",
            params={"code": LATENCY_STATS_CODE},
            x=1200,
        ),
        make_node(
            "cost_gate",
            "cost_budget_gate",
            x=1440,
            params={
                "budget_usd": 0.01,
                "metric_field": "total_cost_usd",
                "operator": "<=",
                "price_per_1k_tokens": 0.0,
            },
        ),
        make_node(
            "latency_gate",
            "latency_slo_gate",
            x=1680,
            params={
                "slo_ms": 1000.0,
                "percentile": "p95",
                "operator": "<=",
            },
        ),
        make_node(
            "done",
            "code",
            label="All Gates Passed",
            params={
                "code": "print('All quality, cost, and latency gates passed!')\noutput = input"
            },
            x=1920,
            y=60,
        ),
        make_node(
            "cost_fail",
            "code",
            label="Cost Budget Exceeded",
            params={"code": COST_FAIL_CODE},
            x=1680,
            y=240,
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "sim", "input"),
        make_edge("e2", "sim", "main", "ds", "input"),
        make_edge("e3", "ds", "main", "quality", "input"),
        make_edge("e4", "quality", "main", "quality_log", "input"),
        make_edge("e5", "quality", "rows", "latency_stats", "input"),
        make_edge("e6", "latency_stats", "main", "cost_gate", "input"),
        make_edge("e7", "cost_gate", "pass", "latency_gate", "input"),
        make_edge("e8", "cost_gate", "fail", "cost_fail", "input"),
        make_edge("e9", "latency_gate", "pass", "done", "input"),
        make_edge("e10", "latency_gate", "fail", "done", "input"),
    ],
}


# ============================================================
# Workflow 10: Model Lifecycle Management
# Registry query → promote → rollback demo
# ============================================================

REGISTRY_DATA_CODE = """
registry = [
    {"model_id": "ft-gpt4o-mini-v1", "provider": "openai", "base_model": "gpt-4o-mini", "status": "candidate", "metrics": {"accuracy": 0.91, "f1": 0.89}},
    {"model_id": "ft-gpt4o-mini-v2", "provider": "openai", "base_model": "gpt-4o-mini", "status": "candidate", "metrics": {"accuracy": 0.94, "f1": 0.92}},
    {"model_id": "lora-llama3-v1", "provider": "huggingface", "base_model": "meta-llama/Llama-3.1-8B", "status": "staging", "metrics": {"accuracy": 0.87}},
    {"model_id": "ft-gpt4o-mini-v0", "provider": "openai", "base_model": "gpt-4o-mini", "status": "archived", "metrics": {}},
]
output = registry
"""

PROMOTE_MODEL_CODE = """
# Select the best model from the registry for promotion
rows = input
if isinstance(rows, list) and rows:
    candidates = [r for r in rows if isinstance(r, dict) and r.get('status') == 'candidate']
    if candidates:
        best = max(candidates, key=lambda r: r.get('metrics', {}).get('accuracy', 0))
        print(f"Selected {best['model_id']} for promotion (accuracy={best.get('metrics',{}).get('accuracy',0):.2%})")
        output = best
    else:
        output = rows[0] if rows else {}
else:
    output = rows
"""

ROLLBACK_SETUP_CODE = """
# Simulate a production model that needs rollback
output = {"model_id": "ft-gpt4o-mini-v2", "status": "production", "provider": "openai"}
"""

LIFECYCLE_DONE_CODE = """
# Log final state
promoted = input.get('model_id', '?')
status = input.get('new_status', input.get('rolled_back_to', '?'))
print(f'Model lifecycle complete: {promoted} → {status}')
output = input
"""

wf10_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        # Registry data
        make_node(
            "reg_data", "code", label="Sample Registry", params={"code": REGISTRY_DATA_CODE}, x=240
        ),
        make_node("reg_ds", "records_to_dataset", x=480),
        # Query registry
        make_node(
            "query",
            "model_registry_query",
            x=720,
            params={
                "filter_status": "candidate",
                "filter_provider": "openai",
                "sort_by": "model_id",
                "limit": 10,
            },
        ),
        # Select and promote best
        make_node(
            "select", "code", label="Select Best Model", params={"code": PROMOTE_MODEL_CODE}, x=960
        ),
        make_node(
            "promote",
            "model_promote",
            x=1200,
            params={
                "target_status": "staging",
                "notes": "Automated promotion after eval gate passed",
                "require_eval_result": False,
            },
        ),
        make_node(
            "promote_log",
            "code",
            label="Log Promotion",
            params={"code": LIFECYCLE_DONE_CODE},
            x=1440,
        ),
        # Rollback demo branch (runs in parallel)
        make_node(
            "rollback_setup",
            "code",
            label="Setup Rollback Demo",
            params={"code": ROLLBACK_SETUP_CODE},
            x=960,
            y=240,
        ),
        make_node(
            "rollback",
            "model_rollback",
            x=1200,
            y=240,
            params={
                "rollback_to_status": "candidate",
                "reason": "Accuracy regression detected in production",
            },
        ),
        make_node(
            "rollback_log",
            "code",
            label="Log Rollback",
            params={
                "code": 'print(f\'Rolled back {input.get("model_id")} from {input.get("rolled_back_from")} to {input.get("rolled_back_to")}\')\noutput = input'
            },
            x=1440,
            y=240,
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "reg_data", "input"),
        make_edge("e2", "reg_data", "main", "reg_ds", "input"),
        make_edge("e3", "reg_ds", "main", "query", "input"),
        make_edge("e4", "query", "rows", "select", "input"),
        make_edge("e5", "select", "main", "promote", "input"),
        make_edge("e6", "promote", "main", "promote_log", "input"),
        make_edge("e7", "trig", "main", "rollback_setup", "input"),
        make_edge("e8", "rollback_setup", "main", "rollback", "input"),
        make_edge("e9", "rollback", "main", "rollback_log", "input"),
    ],
}


if __name__ == "__main__":
    if not TOKEN:
        print("Usage: python create_serving_workflows.py <token>")
        sys.exit(1)

    print("Creating model serving and monitoring workflow demos...")
    wf8_id = create_workflow("Model Serving Pipeline — Deployment Spec + Endpoint Probe", wf8_graph)
    wf9_id = create_workflow("Response Quality and Cost Monitoring", wf9_graph)
    wf10_id = create_workflow("Model Lifecycle — Registry, Promote, Rollback", wf10_graph)

    print("\nAll workflows created:")
    print(f"  WF8 (Serving Pipeline):  {wf8_id}")
    print(f"  WF9 (Quality Monitor):   {wf9_id}")
    print(f"  WF10 (Lifecycle Mgmt):   {wf10_id}")
