"""Seed local AI demo workflows into the development API database.

Run from ``apps/api`` so the relative SQLite URL in ``apps/api/.env`` points at
``apps/api/dev.db``:

    uv run python ../../scripts/seed_ai_demo_workflows.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from textwrap import dedent
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

import nodyra_nodes  # noqa: E402,F401  # registers bundled nodes
from app.db import SessionLocal  # noqa: E402
from app.models import Credential, Environment, Workflow, WorkflowVersion  # noqa: E402
from app.services import org_keys  # noqa: E402
from app.tenancy import DEFAULT_ORG_ID  # noqa: E402
from nodyra.artifacts import LocalArtifactStore  # noqa: E402
from nodyra.context import artifact_store  # noqa: E402
from nodyra.engine import execute  # noqa: E402
from nodyra.models import WorkflowGraph  # noqa: E402
from nodyra.sdk import registry  # noqa: E402


def node(
    node_id: str,
    node_type: str,
    params: dict[str, Any] | None = None,
    x: int = 0,
    y: int = 0,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "params": params or {},
        "position": {"x": x, "y": y},
    }


def edge(
    edge_id: str,
    source: str,
    target: str,
    source_output: str = "main",
    target_input: str = "input",
) -> dict[str, str]:
    return {
        "id": edge_id,
        "source": source,
        "source_output": source_output,
        "target": target,
        "target_input": target_input,
    }


def graph(nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> dict[str, Any]:
    return {"nodes": nodes, "edges": edges}


SUPPORT_SCHEMA = json.dumps(
    {
        "type": "object",
        "required": ["customer", "category", "priority", "summary"],
        "properties": {
            "customer": {"type": "string"},
            "category": {"type": "string"},
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high", "urgent"],
            },
            "summary": {"type": "string"},
        },
        "additionalProperties": True,
    }
)

LONG_POLICY_TEXT = " ".join(
    [
        "Production AI workflows should capture the prompt, model, usage, "
        "latency, and response identifiers.",
        "They should enforce explicit credentials, fail fast on invalid inputs, "
        "and avoid hidden side effects.",
        "Retrieval workflows should keep citations attached to generated answers "
        "so reviewers can audit the response.",
        "Agent workflows should bound tool calls, expose intermediate steps, and "
        "require approval for side effects.",
        "Dataset mapping workflows should preserve row identifiers and surface "
        "partial failures instead of hiding them.",
    ]
    * 8
)

DEMO_CREDENTIAL = {
    "name": "Demo Fake API Key",
    "type": "generic",
    "description": (
        "Non-production credential seeded by make demo. It is safe to delete "
        "and must never be used for real integrations."
    ),
    "data": {"api_key": "demo_not_a_real_secret", "label": "fake-demo-key"},
}

WORKFLOWS: list[dict[str, Any]] = [
    {
        "name": "AI Demo - Local Prompt To Structured JSON",
        "description": (
            "Runs without external credentials. Exercises prompt templating, a "
            "local mock AI transform, and JSON schema validation."
        ),
        "graph": graph(
            [
                node(
                    "trigger",
                    "manual_trigger",
                    {
                        "data": {
                            "customer": "Ada Lovelace",
                            "tier": "enterprise",
                            "issue": "API latency after release",
                            "priority": "high",
                        }
                    },
                    0,
                    80,
                ),
                node(
                    "prompt",
                    "ai_prompt_template",
                    {
                        "system_template": (
                            "You are a support triage assistant. Return strict JSON only."
                        ),
                        "prompt_template": (
                            "Customer: {{ customer }}\nTier: {{ tier }}\n"
                            "Issue: {{ issue }}\nPriority: {{ priority }}"
                        ),
                        "strict_undefined": True,
                    },
                    320,
                    80,
                ),
                node(
                    "mock_ai",
                    "code",
                    {
                        "code": dedent(
                            """
                            if isinstance(input, dict):
                                prompt = input.get("prompt", "")
                            else:
                                prompt = str(input)
                            lowered = prompt.lower()
                            customer = (
                                "Ada Lovelace"
                                if "ada lovelace" in lowered
                                else "Unknown"
                            )
                            output = {
                                "customer": customer,
                                "category": "incident_summary",
                                "priority": "high" if "high" in lowered else "normal",
                                "summary": (
                                    "Enterprise customer reported API latency "
                                    "after release; route to platform support."
                                ),
                            }
                            """
                        ).strip()
                    },
                    650,
                    80,
                ),
                node(
                    "validate",
                    "json_schema_validate",
                    {"schema": SUPPORT_SCHEMA},
                    980,
                    80,
                ),
            ],
            [
                edge("e1", "trigger", "prompt"),
                edge("e2", "prompt", "mock_ai"),
                edge("e3", "mock_ai", "validate"),
            ],
        ),
    },
    {
        "name": "AI Demo - Local Text Chunking",
        "description": (
            "Runs without external credentials. Exercises AI text chunking and "
            "produces a chunk summary for inspection."
        ),
        "graph": graph(
            [
                node(
                    "trigger",
                    "manual_trigger",
                    {"data": {"source": "AI operations checklist"}},
                    0,
                    80,
                ),
                node(
                    "chunk",
                    "ai_text_chunk",
                    {
                        "text": LONG_POLICY_TEXT,
                        "chunk_size": 520,
                        "overlap": 80,
                        "preserve_words": True,
                    },
                    320,
                    80,
                ),
                node(
                    "summary",
                    "code",
                    {
                        "code": dedent(
                            """
                            chunks = input if isinstance(input, list) else []
                            first_chars = (
                                chunks[0].get("char_count", 0) if chunks else 0
                            )
                            output = {
                                "chunk_count": len(chunks),
                                "first_chunk_chars": first_chars,
                                "preview": chunks[:2],
                            }
                            """
                        ).strip()
                    },
                    650,
                    80,
                ),
            ],
            [edge("e1", "trigger", "chunk"), edge("e2", "chunk", "summary")],
        ),
    },
    {
        "name": "AI Demo - Local Dataset Enrichment",
        "description": (
            "Runs without external credentials. Exercises DatasetRef conversion, "
            "row mapping via a local mock AI step, and preview output."
        ),
        "graph": graph(
            [
                node(
                    "trigger",
                    "manual_trigger",
                    {
                        "data": [
                            {
                                "ticket_id": "T-1001",
                                "customer": "Northwind",
                                "issue": "Webhook retries failing",
                                "priority": "urgent",
                            },
                            {
                                "ticket_id": "T-1002",
                                "customer": "Contoso",
                                "issue": "Invoice export missing tax field",
                                "priority": "normal",
                            },
                            {
                                "ticket_id": "T-1003",
                                "customer": "Fabrikam",
                                "issue": "Slow dashboard load",
                                "priority": "high",
                            },
                        ]
                    },
                    0,
                    80,
                ),
                node("to_dataset", "records_to_dataset", {"format": "jsonl"}, 320, 80),
                node(
                    "to_records",
                    "dataset_to_records",
                    {"max_rows": 25, "allow_truncate": False},
                    650,
                    80,
                ),
                node(
                    "mock_map",
                    "code",
                    {
                        "code": dedent(
                            """
                            rows = input if isinstance(input, list) else []
                            enriched = []
                            for row in rows:
                                customer = row.get("customer", "Unknown")
                                issue = row.get("issue", "No issue")
                                priority = row.get("priority", "normal")
                                enriched.append({
                                    **row,
                                    "ai_summary": (
                                        f"{customer}: {issue} [{priority}]"
                                    ),
                                    "recommended_queue": (
                                        "platform"
                                        if priority in {"high", "urgent"}
                                        else "operations"
                                    ),
                                })
                            output = enriched
                            """
                        ).strip()
                    },
                    980,
                    80,
                ),
                node("out_dataset", "records_to_dataset", {"format": "jsonl"}, 1310, 80),
                node("preview", "dataset_preview", {"max_rows": 10}, 1640, 80),
            ],
            [
                edge("e1", "trigger", "to_dataset"),
                edge("e2", "to_dataset", "to_records"),
                edge("e3", "to_records", "mock_map"),
                edge("e4", "mock_map", "out_dataset"),
                edge("e5", "out_dataset", "preview"),
            ],
        ),
    },
    {
        "name": "Demo - Runnable Webhook Intake",
        "description": (
            "Active published webhook demo. POST JSON to /webhook/demo/intake "
            "and inspect the resulting run."
        ),
        "active": True,
        "webhook_path": "demo/intake",
        "graph": graph(
            [
                node(
                    "hook",
                    "webhook_trigger",
                    {
                        "path": "demo/intake",
                        "http_method": "POST",
                        "auth_type": "none",
                    },
                    0,
                    80,
                ),
                node(
                    "normalize",
                    "code",
                    {
                        "code": dedent(
                            """
                            body = input.get("body", {}) if isinstance(input, dict) else {}
                            output = {
                                "received": body,
                                "source": "demo-webhook",
                                "summary": (
                                    f"Received demo webhook for "
                                    f"{body.get('customer', 'unknown customer')}"
                                ),
                            }
                            """
                        ).strip()
                    },
                    320,
                    80,
                ),
            ],
            [edge("e1", "hook", "normalize")],
        ),
    },
    {
        "name": "AI Demo - Real Chat With LLM Provider",
        "description": (
            "Needs an LLM provider credential. Exercises prompt templating and "
            "the normalized AI Chat node."
        ),
        "graph": graph(
            [
                node(
                    "trigger",
                    "manual_trigger",
                    {
                        "data": {
                            "product": "Nodyra",
                            "audience": "workflow builders",
                            "tone": "concise",
                        }
                    },
                    0,
                    80,
                ),
                node(
                    "prompt",
                    "ai_prompt_template",
                    {
                        "system_template": ("You write concise product workflow summaries."),
                        "prompt_template": (
                            "Write three bullet points explaining how {{ product }} "
                            "helps {{ audience }}. Tone: {{ tone }}."
                        ),
                        "strict_undefined": True,
                    },
                    320,
                    80,
                ),
                node(
                    "chat",
                    "ai_chat",
                    {
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "temperature": 0.2,
                        "max_tokens": 400,
                        "credentials": "",
                    },
                    650,
                    80,
                ),
            ],
            [edge("e1", "trigger", "prompt"), edge("e2", "prompt", "chat")],
        ),
    },
    {
        "name": "AI Demo - Agent With Model Memory Tools",
        "description": (
            "Needs an LLM provider credential. Exercises n8n-style AI wiring: "
            "chat model, memory, multiple tools, and an agent."
        ),
        "graph": graph(
            [
                node(
                    "trigger",
                    "manual_trigger",
                    {
                        "data": {
                            "task": (
                                "Draft a support triage note. Use the policy "
                                "lookup tool only if you need extra context."
                            )
                        }
                    },
                    0,
                    80,
                ),
                node(
                    "model",
                    "ai_chat_model",
                    {
                        "provider": "openrouter",
                        "model": "openai/gpt-4.1-mini",
                        "temperature": 0.1,
                        "max_tokens": 500,
                        "credentials": "",
                    },
                    300,
                    -120,
                ),
                node(
                    "memory",
                    "ai_memory_buffer",
                    {
                        "session_id": "demo_support_thread",
                        "messages_json": json.dumps(
                            [
                                {
                                    "role": "user",
                                    "content": (
                                        "Customer is enterprise tier and is "
                                        "blocked by API latency after release."
                                    ),
                                },
                                {
                                    "role": "assistant",
                                    "content": (
                                        "Initial priority should consider "
                                        "impact, reproducibility, and rollback."
                                    ),
                                },
                            ]
                        ),
                        "max_messages": 10,
                    },
                    300,
                    80,
                ),
                node(
                    "policy_tool",
                    "ai_tool",
                    {
                        "name": "lookup_policy",
                        "description": (
                            "Fetch a policy lookup echo endpoint for demo tool-call testing."
                        ),
                        "tool_type": "http",
                        "method": "GET",
                        "url": "https://httpbin.org/get",
                        "parameters_schema_json": json.dumps(
                            {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "Policy search query.",
                                    }
                                },
                                "additionalProperties": False,
                            }
                        ),
                    },
                    300,
                    280,
                ),
                node(
                    "toolbox",
                    "ai_tool_box",
                    {"strict": True},
                    600,
                    280,
                ),
                node(
                    "agent",
                    "ai_agent",
                    {
                        "system": (
                            "You are a production support triage agent. Return "
                            "a concise answer with priority, reasoning, and "
                            "next action."
                        ),
                        "fallback_model": "gpt-4.1-mini",
                        "max_steps": 4,
                        "memory_max_messages": 10,
                        "allow_side_effects": False,
                    },
                    870,
                    80,
                ),
            ],
            [
                edge("e1", "trigger", "agent"),
                edge("e2", "model", "agent", "model", "model"),
                edge("e3", "memory", "agent", "memory", "memory"),
                edge("e4", "policy_tool", "toolbox", "main", "tool_1"),
                edge("e5", "toolbox", "agent", "tools", "tools"),
            ],
        ),
    },
    {
        "name": "AI Demo - RAG Answer With Pinecone",
        "description": (
            "Needs LLM provider and Pinecone credentials. Exercises vector "
            "retrieval and grounded answer generation."
        ),
        "graph": graph(
            [
                node(
                    "trigger",
                    "manual_trigger",
                    {
                        "data": {
                            "query": ("What production safeguards should an AI workflow include?")
                        }
                    },
                    0,
                    80,
                ),
                node(
                    "retrieve",
                    "ai_vector_retriever",
                    {
                        "query": ("What production safeguards should an AI workflow include?"),
                        "embedding_provider": "openai",
                        "embedding_model": "text-embedding-3-small",
                        "index": "nodyra-docs",
                        "namespace": "demo",
                        "top_k": 5,
                        "embedding_credentials": "",
                        "pinecone_credentials": "",
                    },
                    320,
                    80,
                ),
                node(
                    "answer",
                    "ai_rag_answer",
                    {
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "question": ("What production safeguards should an AI workflow include?"),
                        "temperature": 0.1,
                        "credentials": "",
                    },
                    650,
                    80,
                ),
            ],
            [edge("e1", "trigger", "retrieve"), edge("e2", "retrieve", "answer")],
        ),
    },
]


async def seed_demo_credential(session) -> dict[str, str]:
    encrypted_data, encrypted_dek = await org_keys.encrypt_credential_for(
        DEFAULT_ORG_ID, DEMO_CREDENTIAL["data"], session
    )
    credential = await session.scalar(
        select(Credential).where(Credential.name == DEMO_CREDENTIAL["name"])
    )
    if credential is None:
        credential = Credential(
            name=DEMO_CREDENTIAL["name"],
            type=DEMO_CREDENTIAL["type"],
            scope="global",
            description=DEMO_CREDENTIAL["description"],
            encrypted_data=encrypted_data,
            encrypted_dek=encrypted_dek,
        )
        session.add(credential)
        action = "created"
    else:
        credential.type = DEMO_CREDENTIAL["type"]
        credential.scope = "global"
        credential.description = DEMO_CREDENTIAL["description"]
        credential.encrypted_data = encrypted_data
        credential.encrypted_dek = encrypted_dek
        action = "updated"
    return {"name": DEMO_CREDENTIAL["name"], "action": action}


async def seed_workflows() -> dict[str, Any]:
    async with SessionLocal() as session:
        env_id = await session.scalar(select(Environment.id).where(Environment.is_global.is_(True)))
        credential = await seed_demo_credential(session)
        seeded: list[dict[str, str]] = []
        for item in WORKFLOWS:
            active = bool(item.get("active", False))
            workflow = await session.scalar(
                select(Workflow)
                .options(selectinload(Workflow.versions))
                .where(Workflow.name == item["name"])
            )
            if workflow is None:
                workflow = Workflow(
                    name=item["name"],
                    active=active,
                    environment_id=env_id,
                    draft_graph=item["graph"],
                    published_version=1,
                )
                workflow.versions.append(
                    WorkflowVersion(
                        version=1,
                        graph=item["graph"],
                        notes=item["description"],
                    )
                )
                session.add(workflow)
                action = "created"
            else:
                workflow.active = active
                if workflow.environment_id is None:
                    workflow.environment_id = env_id
                workflow.draft_graph = item["graph"]
                if workflow.versions:
                    latest = max(workflow.versions, key=lambda version: version.version)
                    latest.graph = item["graph"]
                    latest.notes = item["description"]
                    workflow.published_version = latest.version
                else:
                    workflow.versions.append(
                        WorkflowVersion(
                            version=1,
                            graph=item["graph"],
                            notes=item["description"],
                        )
                    )
                    workflow.published_version = 1
                action = "updated"
            seeded.append({"name": item["name"], "action": action})
        await session.commit()
        webhooks = [
            {
                "name": item["name"],
                "url": f"/webhook/{item['webhook_path']}",
            }
            for item in WORKFLOWS
            if item.get("webhook_path")
        ]
        return {"credential": credential, "workflows": seeded, "webhooks": webhooks}


async def run_local_workflows() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    temp_root = Path(tempfile.mkdtemp(prefix="nodyra-ai-demo-artifacts-"))
    token = artifact_store.set(LocalArtifactStore(temp_root, run_id="ai-demo-seed"))
    try:
        for item in WORKFLOWS[:3]:
            result = await execute(WorkflowGraph.model_validate(item["graph"]), registry)
            terminal_nodes = [
                candidate["id"]
                for candidate in item["graph"]["nodes"]
                if not any(link["source"] == candidate["id"] for link in item["graph"]["edges"])
            ]
            terminal_outputs = {
                node_id: result.nodes[node_id].outputs.get("main")
                for node_id in terminal_nodes
                if node_id in result.nodes
            }
            errors = {
                node_id: node_result.error
                for node_id, node_result in result.nodes.items()
                if node_result.error
            }
            results.append(
                {
                    "name": item["name"],
                    "status": result.status,
                    "terminal_outputs": terminal_outputs,
                    "errors": errors,
                }
            )
    finally:
        artifact_store.reset(token)
    return results


async def main() -> None:
    seeded = await seed_workflows()
    local_results = await run_local_workflows()
    print(json.dumps({"seeded": seeded, "local_results": local_results}, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
