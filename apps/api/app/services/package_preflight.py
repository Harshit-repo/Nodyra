"""Pre-run check: does the resolved env have every package its nodes need?"""

from __future__ import annotations

import re
import sys

from noodle.packages import canonical_package_name
from noodle.sdk import registry as node_registry

_MARKER_EQ = re.compile(r"sys_platform\s*==\s*['\"]([^'\"]+)['\"]")
_MARKER_NE = re.compile(r"sys_platform\s*!=\s*['\"]([^'\"]+)['\"]")


def _marker_applies(marker: str) -> bool:
    """Return True if the PEP 508 sys_platform marker applies to this server."""
    m = _MARKER_EQ.search(marker)
    if m:
        return sys.platform == m.group(1)
    m = _MARKER_NE.search(marker)
    if m:
        return sys.platform != m.group(1)
    return True  # unknown marker: safe fallback — treat as applicable


def _requirement_applies(req: str) -> bool:
    """Return False if req has a sys_platform marker that excludes this server."""
    if ";" not in req:
        return True
    _, marker = req.split(";", 1)
    return _marker_applies(marker.strip())


def find_missing_packages(graph: dict, env_packages: list[str]) -> dict[str, list[str]]:
    """Return {missing_specifier: [node ids needing it]} for a graph + env.

    Requirements whose PEP 508 sys_platform marker does not match the current
    server platform are skipped — they are not needed here.
    """
    reqs_by_type = {
        m.id: m.requirements for m in node_registry.manifests() if m.requirements
    }
    have = {canonical_package_name(p) for p in env_packages if p.strip()}
    missing: dict[str, list[str]] = {}
    nodes = (graph or {}).get("nodes") or [] if isinstance(graph, dict) else []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        for req in reqs_by_type.get(n.get("type"), []):
            if not _requirement_applies(req):
                continue
            if canonical_package_name(req) not in have:
                missing.setdefault(req, []).append(str(n.get("id") or ""))
    return missing


def format_missing(missing: dict[str, list[str]]) -> str:
    parts = [f"{pkg} (needed by {', '.join(ids)})" for pkg, ids in missing.items()]
    return (
        "This workflow's environment is missing packages required by its nodes: "
        + "; ".join(parts)
        + ". Add them to the environment or switch the workflow to an env that has them."
    )
