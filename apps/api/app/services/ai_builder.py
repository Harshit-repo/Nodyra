"""Validated LLM-backed workflow draft builder.

The AI builder never executes agent plans. It asks an LLM for a strict JSON graph
proposal, validates that proposal against Noodle's graph model and an allow-list
of built-in node ids, strips unsafe credential refs/secrets, and returns a
normal editable draft graph. If no model is configured or the model returns an
invalid graph, a deterministic fallback produces a conservative editable graph.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential, Workflow
from app.schemas import AiWorkflowDraftRequest, AiWorkflowDraftResponse
from app.services.credentials import CREDENTIAL_REF_MARKER, credential_ref
from app.services.org_keys import decrypt_credential_for
from noodle.models import Edge, GraphNode, Position, WorkflowGraph

_ALLOWED_NODE_TYPES = {
    "manual_trigger",
    "schedule_trigger",
    "webhook_trigger",
    "switch",
    "edit_fields",
    "code",
    "http_request",
    "slack",
    "smtp_send_email",
    "notion_create_page_v2",
    "github_get_repo_v2",
    "github_create_issue_v2",
    "postgres_query",
    "mysql_query",
    "s3_put_object",
    "s3_get_object",
    "openai_chat",
    "anthropic_message",
    "ai_prompt_template",
    "ai_chat_model",
    "ai_chat",
    "ai_structured_output",
    "ai_text_chunk",
    "ai_batch_embeddings",
    "ai_dataset_map",
    "ai_vector_retriever",
    "ai_rag_answer",
    "ai_memory_buffer",
    "ai_tool",
    "ai_tool_box",
    "ai_agent",
    "ai_moderation_guard",
    "ai_vision_analyze",
    "ai_image_generate",
    "airtable_list_records_v2",
    "airtable_create_record_v2",
    "csv_parse",
    "csv_write",
    "json_schema_validate",
}

_NODE_REGISTRY: dict[str, dict[str, Any]] = {
    "manual_trigger": {"name": "Manual Trigger", "params": ["data"]},
    "schedule_trigger": {"name": "Schedule Trigger", "params": ["cron", "timezone"]},
    "webhook_trigger": {
        "name": "Webhook",
        "params": ["http_method", "path", "response_mode", "response_code"],
    },
    "switch": {"name": "Switch", "params": ["rules"]},
    "edit_fields": {"name": "Edit Fields", "params": ["fields", "keep_only_set"]},
    "code": {"name": "Code", "params": ["code"]},
    "http_request": {
        "name": "HTTP Request",
        "params": ["url", "method", "headers", "query", "body", "timeout_seconds"],
    },
    "slack": {
        "name": "Slack",
        "params": [
            "resource",
            "operation",
            "credentials",
            "channel",
            "text",
            "blocks",
            "thread_ts",
        ],
        "credential_specs": [{"type": "slack_bot", "param": "credentials", "key": "*"}],
    },
    "smtp_send_email": {
        "name": "SMTP Send Email",
        "params": [
            "host",
            "port",
            "username",
            "password",
            "use_tls",
            "from_email",
            "to_email",
            "subject",
            "body",
        ],
        "credential_type": "smtp",
        "credential_keys": ["username", "password"],
    },
    "github_get_repo_v2": {
        "name": "GitHub Get Repository V2",
        "params": ["credentials", "repo"],
        "credential_specs": [{"type": "github", "param": "credentials", "key": "*"}],
    },
    "github_create_issue_v2": {
        "name": "GitHub Create Issue V2",
        "params": ["credentials", "repo", "title", "body", "labels"],
        "credential_specs": [{"type": "github", "param": "credentials", "key": "*"}],
    },
    "openai_chat": {
        "name": "OpenAI Chat",
        "params": ["api_key", "model", "system", "prompt", "temperature", "max_tokens"],
        "credential_type": "openai",
        "credential_keys": ["api_key"],
    },
    "anthropic_message": {
        "name": "Anthropic Message",
        "params": ["api_key", "model", "system", "prompt", "max_tokens", "temperature"],
        "credential_type": "anthropic",
        "credential_keys": ["api_key"],
    },
    "ai_prompt_template": {
        "name": "AI Prompt Template",
        "params": ["system_template", "prompt_template", "strict_undefined"],
    },
    "ai_chat_model": {
        "name": "AI Chat Model",
        "params": [
            "credentials",
            "provider",
            "model",
            "temperature",
            "max_tokens",
            "response_format",
            "timeout_seconds",
        ],
        "credential_specs": [{"param": "credentials", "type": "llm_provider", "key": "*"}],
    },
    "ai_chat": {
        "name": "AI Chat",
        "params": [
            "credentials",
            "provider",
            "model",
            "system",
            "prompt",
            "messages_json",
            "temperature",
            "max_tokens",
            "response_format",
            "timeout_seconds",
        ],
        "credential_specs": [{"param": "credentials", "type": "llm_provider", "key": "*"}],
    },
    "ai_structured_output": {
        "name": "AI Structured Output",
        "params": ["credentials", "provider", "model", "system", "prompt", "schema_json"],
        "credential_specs": [{"param": "credentials", "type": "llm_provider", "key": "*"}],
    },
    "ai_text_chunk": {
        "name": "AI Text Chunker",
        "params": ["text", "chunk_size", "overlap", "metadata_json"],
    },
    "ai_batch_embeddings": {
        "name": "AI Batch Embeddings",
        "params": ["credentials", "provider", "model", "text_field", "output_field"],
        "credential_specs": [{"param": "credentials", "type": "llm_provider", "key": "*"}],
    },
    "ai_dataset_map": {
        "name": "AI Map Dataset",
        "params": [
            "credentials",
            "provider",
            "model",
            "system",
            "prompt_template",
            "output_column",
        ],
        "credential_specs": [{"param": "credentials", "type": "llm_provider", "key": "*"}],
    },
    "ai_vector_retriever": {
        "name": "AI Vector Retriever",
        "params": [
            "embedding_credentials",
            "pinecone_credentials",
            "query",
            "embedding_provider",
            "embedding_model",
            "namespace",
            "top_k",
        ],
        "credential_specs": [
            {"param": "embedding_credentials", "type": "llm_provider", "key": "*"},
            {"param": "pinecone_credentials", "type": "pinecone", "key": "*"},
        ],
    },
    "ai_rag_answer": {
        "name": "AI RAG Answer",
        "params": ["credentials", "provider", "model", "question", "context_field"],
        "credential_specs": [{"param": "credentials", "type": "llm_provider", "key": "*"}],
    },
    "ai_memory_buffer": {
        "name": "AI Simple Memory",
        "params": ["session_id", "messages_json", "input_role", "max_messages"],
    },
    "ai_tool": {
        "name": "AI Tool",
        "params": [
            "name",
            "description",
            "tool_type",
            "parameters_schema_json",
            "url",
            "workflow_id",
        ],
    },
    "ai_tool_box": {
        "name": "AI Tool Box",
        "params": ["strict"],
    },
    "ai_agent": {
        "name": "AI Agent",
        "params": [
            "credentials",
            "provider",
            "fallback_model",
            "system",
            "task",
            "max_steps",
            "memory_max_messages",
            "allow_side_effects",
        ],
        "credential_specs": [{"param": "credentials", "type": "llm_provider", "key": "*"}],
    },
    "ai_moderation_guard": {
        "name": "AI Moderation Guard",
        "params": ["credentials", "text", "model"],
        "credential_specs": [{"param": "credentials", "type": "openai", "key": "api_key"}],
    },
    "ai_vision_analyze": {
        "name": "AI Vision Analyze",
        "params": ["credentials", "model", "prompt", "image_url", "image_base64"],
        "credential_specs": [{"param": "credentials", "type": "llm_provider", "key": "*"}],
    },
    "ai_image_generate": {
        "name": "AI Image Generate",
        "params": ["credentials", "prompt", "model", "size", "filename"],
        "credential_specs": [{"param": "credentials", "type": "openai", "key": "api_key"}],
    },
    "notion_create_page_v2": {
        "name": "Notion Create Page V2",
        "params": [
            "credentials",
            "database_id",
            "parent_page_id",
            "title",
            "title_property",
            "properties",
            "content",
        ],
        "credential_specs": [{"type": "notion", "param": "credentials", "key": "*"}],
    },
    "postgres_query": {"name": "Postgres Query", "params": ["dsn", "query", "params"]},
    "mysql_query": {"name": "MySQL Query", "params": ["dsn", "query", "params"]},
    "s3_put_object": {"name": "S3 Put Object", "params": ["bucket", "key", "body", "content_type"]},
    "s3_get_object": {"name": "S3 Get Object", "params": ["bucket", "key"]},
    "airtable_list_records_v2": {
        "name": "Airtable List Records V2",
        "params": ["credentials", "base_id", "table_name", "view", "max_records", "filter_formula"],
        "credential_specs": [{"type": "airtable", "param": "credentials", "key": "*"}],
    },
    "airtable_create_record_v2": {
        "name": "Airtable Create Record V2",
        "params": ["credentials", "base_id", "table_name", "fields", "typecast"],
        "credential_specs": [{"type": "airtable", "param": "credentials", "key": "*"}],
    },
    "csv_parse": {"name": "CSV Parse", "params": ["text", "delimiter"]},
    "csv_write": {"name": "CSV Write", "params": ["rows", "delimiter"]},
    "json_schema_validate": {"name": "JSON Schema Validate", "params": ["schema"]},
}

_SECRETISH = re.compile(r"(?i)(sk-[a-z0-9_-]{12,}|xox[baprs]-[a-z0-9-]{10,}|api[_-]?key\s*[:=])")


@dataclass
class _DraftResult:
    graph: WorkflowGraph
    assumptions: list[str] = field(default_factory=list)
    missing_credentials: list[str] = field(default_factory=list)
    required_packages: list[str] = field(default_factory=list)
    explanation: str = ""
    mode: str = "draft"
    change_summary: list[str] = field(default_factory=list)
    confidence: str = "medium"
    focus_node_id: str | None = None
    planner: str = "deterministic_fallback"


def _node(
    node_id: str,
    node_type: str,
    x: float,
    y: float,
    params: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        type=node_type,
        params=params or {},
        position=Position(x=x, y=y),
    )


def _edge(source: str, target: str, target_input: str = "input") -> Edge:
    return Edge(
        id=f"e_{source}_{target}_{uuid.uuid4().hex[:6]}",
        source=source,
        source_output="main",
        target=target,
        target_input=target_input,
    )


def _slug(text: str, fallback: str = "ai-workflow") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or fallback)[:48]


def _coerce_graph(value: Any) -> WorkflowGraph:
    graph = WorkflowGraph.model_validate(value)
    node_ids = {node.id for node in graph.nodes}
    for node in graph.nodes:
        if node.type not in _ALLOWED_NODE_TYPES:
            raise ValueError(f"unknown node type: {node.type}")
        node.params = _sanitize_params(node.params)
    for edge in graph.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise ValueError("edge references a missing node")
    return graph


def _sanitize_params(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get(CREDENTIAL_REF_MARKER) is True:
            return ""
        return {str(k): _sanitize_params(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_params(item) for item in value]
    if isinstance(value, str) and _SECRETISH.search(value):
        return ""
    return value


async def _find_credential(
    session: AsyncSession,
    cred_type: str,
    *,
    workflow_id: str,
    environment_id: str | None,
) -> Credential | None:
    """Return exactly one most-specific visible credential for this type."""
    stmt = select(Credential).where(Credential.type == cred_type)
    rows = (await session.scalars(stmt)).all()
    if not rows:
        return None

    for predicate in (
        lambda c: c.scope == "workflow" and c.workflow_id == workflow_id,
        lambda c: (
            c.scope == "environment"
            and environment_id is not None
            and c.environment_id == environment_id
        ),
        lambda c: c.scope == "global",
    ):
        matches = [c for c in rows if predicate(c)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


def _attach_credential(params: dict, keys: list[str], cred: Credential | None) -> None:
    if cred is None:
        return
    for key in keys:
        params[key] = credential_ref(cred.id, key)


async def _attach_graph_credentials(
    session: AsyncSession,
    graph: WorkflowGraph,
    *,
    workflow_id: str,
    environment_id: str | None,
) -> list[str]:
    missing: list[str] = []
    for node in graph.nodes:
        spec = _NODE_REGISTRY.get(node.type, {})
        credential_specs = spec.get("credential_specs")
        if isinstance(credential_specs, list):
            entries = [
                entry
                for entry in credential_specs
                if isinstance(entry, dict) and entry.get("type") and entry.get("param")
            ]
        else:
            cred_type = spec.get("credential_type")
            keys = list(spec.get("credential_keys") or [])
            entries = [
                {"type": cred_type, "param": key, "key": key} for key in keys if cred_type and key
            ]
        if not entries:
            continue
        for entry in entries:
            cred_type = str(entry["type"])
            param_name = str(entry["param"])
            key = str(entry.get("key") or param_name)
            cred = await _find_credential(
                session,
                cred_type,
                workflow_id=workflow_id,
                environment_id=environment_id,
            )
            if cred is not None:
                node.params[param_name] = credential_ref(cred.id, key)
            else:
                missing.append(_credential_label(cred_type, [key]))
    return sorted(set(missing))


def _credential_label(cred_type: str, keys: list[str]) -> str:
    if cred_type == "openai":
        return "OpenAI API key"
    if cred_type == "anthropic":
        return "Anthropic API key"
    if cred_type == "llm_provider":
        return "LLM provider credential"
    if cred_type == "pinecone":
        return "Pinecone credentials"
    if cred_type == "slack_bot":
        return "Slack bot token"
    if cred_type == "smtp":
        return "SMTP username/password or Gmail app password"
    if cred_type == "github":
        return "GitHub token"
    return f"{cred_type} credential ({', '.join(keys)})"


def _llm_configured() -> bool:
    return bool(
        os.getenv("OPENAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("NOODLE_AI_PROVIDER")
    )


async def _resolve_llm_provider(
    session: AsyncSession,
    *,
    workflow_id: str,
    environment_id: str | None,
    provider_hint: str | None = None,
    model_hint: str | None = None,
) -> tuple[str, str, str]:
    default_models = {
        "anthropic": "claude-3-5-haiku-latest",
        "openai": "gpt-4.1-mini",
    }
    # An explicit caller hint (assistant picker) wins over env configuration;
    # only the two network planners we support are honoured.
    hint = (provider_hint or "").strip().lower()
    if hint not in default_models:
        hint = ""
    provider = hint or (os.getenv("NOODLE_AI_PROVIDER") or "").strip().lower()
    model = (model_hint or "").strip() or os.getenv("NOODLE_AI_MODEL") or ""
    api_key = ""

    if provider == "anthropic" or (not provider and os.getenv("ANTHROPIC_API_KEY")):
        provider = "anthropic"
        api_key = os.getenv("ANTHROPIC_API_KEY") or ""
        model = model or default_models["anthropic"]
    elif provider == "openai" or (not provider and os.getenv("OPENAI_API_KEY")):
        provider = "openai"
        api_key = os.getenv("OPENAI_API_KEY") or ""
        model = model or default_models["openai"]

    if not api_key:
        # Prefer the hinted provider's stored credential, then the other one.
        order = ["openai", "anthropic"]
        if hint == "anthropic":
            order = ["anthropic", "openai"]
        for cred_type in order:
            default_model = default_models[cred_type]
            cred = await _find_credential(
                session,
                cred_type,
                workflow_id=workflow_id,
                environment_id=environment_id,
            )
            if cred is None:
                continue
            data = await decrypt_credential_for(cred, session)
            key = str(data.get("api_key") or "")
            # Stored credentials are allowed as planner credentials only when
            # they look like real provider keys. Test/demo placeholders remain
            # normal node credentials and do not trigger network planner calls.
            if len(key) >= 16:
                # Honour the requested model only when it matches the provider
                # we actually fell back to; otherwise use that provider default.
                use_model = model if (hint and hint == cred_type and model) else default_model
                return cred_type, use_model, key

    if not provider or not api_key:
        raise RuntimeError("No AI planner provider configured")
    return provider, model, api_key


def _llm_messages(
    body: AiWorkflowDraftRequest, current_graph: WorkflowGraph | None
) -> list[dict[str, str]]:
    system = (
        "You are Noodle's workflow graph planner. Return JSON only. "
        "You must create editable Noodle workflow graphs, never hidden execution. "
        "Use only allowed node types from the registry. Never output raw secrets "
        "or credential refs. "
        "Credential fields should be empty strings; the server attaches credentials safely."
    )
    payload = {
        "mode": body.mode,
        "fix_strategy": body.fix_strategy,
        "prompt": body.prompt,
        "failed_run_id": body.failed_run_id,
        "failed_node_id": body.failed_node_id,
        "error": body.error,
        "current_graph": current_graph.model_dump() if current_graph is not None else None,
        "allowed_nodes": _NODE_REGISTRY,
        "response_schema": {
            "graph": {"nodes": [], "edges": []},
            "assumptions": ["string"],
            "missing_credentials": ["string"],
            "required_packages": ["string"],
            "explanation": "string",
            "change_summary": ["string"],
            "confidence": "low|medium|high",
            "focus_node_id": "node id or null",
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
    ]


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network failure shape
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"AI planner HTTP {exc.code}: {detail}") from exc


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def _call_llm_json(
    session: AsyncSession,
    *,
    workflow_id: str,
    environment_id: str | None,
    body: AiWorkflowDraftRequest,
    current_graph: WorkflowGraph | None,
) -> dict[str, Any]:
    provider, model, api_key = await _resolve_llm_provider(
        session,
        workflow_id=workflow_id,
        environment_id=environment_id,
        provider_hint=body.planner_provider,
        model_hint=body.planner_model,
    )
    messages = _llm_messages(body, current_graph)
    if provider == "anthropic":
        payload = {
            "model": model,
            "max_tokens": 3000,
            "temperature": 0.1,
            "system": messages[0]["content"],
            "messages": [messages[1]],
        }
        raw = await asyncio.to_thread(
            _post_json,
            "https://api.anthropic.com/v1/messages",
            {
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            payload,
        )
        chunks = raw.get("content") or []
        text = "".join(str(chunk.get("text") or "") for chunk in chunks if isinstance(chunk, dict))
        return _extract_json_object(text)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    raw = await asyncio.to_thread(
        _post_json,
        "https://api.openai.com/v1/chat/completions",
        {"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        payload,
    )
    choices = raw.get("choices") or []
    text = choices[0].get("message", {}).get("content", "") if choices else ""
    return _extract_json_object(str(text))


async def _call_llm_simple(
    prompt: str,
    system: str = "",
) -> dict[str, Any] | None:
    """Simplified LLM call for explain/refine/test features.

    Resolves the provider from environment variables only (no session/workflow
    context needed). Returns None when no provider is configured or the call
    fails — callers always have a fallback path.
    """
    if not _llm_configured():
        return None
    provider = (os.getenv("NOODLE_AI_PROVIDER") or "").strip().lower()
    model = os.getenv("NOODLE_AI_MODEL") or ""
    api_key = ""

    if provider == "anthropic" or (not provider and os.getenv("ANTHROPIC_API_KEY")):
        provider = "anthropic"
        api_key = os.getenv("ANTHROPIC_API_KEY") or ""
        model = model or "claude-3-5-haiku-latest"
    elif provider == "openai" or (not provider and os.getenv("OPENAI_API_KEY")):
        provider = "openai"
        api_key = os.getenv("OPENAI_API_KEY") or ""
        model = model or "gpt-4.1-mini"
    else:
        return None

    if not api_key:
        return None

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    try:
        if provider == "anthropic":
            payload = {
                "model": model,
                "max_tokens": 3000,
                "temperature": 0.1,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
            raw = await asyncio.to_thread(
                _post_json,
                "https://api.anthropic.com/v1/messages",
                {
                    "content-type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                payload,
            )
            chunks = raw.get("content") or []
            text = "".join(str(chunk.get("text") or "") for chunk in chunks if isinstance(chunk, dict))
            return _extract_json_object(text)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        raw = await asyncio.to_thread(
            _post_json,
            "https://api.openai.com/v1/chat/completions",
            {"content-type": "application/json", "authorization": f"Bearer {api_key}"},
            payload,
        )
        choices = raw.get("choices") or []
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        return _extract_json_object(str(text))
    except Exception:  # noqa: BLE001
        return None


def _current_or_workflow_graph(
    body: AiWorkflowDraftRequest, workflow: Workflow | None
) -> WorkflowGraph | None:
    if body.current_graph is not None:
        return body.current_graph
    if workflow is not None and workflow.draft_graph:
        try:
            return WorkflowGraph.model_validate(workflow.draft_graph)
        except Exception:
            return None
    return None


async def _result_from_llm_payload(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    body: AiWorkflowDraftRequest,
    workflow_id: str,
    environment_id: str | None,
) -> _DraftResult:
    graph = _coerce_graph(payload.get("graph"))
    server_missing = await _attach_graph_credentials(
        session,
        graph,
        workflow_id=workflow_id,
        environment_id=environment_id,
    )
    missing = sorted(set([*list(payload.get("missing_credentials") or []), *server_missing]))
    confidence = str(payload.get("confidence") or "medium").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"
    return _DraftResult(
        graph=graph,
        assumptions=[str(item) for item in payload.get("assumptions") or []],
        missing_credentials=missing,
        required_packages=[str(item) for item in payload.get("required_packages") or []],
        explanation=str(
            payload.get("explanation") or "Generated an editable Noodle workflow draft."
        ),
        mode=body.mode,
        change_summary=[str(item) for item in payload.get("change_summary") or []],
        confidence=confidence,
        focus_node_id=payload.get("focus_node_id"),
        planner="llm",
    )


async def _try_llm_result(
    session: AsyncSession,
    *,
    body: AiWorkflowDraftRequest,
    workflow_id: str,
    environment_id: str | None,
    current_graph: WorkflowGraph | None,
) -> tuple[_DraftResult | None, str | None]:
    try:
        if not _llm_configured():
            # Still try; a workflow-scoped OpenAI/Anthropic credential may be available.
            pass
        payload = await _call_llm_json(
            session,
            workflow_id=workflow_id,
            environment_id=environment_id,
            body=body,
            current_graph=current_graph,
        )
        return (
            await _result_from_llm_payload(
                session,
                payload,
                body=body,
                workflow_id=workflow_id,
                environment_id=environment_id,
            ),
            None,
        )
    except Exception as exc:  # fallback path is intentional
        return None, str(exc)


async def _fallback_result(
    session: AsyncSession,
    *,
    body: AiWorkflowDraftRequest,
    workflow_id: str,
    environment_id: str | None,
    current_graph: WorkflowGraph | None,
    fallback_reason: str | None,
) -> _DraftResult:
    if body.mode == "fix":
        result = _fallback_fix(body, current_graph)
    else:
        result = _fallback_draft(body.prompt)
    result.mode = body.mode
    result.planner = "deterministic_fallback"
    if fallback_reason:
        reason = (
            "LLM planner output was invalid"
            if "unknown node type" in fallback_reason
            or "validation" in fallback_reason
            or "graph" in fallback_reason
            else "LLM planner unavailable"
        )
        result.assumptions.insert(0, f"{reason}: {fallback_reason[:240]}")
    server_missing = await _attach_graph_credentials(
        session,
        result.graph,
        workflow_id=workflow_id,
        environment_id=environment_id,
    )
    credential_labels: set[str] = set()
    for spec in _NODE_REGISTRY.values():
        if spec.get("credential_type"):
            credential_labels.add(
                _credential_label(
                    str(spec.get("credential_type")),
                    list(spec.get("credential_keys") or []),
                )
            )
        credential_specs = spec.get("credential_specs")
        if isinstance(credential_specs, list):
            for entry in credential_specs:
                if isinstance(entry, dict) and entry.get("type"):
                    credential_labels.add(
                        _credential_label(
                            str(entry.get("type")),
                            [str(entry.get("key") or "")],
                        )
                    )
    other_missing = [item for item in result.missing_credentials if item not in credential_labels]
    result.missing_credentials = sorted(set([*other_missing, *server_missing]))
    return result


def _fallback_draft(prompt: str) -> _DraftResult:
    lower = prompt.lower()
    assumptions: list[str] = []
    missing_credentials: list[str] = []
    required_packages: list[str] = []

    nodes: list[GraphNode] = []
    edges: list[Edge] = []

    wants_github = "github" in lower or "issue" in lower or "pull request" in lower
    wants_slack = "slack" in lower
    wants_email = any(word in lower for word in ("email", "gmail", "smtp"))
    wants_openai = any(word in lower for word in ("openai", "summarize", "summary", "ai"))
    wants_anthropic = "anthropic" in lower or "claude" in lower
    wants_schedule = any(
        word in lower
        for word in ("every ", "daily", "hourly", "weekly", "schedule", "cron", "morning")
    )
    wants_http = any(word in lower for word in ("http", "api", "fetch", "request", "get from"))
    wants_code = any(word in lower for word in ("python", "custom code", "parse", "clean"))
    wants_switch = any(word in lower for word in (" if ", "route", "branch", "condition", "filter"))
    wants_webhook = "webhook" in lower or "when" in lower or wants_github

    if wants_schedule:
        start_type = "schedule_trigger"
        start_params = {"cron": "0 9 * * *", "timezone": "UTC"}
    elif wants_webhook:
        start_type = "webhook_trigger"
        start_params = {
            "http_method": "POST",
            "path": _slug(prompt, "incoming-event"),
            "response_mode": "On Received",
            "response_code": 200,
        }
    else:
        start_type = "manual_trigger"
        start_params = {}
    nodes.append(_node("trigger", start_type, 0, 0, start_params))
    previous = "trigger"

    if wants_github and "create" not in lower:
        assumptions.append(
            "GitHub event intake is modeled as a webhook trigger; configure "
            "GitHub to call this URL."
        )

    if wants_http:
        nodes.append(
            _node(
                "fetch_api",
                "http_request",
                280,
                0,
                {
                    "url": "https://api.example.com/data",
                    "method": "GET",
                    "headers": {},
                    "query": {},
                    "body": None,
                    "timeout_seconds": 30,
                },
            )
        )
        edges.append(_edge(previous, "fetch_api"))
        previous = "fetch_api"

    if wants_switch:
        nodes.append(
            _node(
                "route",
                "switch",
                560 if wants_http else 280,
                0,
                {"rules": {"matched": "{{ $json.status == 'ok' }}"}},
            )
        )
        edges.append(_edge(previous, "route"))
        previous = "route"

    if wants_code:
        nodes.append(
            _node(
                "transform", "code", 560 if not wants_switch else 840, 0, {"code": "output = input"}
            )
        )
        edges.append(_edge(previous, "transform"))
        previous = "transform"

    if wants_openai or wants_anthropic:
        provider = "anthropic" if wants_anthropic else "openai"
        params = {
            "credentials": "",
            "provider": provider,
            "model": "claude-3-5-haiku-latest" if wants_anthropic else "gpt-4.1-mini",
            "system": "You summarize workflow input clearly for an operations user.",
            "prompt": "Summarize this event and include the most important next action.",
            "temperature": 0.2,
            "max_tokens": 1024 if wants_anthropic else 500,
            "response_format": "text",
            "timeout_seconds": 75,
        }
        ai_type = "ai_chat"
        nodes.append(_node("summarize", ai_type, 280 + 280 * (len(nodes) - 1), 0, params))
        edges.append(_edge(previous, "summarize"))
        previous = "summarize"
        missing_credentials.append("LLM provider credential")

    if wants_slack:
        slack_params: dict = {
            "resource": "message",
            "operation": "send",
            "credentials": "",
            "channel": "#alerts",
            "text": "{{ $json }}",
            "blocks": None,
            "thread_ts": "",
        }
        nodes.append(
            _node(
                "notify_slack",
                "slack",
                280 + 280 * (len(nodes) - 1),
                0,
                slack_params,
            )
        )
        edges.append(_edge(previous, "notify_slack"))
        previous = "notify_slack"
        missing_credentials.append("Slack bot token")

    if wants_email:
        smtp_params: dict = {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "",
            "password": "",
            "use_tls": True,
            "from_email": "",
            "to_email": "",
            "subject": "Noodle workflow alert",
            "body": "{{ $json }}",
        }
        node_id = "send_email"
        nodes.append(
            _node(
                node_id,
                "smtp_send_email",
                280 + 280 * (len(nodes) - 1),
                180 if wants_slack else 0,
                smtp_params,
            )
        )
        edges.append(
            _edge(
                previous
                if not wants_slack
                else "summarize"
                if any(n.id == "summarize" for n in nodes)
                else previous,
                node_id,
            )
        )
        missing_credentials.append("SMTP username/password or Gmail app password")

    if wants_github and "create" in lower and "issue" in lower:
        gh_params: dict = {
            "credentials": "",
            "repo": "owner/repo",
            "title": "{{ $json.title }}",
            "body": "{{ $json.body }}",
            "labels": None,
        }
        nodes.append(
            _node(
                "create_issue",
                "github_create_issue_v2",
                280 + 280 * (len(nodes) - 1),
                0,
                gh_params,
            )
        )
        edges.append(_edge(previous, "create_issue"))
        missing_credentials.append("GitHub token")

    if len(nodes) == 1:
        nodes.append(
            _node(
                "shape_data",
                "edit_fields",
                280,
                0,
                {"fields": {"message": "{{ $json }}"}, "keep_only_set": False},
            )
        )
        edges.append(_edge("trigger", "shape_data"))
        assumptions.append(
            "The prompt did not name a known integration, so the draft starts "
            "with a trigger and editable data-shaping node."
        )

    graph = WorkflowGraph(nodes=nodes, edges=edges)
    return _DraftResult(
        graph=graph,
        assumptions=assumptions,
        missing_credentials=sorted(set(missing_credentials)),
        required_packages=required_packages,
        explanation=(
            "Generated a normal editable draft graph from existing Noodle nodes. "
            "Review credentials and parameters before publishing."
        ),
        change_summary=[f"Created {len(nodes)} nodes and {len(edges)} connections."],
        confidence="medium",
        focus_node_id=nodes[-1].id if nodes else None,
    )


def _fallback_fix(
    body: AiWorkflowDraftRequest, current_graph: WorkflowGraph | None
) -> _DraftResult:
    if current_graph is None:
        result = _fallback_draft(body.prompt)
        result.assumptions.append(
            "No valid current graph was supplied, so fallback generated a replacement draft."
        )
        result.confidence = "low"
        return result

    graph = WorkflowGraph.model_validate(current_graph.model_dump())
    failed_id = body.failed_node_id
    failed_node = next((node for node in graph.nodes if node.id == failed_id), None)
    assumptions: list[str] = []
    change_summary: list[str] = []
    confidence = "low"

    error = (body.error or "").lower()
    if body.fix_strategy == "replacement":
        replacement = _fallback_draft(body.prompt or body.error or "Fix failed workflow")
        replacement.mode = "fix"
        replacement.confidence = "low"
        replacement.assumptions.append(
            "Fallback replacement proposal was generated without LLM reasoning."
        )
        replacement.change_summary.insert(
            0, "Proposed a replacement draft because replacement strategy was selected."
        )
        return replacement

    if (
        failed_node is not None
        and failed_node.type == "code"
        and any(term in error for term in ("input", "none", "keyerror", "nameerror"))
    ):
        existing = str(failed_node.params.get("code") or "output = input")
        if "try:" not in existing:
            failed_node.params["code"] = (
                "# AI fallback guard: tolerate missing or unexpected input.\n"
                "try:\n"
                "    output = input if input is not None else {}\n"
                "except Exception as exc:\n"
                "    output = {'error': str(exc), 'input': input}\n"
            )
            change_summary.append("Updated the failed code node with a defensive input guard.")
            assumptions.append(
                "Fallback inferred the failure may be caused by missing or malformed input."
            )
            confidence = "medium"
    elif failed_node is not None:
        assumptions.append(
            "Fallback could not infer a safe automatic repair; review the focused node manually."
        )
    else:
        assumptions.append(
            "The failed node was not found in the supplied graph; no graph changes were made."
        )

    if not change_summary:
        change_summary.append("No automatic graph changes were made by fallback repair.")

    return _DraftResult(
        graph=graph,
        assumptions=assumptions,
        explanation="Generated a conservative Fix with AI fallback result. Review before applying.",
        mode="fix",
        change_summary=change_summary,
        confidence=confidence,
        focus_node_id=failed_id,
    )


async def build_workflow_draft(
    session: AsyncSession,
    workflow_id: str,
    prompt_or_body: str | AiWorkflowDraftRequest,
) -> AiWorkflowDraftResponse:
    body = (
        AiWorkflowDraftRequest(prompt=prompt_or_body)
        if isinstance(prompt_or_body, str)
        else prompt_or_body
    )
    workflow = await session.get(Workflow, workflow_id)
    environment_id = workflow.environment_id if workflow is not None else None
    current_graph = _current_or_workflow_graph(body, workflow)

    # Multi-turn refinement — bypass the normal LLM/fallback pipeline
    if body.mode == "refine":
        return await _refine_workflow(
            prompt=body.prompt,
            current_graph=current_graph.model_dump() if current_graph is not None else None,
            conversation_history=body.conversation_history or [],
            target_node_ids=body.target_node_ids or [],
        )

    llm_result, fallback_reason = await _try_llm_result(
        session,
        body=body,
        workflow_id=workflow_id,
        environment_id=environment_id,
        current_graph=current_graph,
    )
    result = llm_result or await _fallback_result(
        session,
        body=body,
        workflow_id=workflow_id,
        environment_id=environment_id,
        current_graph=current_graph,
        fallback_reason=fallback_reason,
    )

    return AiWorkflowDraftResponse(
        workflow_id=workflow_id,
        graph=result.graph,
        assumptions=result.assumptions,
        missing_credentials=sorted(set(result.missing_credentials)),
        required_packages=result.required_packages,
        explanation=result.explanation,
        mode=result.mode,
        change_summary=result.change_summary,
        confidence=result.confidence,
        focus_node_id=result.focus_node_id,
        planner=result.planner,
    )


# ---------------------------------------------------------------------------
# Feature 1: Standalone Explain Workflow
# ---------------------------------------------------------------------------


async def explain_workflow(graph: dict) -> dict:
    """Generate a natural-language explanation of a workflow graph.

    Uses the LLM if available, falls back to a template-based explanation
    built from the node registry metadata and graph structure.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return {
            "explanation": "This workflow is empty — it has no nodes yet.",
            "nodes_summary": [],
            "data_flow": "No data flow.",
            "assumptions": [],
        }

    # Build adjacency for data-flow analysis
    node_map = {n["id"]: n for n in nodes if isinstance(n, dict)}
    sources: defaultdict = defaultdict(list)
    for e in (e for e in edges if isinstance(e, dict)):
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src and tgt:
            sources[tgt].append(src)

    # Try LLM first
    try:
        llm_result = await _call_llm_simple(
            _build_explain_prompt(nodes, edges, node_map),
            system="You explain workflow graphs clearly and concisely.",
        )
        if llm_result and isinstance(llm_result, dict):
            return {
                "explanation": str(llm_result.get("explanation", "")),
                "nodes_summary": llm_result.get("nodes_summary", []),
                "data_flow": str(llm_result.get("data_flow", "")),
                "assumptions": llm_result.get("assumptions", []),
            }
    except Exception:  # noqa: BLE001
        pass

    # Deterministic fallback
    return _fallback_explain(nodes, edges, node_map, sources)


