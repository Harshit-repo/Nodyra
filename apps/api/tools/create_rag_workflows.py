"""Create RAG lifecycle workflow demos."""

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
        f"{API}/workflows/{wf_id}", headers=HEADERS, json={"environment_id": env_id, "graph": graph}
    )
    r2.raise_for_status()
    print(f"  Created {name!r}  id={wf_id}")
    return wf_id


# ============================================================
# Workflow 6: RAG Chunking + Answer Eval (no OpenAI needed)
# ============================================================

DOCS_CODE = """
docs = [
    {
        "id": 1,
        "source": "ml-basics.txt",
        "text": (
            "Machine learning is a branch of artificial intelligence. "
            "It enables systems to learn from experience. "
            "Supervised learning uses labeled training data. "
            "Unsupervised learning finds hidden patterns. "
            "Reinforcement learning learns through rewards and penalties. "
            "Deep learning uses neural networks with many layers. "
            "Transformers revolutionized natural language processing. "
            "Fine-tuning adapts pre-trained models to new tasks. "
            "Embeddings represent text as dense numerical vectors. "
            "RAG combines retrieval with generation for factual answers."
        ),
    },
    {
        "id": 2,
        "source": "dl-advanced.txt",
        "text": (
            "Convolutional neural networks excel at image processing tasks. "
            "Recurrent networks handle sequential data like time series. "
            "Attention mechanisms let models focus on relevant parts of input. "
            "BERT is a bidirectional transformer trained on masked language modeling. "
            "GPT uses autoregressive generation for text completion tasks. "
            "Diffusion models generate high-quality images from noise. "
            "Contrastive learning creates embeddings without labeled data. "
            "Knowledge distillation transfers knowledge from large to small models. "
            "Quantization reduces model size and inference cost significantly."
        ),
    },
]
output = docs
"""

QA_PAIRS_CODE = """
# Simulate RAG: given chunks context, generate Q&A pairs for eval
qa = [
    {
        "question": "What is supervised learning?",
        "context": "Supervised learning uses labeled training data to train models.",
        "answer": "Supervised learning trains models using labeled data.",
        "expected": "labeled training data",
    },
    {
        "question": "What is fine-tuning?",
        "context": "Fine-tuning adapts pre-trained models to new tasks.",
        "answer": "Fine-tuning is the process of adapting a pre-trained model to a specific task.",
        "expected": "adapts pre-trained models",
    },
    {
        "question": "What is deep learning?",
        "context": "Deep learning uses neural networks with many layers.",
        "answer": "Pizza is delicious.",  # Wrong answer for testing
        "expected": "neural networks with many layers",
    },
    {
        "question": "What is BERT?",
        "context": "BERT is a bidirectional transformer trained on masked language modeling.",
        "answer": "BERT is a transformer model trained with bidirectional context.",
        "expected": "bidirectional transformer",
    },
    {
        "question": "What are embeddings?",
        "context": "Embeddings represent text as dense numerical vectors.",
        "answer": "Embeddings are numerical vector representations of text.",
        "expected": "dense numerical vectors",
    },
]
output = qa
"""

SUMMARY_CODE = """
results = input
n = results.get('summary', {}).get('n_rows', 0) if isinstance(results, dict) else 0
accuracy = results.get('summary', {}).get('accuracy', 0) if isinstance(results, dict) else 0
print(f'RAG Eval: {n} Q&A pairs, accuracy={accuracy:.1%}')
output = {
    'accuracy': accuracy,
    'n_evaluated': n,
}
"""

