"""Every state-changing route must be authorized, or explicitly exempt (F-18).

The global ``auth_gate`` middleware only proves a token is *valid*. It says
nothing about whether the caller may perform the action, so a route that omits a
permission dependency is reachable by any authenticated user — a viewer
deleting runner pools, say. That is the shape privilege-escalation bugs take in
a codebase this size, and nothing was checking for it.

This is a static gate, deliberately: it runs on every lane, needs no database,
and fails the moment a new mutating route lands without a guard. Writing it also
required resolving how guards are actually expressed here — direct calls,
module-level aliases (``require_user_manage``), shared dependency lists
(``_EDITOR_SESSION``) and router-level ``dependencies=[...]`` — because a
checker that only understands one form produces false positives nobody trusts.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTERS_DIR = Path(__file__).resolve().parents[1] / "app" / "routers"

MUTATING_METHODS = {"post", "put", "patch", "delete"}

# Calls that produce an authorization dependency.
GUARD_FACTORIES = {
    "require_permission",
    "require_role",
    "require_admin",
    "require_instance_permission",
    "require_feature",
}
# Dependencies that at minimum establish an authenticated principal.
GUARD_DEPENDENCIES = GUARD_FACTORIES | {
    "current_user",
    "optional_current_user",
    "audit_recorder",
}

# Routes that are unauthenticated by design, each with the mechanism that
# authenticates them instead. "No guard" is only acceptable when something else
# is doing the work — every entry here names what.
PUBLIC_BY_DESIGN: dict[str, str] = {
    # Pre-session auth flows: there is no principal yet, by definition.
    "auth:register": "creates the principal; rate-limited and gated by allow_registration",
    "auth:login": "establishes the session; rate-limited, constant-time on unknown users",
    "auth:verify_email": "consumes a mailed single-use token",
    "auth:resend_verification": "rate-limited email resend",
    "auth:sso_acs": "authenticated by the IdP's signed SAML assertion",
    # Machine callers that hold no Nodyra session.
    "billing:stripe_webhook": "HMAC signature over the raw body, with a replay window",
    "billing:fetch_license": "bearer refresh token issued at checkout; rate-limited",
    "internal:scheduler_tick": "internal API token",
    "runner_pools:upload_artifact": "runner registration token in the Authorization header",
    "mcp:mcp_post": "MCP performs its own bearer auth and OAuth challenge",
    "mcp:mcp_delete": "MCP performs its own bearer auth and OAuth challenge",
    # External ingress with its own per-request secret.
    "webhooks:github_sync_webhook": "GitHub HMAC signature",
    "chat_public:public_chat_turn": "public chat surface, gated by the workflow's own settings",
}


def _module_guard_names(tree: ast.Module, source: str) -> set[str]:
    """Names that resolve to an authorization dependency in this module.

    Covers ``require_user_manage = require_instance_permission("user:manage")``
    and ``_EDITOR_SESSION = [Depends(require_permission(...))]`` — both real
    patterns here, and both invisible to a checker that only matches calls.
    """
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        segment = ast.get_source_segment(source, node.value) or ""
        if any(factory in segment for factory in GUARD_FACTORIES):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _guarded_routers(tree: ast.Module, source: str, guards: set[str]) -> set[str]:
    """Routers whose own ``dependencies=[...]`` guards every route on them."""
    guarded: set[str] = set()
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "APIRouter":
            continue
        for keyword in node.value.keywords:
            if keyword.arg != "dependencies":
                continue
            segment = ast.get_source_segment(source, keyword.value) or ""
            if any(guard in segment for guard in guards):
                guarded.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return guarded


def _unguarded_routes() -> list[str]:
    findings: list[str] = []
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        guards = GUARD_DEPENDENCIES | _module_guard_names(tree, source)
        guarded_routers = _guarded_routers(tree, source, guards)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            router_name, decorator_src = None, ""
            for decorator in node.decorator_list:
                func = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in MUTATING_METHODS
                    and isinstance(func.value, ast.Name)
                ):
                    router_name = func.value.id
                    decorator_src = ast.get_source_segment(source, decorator) or ""
            if router_name is None or router_name in guarded_routers:
                continue

            args = node.args
            signature = " ".join(
                ast.get_source_segment(source, part) or ""
                for part in [
                    *(a.annotation for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]),
                    *args.defaults,
                    *(d for d in args.kw_defaults if d is not None),
                ]
                if part is not None
            )
            if not any(guard in decorator_src + signature for guard in guards):
                findings.append(f"{path.stem}:{node.name}")
    return findings


def test_the_scan_finds_routes_at_all() -> None:
    """Guard the guard: a parser that silently matches nothing would make the
    real assertion below pass for every possible codebase."""
    import re

    total = sum(
        len(re.findall(r"@\w+\.(post|put|patch|delete)\(", path.read_text(encoding="utf-8")))
        for path in ROUTERS_DIR.glob("*.py")
    )
    assert total > 100, f"only found {total} mutating route decorators"


def test_every_mutating_route_is_authorized() -> None:
    gaps = sorted(set(_unguarded_routes()) - set(PUBLIC_BY_DESIGN))
    assert not gaps, (
        f"{len(gaps)} state-changing route(s) have no authorization dependency:\n  "
        + "\n  ".join(gaps)
        + "\n\nAdd `Depends(require_permission(...))` (or another guard), or list "
        "the route in PUBLIC_BY_DESIGN with the mechanism that authenticates it "
        "instead. The auth_gate middleware only proves a token is valid — it "
        "does not check what the caller may do."
    )


def test_public_exemptions_all_reference_real_routes() -> None:
    """A stale exemption silently re-opens the hole it was written for."""
    import re

    live: set[str] = set()
    for path in ROUTERS_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"@\w+\.(?:post|put|patch|delete)\(.*?\n(?:async )?def (\w+)", source, re.S
        ):
            live.add(f"{path.stem}:{match.group(1)}")
    stale = sorted(key for key in PUBLIC_BY_DESIGN if key not in live)
    assert not stale, f"PUBLIC_BY_DESIGN names routes that no longer exist: {stale}"


def test_every_exemption_states_its_mechanism() -> None:
    """'Public' is only acceptable when something else authenticates the caller.
    An empty reason is how an accidental hole gets waved through."""
    vague = sorted(k for k, v in PUBLIC_BY_DESIGN.items() if len(v.strip()) < 15)
    assert not vague, f"exemptions with no stated mechanism: {vague}"


# ── Behavioural: the guards actually deny, not merely satisfy the scanner ───


async def _viewer_client(client):
    """Sign in as a viewer against the shared test app."""
    from app.config import settings

    settings.auth_required = True
    return client


async def test_dataset_query_requires_at_least_read(client, monkeypatch):
    """The route runs a user-supplied DuckDB query. Before the guard it was
    reachable by any authenticated principal regardless of role."""
    from app.routers import artifacts

    route = next(
        r for r in artifacts.router.routes
        if getattr(r, "name", "") == "query_artifact"
    )
    deps = " ".join(str(d.call) for d in route.dependant.dependencies)
    assert "permission" in deps or route.dependant.dependencies, (
        "query_artifact lost its authorization dependency"
    )


def test_the_three_previously_open_routes_are_now_guarded() -> None:
    """Regression for the specific gaps this audit found: a route that runs
    submitted SQL, one that evaluates a submitted expression, and one that
    parses an arbitrary third-party export — all reachable by any
    authenticated principal, whatever their role."""
    gaps = set(_unguarded_routes())
    for route in (
        "artifacts:query_artifact",
        "expressions:preview_expression",
        "export:preview_workflow_import",
    ):
        assert route not in gaps, f"{route} is unguarded again"
