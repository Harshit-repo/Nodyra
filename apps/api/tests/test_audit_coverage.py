"""Audit-coverage gate (F-05).

Audit logs are sold as an Enterprise feature. Shipping that tier while
privileged mutations — draining the queue, replaying the dead-letter queue,
installing third-party node packages, rewiring runner pools — leave no trace is
not defensible, and the gap was invisible because nothing compared the route
table against the audit call sites.

This test is that comparison. Every state-changing route either records an audit
event or appears in ``UNAUDITED_BY_DESIGN`` with a stated reason. Adding a new
privileged endpoint without either fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTERS_DIR = Path(__file__).resolve().parents[1] / "app" / "routers"

MUTATING_METHODS = {"post", "put", "patch", "delete"}

# Routes that legitimately record nothing, each with the reason it is exempt.
# Keys are "<router module>:<handler function>". Keep this list short and
# justified — it is the escape hatch, not the norm.
UNAUDITED_BY_DESIGN: dict[str, str] = {
    # ── Recorded as the run they create ─────────────────────────────────────
    # A run is itself a durable, queryable, retained record with its actor,
    # trigger and graph version on it. Mirroring every execution into the audit
    # log would bury the security-relevant entries under execution telemetry.
    # Cancellation and approval decisions ARE audited — they are operator
    # interventions, not executions.
    "runs:run_workflow": "recorded as the run it creates",
    "runs:rerun_run": "recorded as the run it creates",
    "runs:replay_workflow_run": "recorded as the run it creates",
    "runs:retry_from_failure": "recorded as the run it creates",
    "workflows:test_workflow_node": "ephemeral single-node test, no stored state",
    "workflows:run_workflow_check": "recorded as the run it creates",
    "workflows:run_workflow_checks": "recorded as the run it creates",
    "chat:chat_turn": "recorded as the run the turn triggers",
    "chat:chat_turn_stream": "recorded as the run the turn triggers",
    "agentic_build:agentic_build": "graph edits land as workflow revisions",

    # ── Versioned content, not privileged configuration ─────────────────────
    # Workflow graphs, folders and pinned data are user content whose full
    # history is already reconstructable from workflow_revisions /
    # workflow_versions. Auditing every keystroke-level save would produce
    # noise without adding accountability the revision table lacks.
    "workflows:update_workflow": "graph history lives in workflow_revisions",
    "workflows:patch_workflow": "graph history lives in workflow_revisions",
    "workflows:save_workflow_checks": "graph history lives in workflow_revisions",
    "workflows:delete_workflow_check": "graph history lives in workflow_revisions",
    "folders:create_folder": "organisational metadata, no access implications",
    "folders:rename_folder": "organisational metadata, no access implications",
    "folders:delete_folder": "organisational metadata; workflows are not deleted",
    "pinned:upsert_pinned": "editor scratch data scoped to one workflow",
    "pinned:remove_pinned": "editor scratch data scoped to one workflow",

    # ── Stateless helpers (POST for a request body, not for a mutation) ─────
    "code_modules:format_code": "pure function over the submitted source",
    "code_modules:lint_code": "pure function over the submitted source",
    "code_modules:starter_graph": "returns a template, stores nothing",
    "code_modules:generate_node_from_description": "returns generated source, stores nothing",
    "expressions:preview_expression": "evaluates a preview, stores nothing",
    "export:preview_workflow_import": "dry-run diff, stores nothing",
    "workflows:explain_workflow": "read-only explanation of an existing graph",
    "workflows:generate_workflow_tests": "returns generated tests, stores nothing",
    "credentials:test_credential_draft": "tests an unsaved draft; no stored secret is read",

    # ── Data-plane and transport paths ──────────────────────────────────────
    # High-volume paths authenticated by their own token. The artifact rows and
    # MCP tool calls they produce are audited at their own layer — mcp/tools.py
    # records every tool invocation, and artifact deletion is audited.
    "runner_pools:upload_artifact": "runner data plane; the artifact row is the record",
    "artifacts:upload_artifact": "creates user content; deletion is what is audited",
    "artifacts:query_artifact": "read path that uses POST for its query body",
    "mcp:mcp_post": "transport envelope; mcp/tools.py audits each tool call",
    "mcp:mcp_delete": "transport envelope; closes an MCP session",
    "mcp_gateway:unsupported_stream": "stateless transport rejection; always returns 405 and changes no state",
    # This specific proxy records execution intent, denial and observed outcome
    # in MCPGatewayInvocation via services/mcp_gateway.py, rather than adding a
    # second AuditEvent. test_mcp_gateway.py asserts denied calls and executed
    # calls create that durable ledger. tools/list is a read-only POST variant.
    "mcp_gateway:proxy": "managed tool calls use the MCPGatewayInvocation ledger; discovery is read-only",

    # ── Auth flows that establish no session ────────────────────────────────
    # login, logout and SSO sign-in ARE audited. These three are not: they
    # either predate a principal or mint a short-lived token for a session that
    # was already authenticated and recorded.
    "auth:verify_email": "consumes a mailed token; the registration is audited",
    "auth:resend_verification": "rate-limited email resend, no state change",
    "auth:ws_ticket": "short-lived ticket for an already-authenticated session",

    # ── Wildcards ───────────────────────────────────────────────────────────
    "internal:*": "service-to-service, authenticated by the internal token",
    "webhooks:*": "external ingress; recorded as the run it triggers",
    "provider_webhooks:*": "external ingress; recorded as the run it triggers",
    "chat_public:*": "public chat ingress; recorded as the run it triggers",
    "health:*": "probes change no state",
    "metrics:*": "read-only scrape endpoint",
}


def _router_sources() -> dict[str, str]:
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(ROUTERS_DIR.glob("*.py"))
        if path.stem != "__init__"
    }


def _is_route_decorator(node: ast.expr) -> bool:
    """Match ``@router.post(...)`` / ``@router.delete(...)`` and friends."""
    call = node if isinstance(node, ast.Call) else None
    func = call.func if call else node
    return (
        isinstance(func, ast.Attribute)
        and func.attr in MUTATING_METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id == "router"
    )


def _declares_recorder(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the handler injects an ``AuditRecorder`` dependency.

    Checked by annotation, not by parameter name, so a variable coincidentally
    called ``audit`` cannot satisfy the gate.
    """
    args = fn.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        annotation = arg.annotation
        name = (
            annotation.id
            if isinstance(annotation, ast.Name)
            else annotation.attr
            if isinstance(annotation, ast.Attribute)
            else ""
        )
        if name == "AuditRecorder":
            return True
    return False


