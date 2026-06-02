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
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential, Workflow
from app.schemas import AiWorkflowDraftRequest, AiWorkflowDraftResponse
from app.services.credentials import CREDENTIAL_REF_MARKER, credential_ref
from app.services.crypto import decrypt_data
from noodle.models import Edge, GraphNode, Position, WorkflowGraph

_ALLOWED_NODE_TYPES = {
    "manual_trigger",
    "schedule_trigger",
    "webhook_trigger",
    "switch",
    "edit_fields",
    "code",
    "http_request",
    "slack_send_message",
    "smtp_send_email",
    "notion_create_page",
    "github_get_repo",
    "github_create_issue",
    "postgres_query",
    "mysql_query",
    "s3_put_object",
    "s3_get_object",
    "openai_chat",
    "anthropic_message",
    "ai_prompt_template",
    "ai_chat",
    "ai_structured_output",
    "ai_text_chunk",
    "ai_batch_embeddings",
    "ai_dataset_map",
    "ai_vector_retriever",
    "ai_rag_answer",
    "ai_tool",
    "ai_agent",
    "ai_moderation_guard",
    "ai_vision_analyze",
    "ai_image_generate",
    "airtable_list_records",
    "airtable_create_record",
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
    "slack_send_message": {
        "name": "Slack Send Message",
        "params": ["bot_token", "channel", "text", "blocks", "thread_ts"],
        "credential_type": "slack_bot",
        "credential_keys": ["bot_token"],
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
    "github_get_repo": {
        "name": "GitHub Get Repository",
        "params": ["repo", "token"],
        "credential_type": "github",
        "credential_keys": ["token"],
    },
    "github_create_issue": {
        "name": "GitHub Create Issue",
        "params": ["repo", "token", "title", "body", "labels"],
        "credential_type": "github",
        "credential_keys": ["token"],
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
    "ai_agent": {
        "name": "AI Agent",
        "params": ["credentials", "provider", "model", "system", "task", "max_steps"],
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
    "notion_create_page": {
        "name": "Notion Create Page",
        "params": ["token", "parent_id", "properties", "children"],
    },
    "postgres_query": {"name": "Postgres Query", "params": ["dsn", "query", "params"]},
    "mysql_query": {"name": "MySQL Query", "params": ["dsn", "query", "params"]},
    "s3_put_object": {"name": "S3 Put Object", "params": ["bucket", "key", "body", "content_type"]},
    "s3_get_object": {"name": "S3 Get Object", "params": ["bucket", "key"]},
    "airtable_list_records": {
        "name": "Airtable List Records",
        "params": ["api_key", "base_id", "table"],
    },
    "airtable_create_record": {
        "name": "Airtable Create Record",
        "params": ["api_key", "base_id", "table", "fields"],
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
                {"type": cred_type, "param": key, "key": key}
                for key in keys
                if cred_type and key
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
) -> tuple[str, str, str]:
    provider = (os.getenv("NOODLE_AI_PROVIDER") or "").strip().lower()
    model = os.getenv("NOODLE_AI_MODEL") or ""
    api_key = ""

    if provider == "anthropic" or os.getenv("ANTHROPIC_API_KEY"):
        provider = "anthropic"
        api_key = os.getenv("ANTHROPIC_API_KEY") or ""
        model = model or "claude-3-5-haiku-latest"
    elif provider == "openai" or os.getenv("OPENAI_API_KEY"):
        provider = "openai"
        api_key = os.getenv("OPENAI_API_KEY") or ""
        model = model or "gpt-4.1-mini"

    if not api_key:
        for cred_type, default_model in (
            ("openai", "gpt-4.1-mini"),
            ("anthropic", "claude-3-5-haiku-latest"),
        ):
            cred = await _find_credential(
                session,
                cred_type,
                workflow_id=workflow_id,
                environment_id=environment_id,
            )
            if cred is None:
                continue
            data = decrypt_data(cred.encrypted_data)
            key = str(data.get("api_key") or "")
            # Stored credentials are allowed as planner credentials only when
            # they look like real provider keys. Test/demo placeholders remain
            # normal node credentials and do not trigger network planner calls.
            if len(key) >= 16:
                return cred_type, model or default_model, key

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
            "bot_token": "",
            "channel": "#alerts",
            "text": "{{ $json }}",
            "blocks": None,
            "thread_ts": "",
        }
        nodes.append(
            _node(
                "notify_slack", "slack_send_message", 280 + 280 * (len(nodes) - 1), 0, slack_params
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
            "repo": "owner/repo",
            "token": "",
            "title": "{{ $json.title }}",
            "body": "{{ $json.body }}",
            "labels": None,
        }
        nodes.append(
            _node("create_issue", "github_create_issue", 280 + 280 * (len(nodes) - 1), 0, gh_params)
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