def _build_explain_prompt(nodes: list, edges: list, node_map: dict) -> str:
    node_descriptions = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id", "")
        ntype = n.get("type", "")
        ninfo = _NODE_REGISTRY.get(ntype, {})
        node_descriptions.append(
            f"  - {nid} ({ntype}): {ninfo.get('description', 'No description')}"
        )
    edge_descriptions = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        edge_descriptions.append(
            f"  {e.get('source','')} → {e.get('target','')}"
        )
    return (
        "Explain this workflow graph in clear English. "
        "Describe what triggers it, what each node does, "
        "how data flows between nodes, and any assumptions.\n\n"
        "Nodes:\n" + "\n".join(node_descriptions) + "\n\n"
        "Edges:\n" + "\n".join(edge_descriptions) + "\n\n"
        "Respond as JSON: {explanation, nodes_summary: [{id, type, purpose}], "
        "data_flow, assumptions: [string]}"
    )


def _fallback_explain(
    nodes: list,
    edges: list,
    node_map: dict,
    sources: dict[str, list[str]],
) -> dict:
    """Template-based explanation when no LLM is available."""
    trigger_nodes = []
    action_nodes = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        ntype = n.get("type", "")
        ninfo = _NODE_REGISTRY.get(ntype, {})
        name = n.get("name") or ninfo.get("name") or ntype
        if "trigger" in ntype:
            trigger_nodes.append(name)
        else:
            action_nodes.append(name)

    explanations_parts = []
    if trigger_nodes:
        explanations_parts.append(
            f"This workflow is triggered by: {', '.join(trigger_nodes)}."
        )
    if action_nodes:
        explanations_parts.append(
            f"It processes data through: {', '.join(action_nodes)}."
        )

    explanation = " ".join(explanations_parts) if explanations_parts else "This workflow has no recognizable trigger or action nodes."

    data_flow_parts = []
    for tgt_id, src_ids in sources.items():
        for source_id in src_ids:
            src_node = node_map.get(source_id, {})
            tgt_node = node_map.get(tgt_id, {})
            data_flow_parts.append(
                f"Data flows from {src_node.get('name', source_id)} to {tgt_node.get('name', tgt_id)}"
            )

    return {
        "explanation": explanation,
        "nodes_summary": [
            {
                "id": n.get("id", ""),
                "type": n.get("type", ""),
                "purpose": _NODE_REGISTRY.get(n.get("type", ""), {}).get("description", "Unknown"),
            }
            for n in nodes if isinstance(n, dict)
        ],
        "data_flow": ". ".join(data_flow_parts) if data_flow_parts else "No edges defined.",
        "assumptions": [],
    }


