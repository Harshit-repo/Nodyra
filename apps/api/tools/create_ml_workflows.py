"""Script to create ML workflow demos for the new LLM training/eval nodes."""

from __future__ import annotations

import sys

import requests

API = "http://localhost:8000"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
GLOBAL_ENV = "4b75702411d74bd799371b0f587220a8"


def make_node(id, ntype, label=None, params=None, x=0, y=120, outputs_override=None):
    return {
        "id": id,
        "type": ntype,
        "label": label,
        "params": params or {},
        "position": {"x": x, "y": y},
        "disabled": False,
        "outputs_override": outputs_override,
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
        f"{API}/workflows/{wf_id}",
        headers=HEADERS,
        json={"environment_id": env_id, "graph": graph},
    )
    r2.raise_for_status()
    print(f"  Created {name!r}  id={wf_id}")
    return wf_id


# ============================================================
# Workflow 1: Deterministic Rule Eval
# Tests: code -> records_to_dataset -> llm_rule_eval -> eval_gate -> eval_report
# ============================================================

GENERATE_PREDS_CODE = (
    "import random\n"
    "random.seed(42)\n"
    "rows = []\n"
    "for i in range(30):\n"
    "    expected = str(i * 3)\n"
    "    output = expected if random.random() < 0.80 else str(i * 3 + 1)\n"
    '    rows.append({"prompt": f"What is {i} * 3?", "output": output, "expected": expected})\n'
    "output = rows\n"
)

FAIL_LOG_CODE = (
    'summary = input.get("input", {}).get("summary", {})\n'
    'accuracy = summary.get("accuracy", "?")\n'
    'print(f"EVAL GATE FAILED: accuracy={accuracy}")\n'
    'output = {"gate_failed": True, "accuracy": accuracy}\n'
)

wf1_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "gen",
            "code",
            label="Generate Sample Predictions",
            params={"code": GENERATE_PREDS_CODE},
            x=260,
            outputs_override=["main"],
        ),
        make_node("ds", "records_to_dataset", x=520),
        make_node(
            "eval",
            "llm_rule_eval",
            x=780,
            params={
                "mode": "exact_match",
                "output_column": "output",
                "expected_column": "expected",
                "case_sensitive": False,
                "strip_whitespace": True,
            },
        ),
        make_node(
            "gate",
            "eval_gate",
            x=1040,
            params={"metric": "accuracy", "operator": ">=", "threshold": 0.70, "on_fail": "branch"},
        ),
        make_node(
            "report",
            "eval_report",
            x=1300,
            y=60,
            params={"title": "Rule Eval Report", "include_sample_rows": True, "sample_rows": 10},
        ),
        make_node(
            "fail_log",
            "code",
            label="Log Gate Failure",
            params={"code": FAIL_LOG_CODE},
            x=1300,
            y=200,
            outputs_override=["main"],
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "gen", "input"),
        make_edge("e2", "gen", "main", "ds", "input"),
        make_edge("e3", "ds", "main", "eval", "input"),
        make_edge("e4", "eval", "main", "gate", "input"),
        make_edge("e5", "gate", "pass", "report", "input"),
        make_edge("e6", "gate", "fail", "fail_log", "input"),
    ],
}

# ============================================================
# Workflow 2: Fine-Tune Dataset Prep + Validation Gate
# Tests: code -> records_to_dataset -> llm_fine_tune_dataset -> code(metrics) -> eval_gate
# ============================================================

