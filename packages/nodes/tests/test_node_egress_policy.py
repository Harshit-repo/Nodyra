"""ARCH guardrail / node production contract (Phase 5.1): every node's
outbound HTTP must go through the shared SSRF guard and declare a timeout.

A node that issues a raw ``requests.<verb>(...)`` call with a host derived from
user input is an SSRF vector (see audit 2026-06-19, SEC-1/2, and the 2026-07
node/data-flow audit's SEC-B finding). This test fails if a node module makes
raw outbound HTTP without importing the ``http_security`` guard — forcing a
new node author to either route through ``safe_request`` / ``assert_public_host``
or consciously add their fixed-endpoint module to the documented allowlist
below. A second test fails if any HTTP call — guarded or not — omits an
explicit ``timeout=``, since an untimed call can hang a worker indefinitely.

The SSRF check is a *nudge*, not a proof: allowlisted modules call first-party
SaaS endpoints whose host is NOT user-controlled.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

NODES_ROOT = Path(__file__).resolve().parents[1] / "nodyra_nodes"

# Raw outbound HTTP call pattern (requests.get/post/put/delete/patch/request/head).
_RAW_REQUEST_RE = re.compile(r"\brequests\.(get|post|put|delete|patch|request|head)\(")
_GUARD_IMPORT_RE = re.compile(r"from nodyra_nodes\.http_security import|import http_security")

# Modules permitted to call requests directly: their request host is a fixed
# first-party endpoint (no user-controlled host).
#
# ai_v2/providers/{anthropic,openai,embeddings}.py used to be allowlisted here
# as "user-configured, may legitimately point at a private host" — but that
# bypassed the SSRF guard entirely instead of using its documented opt-out
# (NODYRA_ALLOW_PRIVATE_EGRESS), and was inconsistent with the legacy chat/
# embedding path in llm.py, which already routes the identical Ollama/
# openai_compatible/Azure base_url cases through safe_request (SEC-B). All
# three now import the guard and are no longer allowlist-eligible; a
# self-hosted Ollama endpoint still works via NODYRA_ALLOW_PRIVATE_EGRESS=1.
_ALLOWLIST = {
    "communication.py",  # fixed first-party endpoints (Pushover, ...)
}


def _rel(path: Path) -> str:
    return path.relative_to(NODES_ROOT).as_posix()


def test_nodes_with_raw_http_use_the_shared_guard() -> None:
    offenders: list[str] = []
    for path in NODES_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if not _RAW_REQUEST_RE.search(text):
            continue
        if _GUARD_IMPORT_RE.search(text):
            continue
        rel = _rel(path)
        if rel in _ALLOWLIST:
            continue
        offenders.append(rel)

    assert not offenders, (
        "These node modules make raw requests.<verb>() calls without importing "
        "the http_security guard. Route user-controlled hosts through "
        "safe_request(), or add the module to the documented allowlist if its "
        "host is a fixed first-party endpoint:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_entries_still_exist_and_are_raw() -> None:
    """Keep the allowlist honest: every entry must still exist and still make a
    raw request without the guard (otherwise it should be removed)."""
    for rel in _ALLOWLIST:
        path = NODES_ROOT / rel
        assert path.exists(), f"allowlisted module no longer exists: {rel}"
        text = path.read_text(encoding="utf-8")
        assert _RAW_REQUEST_RE.search(text), (
            f"allowlisted module {rel} no longer makes a raw request — "
            "remove it from the allowlist"
        )
        assert not _GUARD_IMPORT_RE.search(text), (
            f"allowlisted module {rel} now imports the guard — "
            "remove it from the allowlist"
        )


# ---------------------------------------------------------------------------
# Phase 5.1 (node production contract): every outbound HTTP call must declare
# an explicit timeout. An untimed ``requests``/``httpx``/``safe_request`` call
# can hang a worker indefinitely on a slow/unresponsive endpoint — the runner
# has no way to reclaim it short of killing the whole process.
# ---------------------------------------------------------------------------

_HTTP_VERBS = {"get", "post", "put", "patch", "delete", "request"}


class _MissingTimeoutFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        is_target = False
        if isinstance(func, ast.Attribute) and func.attr in _HTTP_VERBS:
            base = func.value
            if isinstance(base, ast.Name) and base.id in {"requests", "httpx"}:
                is_target = True
        elif isinstance(func, ast.Name) and func.id == "safe_request":
            is_target = True
        if is_target:
            has_timeout_kw = any(kw.arg == "timeout" for kw in node.keywords)
            has_kwargs_passthrough = any(kw.arg is None for kw in node.keywords)
            if not has_timeout_kw and not has_kwargs_passthrough:
                self.violations.append(node.lineno)
        self.generic_visit(node)


def test_nodes_declare_explicit_http_timeouts() -> None:
    offenders: list[str] = []
    for path in NODES_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        finder = _MissingTimeoutFinder()
        finder.visit(tree)
        offenders.extend(f"{_rel(path)}:{lineno}" for lineno in finder.violations)

    assert not offenders, (
        "These calls make outbound HTTP with no explicit timeout= — a slow or "
        "unresponsive endpoint can hang the runner indefinitely:\n  "
        + "\n  ".join(offenders)
    )