wf6_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "docs",
            "code",
            label="Sample Documents",
            params={"code": DOCS_CODE},
            x=240,
            outputs_override=["main"],
        ),
        make_node("ds", "records_to_dataset", x=480),
        make_node(
            "chunk",
            "document_chunk",
            x=720,
            params={
                "text_column": "text",
                "strategy": "sentence",
                "chunk_size": 200,
                "chunk_overlap": 30,
                "min_chunk_size": 30,
                "include_metadata": True,
            },
        ),
        make_node(
            "qa",
            "code",
            label="Simulated Q&A Pairs",
            params={"code": QA_PAIRS_CODE},
            x=1000,
            outputs_override=["main"],
        ),
        make_node("qa_ds", "records_to_dataset", x=1240),
        make_node(
            "relevance",
            "rag_context_relevance",
            x=1480,
            params={
                "mode": "keyword_overlap",
                "question_column": "question",
                "context_column": "context",
                "output_column": "context_relevant",
                "overlap_threshold": 0.2,
            },
        ),
        make_node(
            "answer_eval",
            "rag_answer_eval",
            x=1760,
            params={
                "mode": "contains_answer",
                "answer_column": "answer",
                "expected_column": "expected",
            },
        ),
        make_node(
            "gate",
            "eval_gate",
            x=2040,
            params={"metric": "accuracy", "operator": ">=", "threshold": 0.6, "on_fail": "branch"},
        ),
        make_node(
            "report",
            "eval_report",
            x=2300,
            y=60,
            params={"title": "RAG Answer Eval Report", "include_sample_rows": True},
        ),
        make_node(
            "fail",
            "code",
            label="Low RAG Accuracy",
            params={
                "code": "acc = input.get('input', {}).get('value', 0)\nprint(f'RAG accuracy too low: {acc:.1%}')\noutput = input"
            },
            x=2300,
            y=200,
            outputs_override=["main"],
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "docs", "input"),
        make_edge("e2", "docs", "main", "ds", "input"),
        make_edge("e3", "ds", "main", "chunk", "input"),
        # Q&A pairs go to separate eval branch (chunk output ignored for now)
        make_edge("e4", "qa", "main", "qa_ds", "input"),
        make_edge("e5", "qa_ds", "main", "relevance", "input"),
        make_edge("e6", "relevance", "rows", "answer_eval", "input"),
        make_edge("e7", "answer_eval", "main", "gate", "input"),
        make_edge("e8", "gate", "pass", "report", "input"),
        make_edge("e9", "gate", "fail", "fail", "input"),
        # chunk also connects to trigger for parallel demonstration
        make_edge("e10", "trig", "main", "qa", "input"),
    ],
}


# ============================================================
# Workflow 7: Chunking Experiment — Compare Strategies
# ============================================================

MULTI_DOCS_CODE = """
import textwrap
docs = [
    {"id": i+1, "text": " ".join([
        f"Section {i+1}.{j+1}: This is the content of subsection {j+1} in document {i+1}. " +
        "It contains important information about machine learning and artificial intelligence. " +
        "The text is intentionally long enough to demonstrate chunking behavior properly."
        for j in range(5)
    ])} for i in range(10)
]
output = docs
"""

wf7_graph = {
    "nodes": [
        make_node("trig", "manual_trigger", params={"data": {}}, x=0),
        make_node(
            "gen",
            "code",
            label="Generate Multi-Doc Corpus",
            params={"code": MULTI_DOCS_CODE},
            x=240,
            outputs_override=["main"],
        ),
        make_node("ds", "records_to_dataset", x=480),
        make_node(
            "experiment",
            "rag_chunking_experiment",
            x=720,
            params={
                "text_column": "text",
                "strategies": "fixed_size, sentence, paragraph, recursive",
                "chunk_sizes": "128, 256, 512",
                "chunk_overlap": 32,
                "min_chunk_size": 20,
                "max_documents": 10,
            },
        ),
        make_node(
            "done",
            "code",
            label="Log Results",
            params={
                "code": "n = input.get('n_experiments', 0)\nprint(f'Chunking experiment complete: {n} configurations tested')\noutput = input"
            },
            x=1000,
            outputs_override=["main"],
        ),
    ],
    "edges": [
        make_edge("e1", "trig", "main", "gen", "input"),
        make_edge("e2", "gen", "main", "ds", "input"),
        make_edge("e3", "ds", "main", "experiment", "input"),
        make_edge("e4", "experiment", "main", "done", "input"),
    ],
}


if __name__ == "__main__":
    if not TOKEN:
        print("Usage: python create_rag_workflows.py <token>")
        sys.exit(1)

    print("Creating RAG lifecycle workflow demos...")
    wf6_id = create_workflow("RAG Chunk and Answer Eval Pipeline", wf6_graph)
    wf7_id = create_workflow("RAG Chunking Experiment — Compare Strategies", wf7_graph)

    print("\nAll workflows created:")
    print(f"  WF6 (RAG Eval):        {wf6_id}")
    print(f"  WF7 (Chunk Experiment):{wf7_id}")