SAMPLE_TRAINING_CODE = (
    "rows = []\n"
    "topics = [\n"
    '    ("What is supervised learning?", "Supervised learning trains models using labeled data."),\n'
    '    ("What is overfitting?", "Overfitting occurs when a model memorizes training data."),\n'
    '    ("What is gradient descent?", "Gradient descent minimizes loss by adjusting parameters."),\n'
    '    ("What is backpropagation?", "Backpropagation computes gradients for neural network training."),\n'
    '    ("What is a transformer?", "A transformer uses attention to process sequences in parallel."),\n'
    '    ("What is fine-tuning?", "Fine-tuning adapts a pre-trained model to a specific task."),\n'
    '    ("What is a token?", "A token is the smallest unit of text a language model processes."),\n'
    '    ("What is perplexity?", "Perplexity measures how well a model predicts a sample of text."),\n'
    '    ("What is RLHF?", "RLHF aligns models with human preferences using reinforcement learning."),\n'
    '    ("What is a LoRA adapter?", "A LoRA adapter fine-tunes large models using low-rank matrices."),\n'
    '    ("What is in-context learning?", "In-context learning lets models learn from prompt examples."),\n'
    '    ("What is RAG?", "RAG combines retrieval with generation for factual answers."),\n'
    "]\n"
    "for q, a in topics * 5:  # 60 examples\n"
    '    rows.append({"user": q, "assistant": a})\n'
    "output = rows\n"
)

VALIDATE_DATASET_CODE = (
    "ft_ds = input\n"
    "n = ft_ds.get('n_examples', 0)\n"
    "tok = ft_ds.get('token_estimate', 0)\n"
    "warnings = ft_ds.get('validation', {}).get('warnings', [])\n"
    "print(f'Fine-tune dataset ready: {n} examples, ~{tok} tokens')\n"
    "if warnings:\n"
    "    print(f'Warnings: {warnings}')\n"
    "output = {'accuracy': 1.0 if n >= 10 else 0.0, 'n_examples': n, 'token_estimate': tok}\n"
)

wf2_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "gen",
            "code",
            label="Generate Training Data",
            params={"code": SAMPLE_TRAINING_CODE},
            x=260,
            outputs_override=["main"],
        ),
        make_node("ds", "records_to_dataset", x=520),
        make_node(
            "ft_prep",
            "llm_fine_tune_dataset",
            x=780,
            params={
                "format": "openai_chat_jsonl",
                "user_column": "user",
                "assistant_column": "assistant",
                "min_examples": 10,
                "dedupe": True,
                "validation_split": 0.1,
                "max_tokens_per_example": 512,
            },
        ),
        make_node(
            "validate",
            "code",
            label="Validate Dataset",
            params={"code": VALIDATE_DATASET_CODE},
            x=1040,
            outputs_override=["main"],
        ),
        make_node(
            "gate",
            "eval_gate",
            x=1300,
            params={"metric": "accuracy", "operator": ">=", "threshold": 1.0, "on_fail": "branch"},
        ),
        make_node(
            "ready",
            "code",
            label="Dataset Ready",
            params={
                "code": "print('Dataset validated! Ready to upload to OpenAI.')\noutput = input"
            },
            x=1560,
            y=60,
            outputs_override=["main"],
        ),
        make_node(
            "insufficient",
            "code",
            label="Too Few Examples",
            params={
                "code": "n = input.get('input', {}).get('n_examples', 0)\nprint(f'Not enough: {n}')\noutput = input"
            },
            x=1560,
            y=200,
            outputs_override=["main"],
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "gen", "input"),
        make_edge("e2", "gen", "main", "ds", "input"),
        make_edge("e3", "ds", "main", "ft_prep", "input"),
        make_edge("e4", "ft_prep", "main", "validate", "input"),
        make_edge("e5", "validate", "main", "gate", "input"),
        make_edge("e6", "gate", "pass", "ready", "input"),
        make_edge("e7", "gate", "fail", "insufficient", "input"),
    ],
}

# ============================================================
# Workflow 3: Full OpenAI Fine-Tune Pipeline
# Tests all Milestone A nodes end-to-end
# ============================================================

