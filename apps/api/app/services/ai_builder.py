"""Deterministic v1 workflow draft builder.

This is intentionally not a hidden agent executor. It turns a natural-language
request into a normal editable graph using existing node ids and conservative
defaults. A real LLM-backed builder can replace the planner later while keeping
the same validated graph contract.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential, Workflow
from app.schemas import AiWorkflowDraftResponse
from app.services.credentials import credential_ref
from noodle.models import Edge, GraphNode, Position, WorkflowGraph


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


async def _find_credential(
    session: AsyncSession,
    cred_type: str,
    *,
    workflow_id: str,
    environment_id: str | None,
) -> Credential | None:
    """Return the most-specific visible credential for this type, or None.

    Specificity order: workflow > environment > global. Only attaches a ref
    when exactly one credential matches at the most specific visible tier —
    otherwise the user picks via the inspector and the field stays in
    ``missing_credentials``.
    """
    stmt = select(Credential).where(Credential.type == cred_type)
    rows = (await session.scalars(stmt)).all()
    if not rows:
        return None

    for scope, predicate in (
        (
            "workflow",
            lambda c: c.scope == "workflow" and c.workflow_id == workflow_id,
        ),
        (
            "environment",
            lambda c: (
                c.scope == "environment"
                and environment_id is not None
                and c.environment_id == environment_id
            ),
        ),
        ("global", lambda c: c.scope == "global"),
    ):
        del scope  # name-only readability
        matches = [c for c in rows if predicate(c)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Ambiguous at this tier — leave the field empty and let the
            # inspector picker resolve it. Don't fall through to a less
            # specific tier or we'd silently pick the wrong one.
            return None
    return None


def _attach_credential(
    params: dict,
    keys: list[str],
    cred: Credential | None,
) -> None:
    if cred is None:
        return
    for key in keys:
        params[key] = credential_ref(cred.id, key)


async def build_workflow_draft(
    session: AsyncSession, workflow_id: str, prompt: str
) -> AiWorkflowDraftResponse:
    lower = prompt.lower()
    assumptions: list[str] = []
    missing_credentials: list[str] = []
    required_packages: list[str] = []

    workflow = await session.get(Workflow, workflow_id)
    environment_id = workflow.environment_id if workflow is not None else None

    nodes: list[GraphNode] = []
    edges: list[Edge] = []

    wants_github = "github" in lower or "issue" in lower or "pull request" in lower
    wants_slack = "slack" in lower
    wants_email = any(word in lower for word in ("email", "gmail", "smtp"))
    wants_openai = any(word in lower for word in ("openai", "summarize", "summary", "ai"))
    wants_anthropic = "anthropic" in lower or "claude" in lower
    wants_webhook = "webhook" in lower or "when" in lower or wants_github

    start_type = "webhook_trigger" if wants_webhook else "manual_trigger"
    start_params = (
        {
            "http_method": "POST",
            "path": _slug(prompt, "incoming-event"),
            "response_mode": "On Received",
            "response_code": 200,
        }
        if start_type == "webhook_trigger"
        else {}
    )
    nodes.append(_node("trigger", start_type, 0, 0, start_params))
    previous = "trigger"

    if wants_github and "create" not in lower:
        assumptions.append(
            "GitHub event intake is modeled as a webhook trigger; configure "
            "the GitHub webhook to call this URL."
        )

    if wants_openai or wants_anthropic:
        ai_type = "anthropic_message" if wants_anthropic else "openai_chat"
        cred_type = "anthropic" if wants_anthropic else "openai"
        params = (
            {
                "api_key": "",
                "model": "claude-3-5-haiku-latest",
                "system": "You summarize workflow input clearly for an operations user.",
                "prompt": "Summarize this event and include the most important next action.",
                "max_tokens": 1024,
                "temperature": 0.2,
            }
            if wants_anthropic
            else {
                "api_key": "",
                "model": "gpt-4.1-mini",
                "system": "You summarize workflow input clearly for an operations user.",
                "prompt": "Summarize this event and include the most important next action.",
                "temperature": 0.2,
                "max_tokens": 500,
            }
        )
        cred = await _find_credential(
            session,
            cred_type,
            workflow_id=workflow_id,
            environment_id=environment_id,
        )
        _attach_credential(params, ["api_key"], cred)
        nodes.append(_node("summarize", ai_type, 280, 0, params))
        edges.append(_edge(previous, "summarize"))
        previous = "summarize"
        if cred is None:
            missing_credentials.append(
                "OpenAI API key" if ai_type == "openai_chat" else "Anthropic API key"
            )

    if wants_slack:
        slack_params: dict = {
            "bot_token": "",
            "channel": "",
            "text": "",
            "blocks": None,
            "thread_ts": "",
        }
        cred = await _find_credential(
            session,
            "slack_bot",
            workflow_id=workflow_id,
            environment_id=environment_id,
        )
        _attach_credential(slack_params, ["bot_token"], cred)
        nodes.append(_node("notify_slack", "slack_send_message", 560, 0, slack_params))
        edges.append(_edge(previous, "notify_slack"))
        previous = "notify_slack"
        if cred is None:
            missing_credentials.append("Slack bot token")

    if wants_email:
        y = 180 if wants_slack else 0
        smtp_params: dict = {
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "",
            "password": "",
            "use_tls": True,
            "from_email": "",
            "to_email": "",
            "subject": "Noodle workflow alert",
            "body": "",
        }
        cred = await _find_credential(
            session,
            "smtp",
            workflow_id=workflow_id,
            environment_id=environment_id,
        )
        _attach_credential(smtp_params, ["username", "password"], cred)
        nodes.append(_node("send_email", "smtp_send_email", 560, y, smtp_params))
        edges.append(_edge(previous if not wants_slack else "summarize", "send_email"))
        if cred is None:
            missing_credentials.append(
                "SMTP username/password or Gmail app password"
            )

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

    if wants_github and "create" in lower and "issue" in lower:
        gh_params: dict = {
            "repo": "",
            "token": "",
            "title": "",
            "body": "",
            "labels": None,
        }
        cred = await _find_credential(
            session,
            "github",
            workflow_id=workflow_id,
            environment_id=environment_id,
        )
        _attach_credential(gh_params, ["token"], cred)
        nodes.append(_node("create_issue", "github_create_issue", 560, 0, gh_params))
        edges.append(_edge(previous, "create_issue"))
        if cred is None:
            missing_credentials.append("GitHub token")

    graph = WorkflowGraph(nodes=nodes, edges=edges)
    return AiWorkflowDraftResponse(
        workflow_id=workflow_id,
        graph=graph,
        assumptions=assumptions,
        missing_credentials=sorted(set(missing_credentials)),
        required_packages=required_packages,
        explanation=(
            "Generated a normal editable draft graph from existing Noodle nodes. "
            "Review credentials and parameters before publishing."
        ),
    )