# ---------------------------------------------------------------------------
# Feature 2: Multi-Turn AI Refinement
# ---------------------------------------------------------------------------


async def _refine_workflow(
    prompt: str,
    current_graph: dict | None,
    conversation_history: list[dict],
    target_node_ids: list[str],
) -> AiWorkflowDraftResponse:
    """Refine specific parts of an existing workflow based on conversation."""
    if not current_graph or not current_graph.get("nodes"):
        # No existing graph — generate a fresh draft from the prompt
        fallback = _fallback_draft(prompt)
        return AiWorkflowDraftResponse(
            workflow_id="",
            graph=fallback.graph,
            mode="refine",
            planner="llm",
            explanation="No existing graph to refine; generated a fresh draft.",
            change_summary=fallback.change_summary,
            confidence=fallback.confidence,
            missing_credentials=fallback.missing_credentials,
            required_packages=fallback.required_packages,
        )

    # Try LLM first
    try:
        context = {
            "current_graph": current_graph,
            "node_registry": {k: v for k, v in _NODE_REGISTRY.items()},
            "target_node_ids": target_node_ids,
        }
        llm_result = await _call_llm_simple(
            _build_refine_prompt(prompt, context, conversation_history),
            system="You modify specific parts of workflow graphs. Only change what the user asks.",
        )
        if llm_result and isinstance(llm_result, dict) and "graph" in llm_result:
            graph = _coerce_graph(llm_result["graph"])
            return AiWorkflowDraftResponse(
                workflow_id="",
                graph=graph,
                mode="refine",
                planner="llm",
                explanation=llm_result.get("explanation", "Workflow refined."),
                change_summary=llm_result.get("change_summary", []) if isinstance(llm_result.get("change_summary"), list) else [str(llm_result.get("change_summary", ""))] if llm_result.get("change_summary") else [],
                confidence=llm_result.get("confidence", "medium"),
                missing_credentials=llm_result.get("missing_credentials", []),
                required_packages=llm_result.get("required_packages", []),
            )
    except Exception:  # noqa: BLE001
        pass

    # Fallback: apply basic keyword-based modifications
    return _fallback_refine(prompt, current_graph, target_node_ids)


