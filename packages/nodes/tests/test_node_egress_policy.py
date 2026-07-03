"""ARCH guardrail: node outbound HTTP must go through the shared SSRF guard.

A node that issues a raw ``requests.<verb>(...)`` call with a host derived from
user input is an SSRF vector (see audit 2026-06-19, SEC-1/2). This test fails if
a node module makes raw outbound HTTP without importing the ``http_security``
guard — forcing a new node author to either route through ``safe_request`` /
``assert_public_host`` or consciously add their fixed-endpoint module to the
documented allowlist below.

It is a *nudge*, not a proof: allowlisted modules call first-party SaaS / model
endpoints whose host is NOT user-controlled, or are trusted-by-design model
endpoints the user explicitly configures (and may legitimately point at a
private host, e.g. a self-hosted Ollama server).
"""

from __future__ import annotations

import re
from pathlib import Path

NODES_ROOT = Path(__file__).resolve().parents[1] / "nodyra_nodes"

# Raw outbound HTTP call pattern (requests.get/post/put/delete/patch/request/head).
_RAW_REQUEST_RE = re.compile(r"\brequests\.(get|post|put|delete|patch|request|head)\(")
_GUARD_IMPORT_RE = re.compile(r"from nodyra_nodes\.http_security import|import http_security")

# Modules permitted to call requests directly: their request host is a fixed
# first-party endpoint (no user-controlled host), or a model endpoint the user
# configures explicitly and may legitimately point at a private host.
_ALLOWLIST = {
    "ai_v2/providers/anthropic.py",   # fixed api.anthropic.com
    "ai_v2/providers/openai.py",      # OpenAI / openai-compatible model endpoint (user-configured)
    "ai_v2/providers/embeddings.py",  # embedding model endpoints (user-configured)
    "communication.py",               # fixed first-party endpoints (Pushover, ...)
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
