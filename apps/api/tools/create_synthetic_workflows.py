"""Create synthetic data and labeling workflow demos."""

from __future__ import annotations

import sys

import requests

API = "http://localhost:8000"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
GLOBAL_ENV = "4b75702411d74bd799371b0f587220a8"


def make_node(nid, ntype, label=None, params=None, x=0, y=120, outputs_override=None):
    return {
        "id": nid,
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
# Workflow 4: Weak Label Pipeline (rule-based, no LLM needed)
# Tests: code -> records_to_dataset -> weak_label -> llm_rule_eval -> eval_gate
# ============================================================

GENERATE_SUPPORT_TICKETS = """
tickets = [
    {"id": 1, "text": "I cannot log into my account and need a password reset"},
    {"id": 2, "text": "My invoice shows a wrong charge, please refund me"},
    {"id": 3, "text": "The app crashes every time I open it on iOS"},
    {"id": 4, "text": "How do I upgrade my plan to the premium tier?"},
    {"id": 5, "text": "I was charged twice for the same subscription"},
    {"id": 6, "text": "Feature request: add dark mode to the dashboard"},
    {"id": 7, "text": "Cannot connect to the API, getting 503 errors"},
    {"id": 8, "text": "I need to cancel my subscription immediately"},
    {"id": 9, "text": "The export to CSV button is broken and shows an error"},
    {"id": 10, "text": "When is the next planned maintenance window?"},
    {"id": 11, "text": "My payment failed but I was still charged"},
    {"id": 12, "text": "Login page is loading very slowly, almost timing out"},
    {"id": 13, "text": "Can I get an invoice for my last payment?"},
    {"id": 14, "text": "The webhook integration stopped working after the update"},
    {"id": 15, "text": "I cannot export my data, the download link is broken"},
]
output = tickets
"""

CHECK_LABELS_CODE = """
labeled = input
total = len(labeled.get('rows', {}).get('preview', [])) if isinstance(labeled, dict) and 'rows' in labeled else 0
distribution = labeled.get('label_distribution', {}) if isinstance(labeled, dict) else {}
print(f"Label distribution: {distribution}")
# We expect at least 3 categories with labels
n_categories = len([v for v in distribution.values() if v > 0])
output = {
    'accuracy': 1.0 if n_categories >= 3 else 0.0,
    'n_labeled': labeled.get('n_labeled', 0) if isinstance(labeled, dict) else 0,
    'distribution': distribution,
    'n_categories': n_categories,
}
"""

BILLING_BILLING_PATTERNS = '{"billing": ["invoice", "payment", "refund", "charge", "subscription", "cancel"], "technical": ["crash", "error", "bug", "api", "503", "broken", "slow", "export"], "account": ["login", "password", "log in", "log into", "account"], "other": []}'

wf4_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "gen",
            "code",
            label="Generate Support Tickets",
            params={"code": GENERATE_SUPPORT_TICKETS},
            x=260,
            outputs_override=["main"],
        ),
        make_node("ds", "records_to_dataset", x=520),
        make_node(
            "label",
            "weak_label",
            x=780,
            params={
                "mode": "rule_keyword_vote",
                "text_column": "text",
                "labels": "billing, technical, account, other",
                "output_column": "category",
                "rule_patterns": '{"billing": ["invoice", "payment", "refund", "charge", "subscription", "cancel"], "technical": ["crash", "error", "api", "503", "broken", "slow", "export", "webhook"], "account": ["login", "password", "log in", "account"]}',
                "default_label": "other",
            },
        ),
        make_node(
            "check",
            "code",
            label="Check Label Distribution",
            params={"code": CHECK_LABELS_CODE},
            x=1060,
            outputs_override=["main"],
        ),
        make_node(
            "gate",
            "eval_gate",
            x=1320,
            params={
                "metric": "n_categories",
                "operator": ">=",
                "threshold": 3,
                "on_fail": "branch",
            },
        ),
        make_node(
            "success",
            "code",
            label="Labels Ready",
            params={"code": "print(f'Labeling complete! {input}')\noutput = input"},
            x=1580,
            y=60,
            outputs_override=["main"],
        ),
        make_node(
            "fail",
            "code",
            label="Too Few Categories",
            params={
                "code": "print('WARNING: labeler only found ' + str(input.get('input', {}).get('n_categories', 0)) + ' categories')\noutput = input"
            },
            x=1580,
            y=200,
            outputs_override=["main"],
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "gen", "input"),
        make_edge("e2", "gen", "main", "ds", "input"),
        make_edge("e3", "ds", "main", "label", "input"),
        make_edge("e4", "label", "main", "check", "input"),
        make_edge("e5", "check", "main", "gate", "input"),
        make_edge("e6", "gate", "pass", "success", "input"),
        make_edge("e7", "gate", "fail", "fail", "input"),
    ],
}


# ============================================================
# Workflow 5: Synthetic Q&A + Token Profile + Fine-Tune Prep
# Uses: code -> records_to_dataset -> token_profile -> llm_fine_tune_dataset -> eval_gate
# No OpenAI key needed (token_profile uses tiktoken, ft_prep uses duckdb)
# ============================================================