def _build_refine_prompt(prompt: str, context: dict, history: list[dict]) -> str:
    sanitized_graph = _strip_credential_refs(context["current_graph"])
    return (
        f"Modify this workflow graph based on the user's request.\n\n"
        f"USER REQUEST: {prompt}\n\n"
        f"CURRENT GRAPH: {json.dumps(sanitized_graph)}\n\n"
        f"AVAILABLE NODE TYPES: {json.dumps(list(context['node_registry'].keys()))}\n\n"
        f"TARGET NODE IDS (only modify these): {context['target_node_ids']}\n\n"
        f"CONVERSATION HISTORY: {json.dumps(history[-5:])}\n\n"
        f"Return JSON: {{graph: {{nodes, edges}}, explanation, change_summary, "
        f"confidence, missing_credentials, required_packages}}"
    )


def _fallback_refine(
    prompt: str,
    current_graph: dict,
    target_node_ids: list[str],
) -> AiWorkflowDraftResponse:
    """Basic keyword-based modifications when no LLM is available."""
    nodes = list(current_graph.get("nodes", []))
    changes = []
    prompt_lower = prompt.lower()

    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        if target_node_ids and node.get("id") not in target_node_ids:
            continue
        params = dict(node.get("params", {}))
        # Simple keyword-based param changes
        if "channel" in prompt_lower and "slack" in str(node.get("type", "")).lower():
            match = re.search(r'#[\w-]+', prompt)
            if match:
                params["channel"] = match.group(0)
                changes.append(f"Updated Slack channel to {match.group(0)}")
        if "email" in prompt_lower and "to" in prompt_lower:
            match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', prompt)
            if match:
                params["to"] = match.group(0)
                changes.append(f"Updated email recipient to {match.group(0)}")
        nodes[i] = {**node, "params": params}

    # Rebuild edges, preserving the existing ones
    raw_edges = current_graph.get("edges", [])
    edges = [Edge(**e) if isinstance(e, dict) else e for e in raw_edges]
    # Rebuild nodes
    rebuilt_nodes = []
    for n in nodes:
        if isinstance(n, dict):
            rebuilt_nodes.append(GraphNode(**n))
        else:
            rebuilt_nodes.append(n)

    graph = WorkflowGraph(
        nodes=rebuilt_nodes,
        edges=edges,
    )
    return AiWorkflowDraftResponse(
        workflow_id="",
        graph=graph,
        mode="refine",
        planner="deterministic_fallback",
        explanation="Applied keyword-based refinements.",
        change_summary=changes if changes else ["No changes detected."],
        confidence="low",
        missing_credentials=[],
        required_packages=[],
    )