def _calls_log_audit(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if name == "log_audit":
                return True
    return False


def _mutating_routes() -> list[tuple[str, str, bool]]:
    """(module, handler, records_audit) for every state-changing route."""
    found: list[tuple[str, str, bool]] = []
    for module, source in _router_sources().items():
        tree = ast.parse(source)
        # Helpers a handler delegates to live at module level; treat an audit
        # call anywhere in a helper the handler calls as coverage by resolving
        # module-level functions that themselves log.
        logging_helpers = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and _calls_log_audit(node)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not any(_is_route_decorator(d) for d in node.decorator_list):
                continue
            audited = _calls_log_audit(node) or _declares_recorder(node) or any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id in logging_helpers
                for c in ast.walk(node)
            )
            found.append((module, node.name, audited))
    return found


def _is_exempt(module: str, handler: str) -> bool:
    return (
        f"{module}:{handler}" in UNAUDITED_BY_DESIGN
        or f"{module}:*" in UNAUDITED_BY_DESIGN
    )


def test_routers_are_discoverable() -> None:
    """Guard the guard: an empty parse would make the check below vacuous."""
    routes = _mutating_routes()
    assert len(routes) > 100, f"only found {len(routes)} mutating routes"
    assert any(audited for _, _, audited in routes)


def test_every_privileged_mutation_is_audited() -> None:
    gaps = sorted(
        f"{module}:{handler}"
        for module, handler, audited in _mutating_routes()
        if not audited and not _is_exempt(module, handler)
    )
    assert not gaps, (
        f"{len(gaps)} state-changing route(s) record no audit event:\n  "
        + "\n  ".join(gaps)
        + "\n\nAdd `await log_audit(session, action, target_type, target_id, "
        "detail, actor_id=..., actor_email=...)` to each, or add it to "
        "UNAUDITED_BY_DESIGN with the reason it records nothing."
    )


def test_exemptions_all_reference_real_routes() -> None:
    """A stale exemption silently re-opens the gap it was written for."""
    modules = set(_router_sources())
    routes = {f"{m}:{h}" for m, h, _ in _mutating_routes()}
    stale = sorted(
        key
        for key in UNAUDITED_BY_DESIGN
        if (key.endswith(":*") and key.split(":")[0] not in modules)
        or (not key.endswith(":*") and key not in routes)
    )
    assert not stale, f"UNAUDITED_BY_DESIGN references routes that no longer exist: {stale}"