SAMPLE_QA_CODE = """
# Synthetic ML Q&A pairs for fine-tuning prep demo
qa_pairs = [
    {"user": "What is supervised learning?", "assistant": "Supervised learning trains a model using labeled input-output pairs to predict new outputs."},
    {"user": "What is unsupervised learning?", "assistant": "Unsupervised learning finds patterns in data without labeled examples."},
    {"user": "What is gradient descent?", "assistant": "Gradient descent is an optimization algorithm that minimizes a loss function by adjusting parameters in the direction of steepest descent."},
    {"user": "What is overfitting?", "assistant": "Overfitting happens when a model learns the training data too well and fails to generalize to new data."},
    {"user": "What is a transformer?", "assistant": "A transformer is a neural network architecture that uses self-attention to process sequences in parallel."},
    {"user": "What is fine-tuning?", "assistant": "Fine-tuning adapts a pre-trained model to a specific task by continuing training on domain-specific data."},
    {"user": "What is a LoRA adapter?", "assistant": "LoRA adds small low-rank weight matrices to a frozen base model, enabling efficient fine-tuning with far fewer parameters."},
    {"user": "What is RLHF?", "assistant": "Reinforcement Learning from Human Feedback trains a reward model from human preferences and uses it to fine-tune an LLM via PPO."},
    {"user": "What is RAG?", "assistant": "Retrieval-Augmented Generation combines a retriever with a generator so the model can answer questions with up-to-date factual context."},
    {"user": "What is perplexity?", "assistant": "Perplexity measures how well a language model predicts a sample; lower perplexity means the model is more confident."},
    {"user": "What is tokenization?", "assistant": "Tokenization splits text into sub-word units (tokens) that a language model processes as input."},
    {"user": "What is beam search?", "assistant": "Beam search is a decoding strategy that explores the top-k most likely sequences at each step to find high-probability outputs."},
    {"user": "What is temperature in LLMs?", "assistant": "Temperature controls output randomness — lower values make outputs more deterministic, higher values make them more creative."},
    {"user": "What is a vector embedding?", "assistant": "A vector embedding is a dense numerical representation of text that captures semantic meaning in a high-dimensional space."},
    {"user": "What is a prompt template?", "assistant": "A prompt template is a structured format that guides how instructions, context, and examples are passed to a language model."},
]
output = qa_pairs
"""

CHECK_PREP_CODE = """
ft_ref = input
n = ft_ref.get('n_examples', 0) if isinstance(ft_ref, dict) else 0
token_est = ft_ref.get('token_estimate', 0) if isinstance(ft_ref, dict) else 0
print(f'Fine-tune dataset: {n} examples, ~{token_est} tokens')
output = {
    'accuracy': 1.0 if n >= 10 else 0.0,
    'n_examples': n,
    'token_estimate': token_est,
}
"""

wf5_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "gen",
            "code",
            label="Sample Q&A Dataset",
            params={"code": SAMPLE_QA_CODE},
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
            },
        ),
        make_node(
            "check",
            "code",
            label="Validate Dataset",
            params={"code": CHECK_PREP_CODE},
            x=1060,
            outputs_override=["main"],
        ),
        make_node(
            "gate",
            "eval_gate",
            x=1320,
            params={"metric": "n_examples", "operator": ">=", "threshold": 10, "on_fail": "branch"},
        ),
        make_node(
            "ready",
            "code",
            label="Ready to Upload",
            params={
                "code": "n = input.get('input', {}).get('n_examples', 0)\nprint(f'Dataset ready: {n} examples!')\noutput = input"
            },
            x=1580,
            y=60,
            outputs_override=["main"],
        ),
        make_node(
            "insufficient",
            "code",
            label="Insufficient Data",
            params={
                "code": "n = input.get('input', {}).get('n_examples', 0)\nprint(f'Not enough examples: {n}')\noutput = input"
            },
            x=1580,
            y=200,
            outputs_override=["main"],
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "gen", "input"),
        make_edge("e2", "gen", "main", "ds", "input"),
        make_edge("e3", "ds", "main", "ft_prep", "input"),
        make_edge("e4", "ft_prep", "main", "check", "input"),
        make_edge("e5", "check", "main", "gate", "input"),
        make_edge("e6", "gate", "pass", "ready", "input"),
        make_edge("e7", "gate", "fail", "insufficient", "input"),
    ],
}


if __name__ == "__main__":
    if not TOKEN:
        print("Usage: python create_synthetic_workflows.py <token>")
        sys.exit(1)

    print("Creating synthetic data workflow demos...")
    wf4_id = create_workflow("Weak Label — Rule-Based Ticket Classification", wf4_graph)
    wf5_id = create_workflow("Synthetic QA Dataset Prep + Validation", wf5_graph)

    print("\nAll workflows created:")
    print(f"  WF4 (Weak Label):     {wf4_id}")
    print(f"  WF5 (Dataset Prep):   {wf5_id}")