# ---------------------------------------------------------------------------
# Feature 3: Workflow Test Generation
# ---------------------------------------------------------------------------


async def generate_tests(graph: dict) -> list[dict]:
    """Generate test cases for a workflow graph.

    Returns a list of test objects with input_data, expected_outputs, and assertions.
    """
    nodes = graph.get("nodes", [])
    if not nodes:
        return []

    # Find the trigger node to base test data on
    trigger = None
    for n in nodes:
        if isinstance(n, dict) and "trigger" in n.get("type", ""):
            trigger = n
            break

    try:
        llm_result = await _call_llm_simple(
            _build_test_gen_prompt(graph, trigger),
            system="You generate test cases for workflow graphs. Be creative but realistic.",
        )
        if llm_result and isinstance(llm_result, dict) and "tests" in llm_result:
            return llm_result["tests"]
    except Exception:  # noqa: BLE001
        pass

    # Fallback: generate basic test for trigger nodes
    return _fallback_generate_tests(graph, trigger)


def _strip_credential_refs(obj: Any) -> Any:
    """Recursively strip credential references from a value before sending to LLM."""
    if isinstance(obj, dict):
        if obj.get("__noodle_credential__") or obj.get("credential_id"):
            return {"__credential_placeholder__": True}
        return {k: _strip_credential_refs(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_credential_refs(v) for v in obj]
    return obj


def _build_test_gen_prompt(graph: dict, trigger: dict | None) -> str:
    node_info = []
    for n in graph.get("nodes", []):
        if isinstance(n, dict):
            sanitized_params = _strip_credential_refs(n.get("params", {}))
            ninfo = _NODE_REGISTRY.get(n.get("type", ""), {})
            node_info.append(
                f"  {n.get('id')} ({n.get('type')}): params={sanitized_params}"
            )
    trigger_params = _strip_credential_refs(trigger.get("params", {})) if trigger else {}
    trigger_info = f"Trigger: {trigger.get('type')} with params {trigger_params}" if trigger else "No trigger node found"
    return (
        f"Generate 3 test cases for this workflow graph.\n\n"
        f"{trigger_info}\n\n"
        f"All nodes:\n" + "\n".join(node_info) + "\n\n"
        f"Respond as JSON: {{tests: [{{name, input_data: {{}}, expected_outputs: {{}}, assertions: [string]}}]}}"
    )


def _fallback_generate_tests(graph: dict, trigger: dict | None) -> list[dict]:
    """Basic test generation without LLM."""
    tests = []
    if trigger:
        trigger_type = trigger.get("type", "")
        if "webhook" in trigger_type:
            tests.append({
                "name": "Valid webhook payload",
                "input_data": {"body": {"test": True}, "headers": {"Content-Type": "application/json"}},
                "expected_outputs": {},
                "assertions": ["run status should be success"],
            })
        elif "schedule" in trigger_type:
            tests.append({
                "name": "Scheduled trigger execution",
                "input_data": {"timestamp": "2026-01-01T00:00:00Z"},
                "expected_outputs": {},
                "assertions": ["run status should be success"],
            })
        else:
            tests.append({
                "name": "Manual trigger with default input",
                "input_data": {"data": "test"},
                "expected_outputs": {},
                "assertions": ["run.status == 'success'"],
            })
    else:
        tests.append({
            "name": "Default execution",
            "input_data": {},
            "expected_outputs": {},
            "assertions": ["run.status == 'success'"],
        })
    return tests
