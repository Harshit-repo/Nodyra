"""Pre-run check: does the resolved env have every package its nodes need?"""

from __future__ import annotations

from noodle.packages import canonical_package_name
from noodle.sdk import registry as node_registry


def find_missing_packages(graph: dict, env_packages: list[str]) -> dict[str, list[str]]:
    """Return {missing_specifier: [node ids needing it]} for a graph + env.

    A package is "missing" when no installed package shares its canonical name.
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