OPENAI_DATA_CODE = (
    "rows = [\n"
    '    {"user": "What is supervised learning?", "assistant": "Supervised learning trains models on labeled data."},\n'
    '    {"user": "What is overfitting?", "assistant": "Overfitting means the model memorizes training data."},\n'
    '    {"user": "What is gradient descent?", "assistant": "Gradient descent minimizes loss by adjusting weights."},\n'
    '    {"user": "What is a neural network?", "assistant": "A neural network is a graph of interconnected nodes."},\n'
    '    {"user": "What is backpropagation?", "assistant": "Backpropagation computes gradients layer by layer."},\n'
    '    {"user": "What is a transformer?", "assistant": "A transformer uses attention mechanisms for sequence tasks."},\n'
    '    {"user": "What is fine-tuning?", "assistant": "Fine-tuning adapts a pre-trained model to a new task."},\n'
    '    {"user": "What is a token?", "assistant": "A token is the unit of text used by language models."},\n'
    '    {"user": "What is RLHF?", "assistant": "RLHF aligns models with human feedback via reinforcement learning."},\n'
    '    {"user": "What is a LoRA adapter?", "assistant": "A LoRA adapter trains low-rank weight matrices for efficiency."},\n'
    '    {"user": "What is RAG?", "assistant": "RAG retrieves relevant documents and uses them in generation."},\n'
    '    {"user": "What is in-context learning?", "assistant": "In-context learning uses prompt examples to guide output."},\n'
    "]\n"
    "output = rows\n"
)

wf3_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "gen",
            "code",
            label="Generate Training Examples",
            params={"code": OPENAI_DATA_CODE},
            x=260,
            outputs_override=["main"],
        ),
        make_node("ds", "records_to_dataset", x=520),
        make_node(
            "ft_prep",
            "llm_fine_tune_dataset",
            x=780,
            params={
                "format": "openai_chat_jsonl",
                "user_column": "user",
                "assistant_column": "assistant",
                "min_examples": 10,
                "dedupe": True,
                "validation_split": 0.0,
            },
        ),
        make_node(
            "upload",
            "openai_upload_fine_tune_file",
            x=1060,
            params={"openai_api_key": "", "purpose": "fine-tune"},
        ),
        make_node(
            "create_job",
            "openai_create_fine_tune_job",
            x=1340,
            params={
                "openai_api_key": "",
                "model": "gpt-4.1-mini",
                "suffix": "noodle-test",
            },
        ),
        make_node(
            "status",
            "openai_fine_tune_status",
            x=1620,
            params={
                "openai_api_key": "",
                "include_events": True,
                "include_result_files": True,
                "fail_if_failed": True,
            },
        ),
        make_node(
            "register",
            "register_fine_tuned_model",
            x=1900,
            params={
                "name": "Noodle Test Fine-Tune",
                "task": "chat",
                "description": "Test fine-tuned model from Noodle workflow",
                "tags": "test, noodle, gpt-4.1-mini",
            },
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "gen", "input"),
        make_edge("e2", "gen", "main", "ds", "input"),
        make_edge("e3", "ds", "main", "ft_prep", "input"),
        make_edge("e4", "ft_prep", "main", "upload", "input"),
        make_edge("e5", "upload", "main", "create_job", "input"),
        make_edge("e6", "create_job", "main", "status", "input"),
        make_edge("e7", "status", "succeeded", "register", "input"),
    ],
}

if __name__ == "__main__":
    if not TOKEN:
        print("Usage: python create_ml_workflows.py <token>")
        sys.exit(1)

    print("Creating ML workflow demos...")
    wf1_id = create_workflow("LLM Eval — Deterministic Rule Check", wf1_graph)
    wf2_id = create_workflow("LLM Fine-Tune Dataset Prep + Validation", wf2_graph)
    wf3_id = create_workflow("OpenAI Fine-Tune Pipeline", wf3_graph)

    print("\nAll workflows created:")
    print(f"  WF1 (Rule Eval):      {wf1_id}")
    print(f"  WF2 (Dataset Prep):   {wf2_id}")
    print(f"  WF3 (Full Pipeline):  {wf3_id}")
