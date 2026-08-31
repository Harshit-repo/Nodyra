"""Pre-run check: does the resolved env have every package its nodes need?"""

from __future__ import annotations

import importlib.metadata as metadata
import re
import sys

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from nodyra.packages import canonical_package_name
from nodyra.sdk import registry as node_registry

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


def bundled_packages() -> frozenset[str]:
    """Distributions every environment has because the node library needs them.

    nodyra-nodes is installed in every workflow environment by
    construction, so its own dependencies are always importable there. Nodes
    still *declare* those packages as requirements — duckdb, for instance — and
    without this the pre-flight check demanded they also appear in the
    environment's package list, refusing runs that would have succeeded.

    Read from installed metadata rather than hard-coded, so removing a
    dependency from nodyra-nodes makes the check start requiring it again
    without anyone remembering to edit this file.
    """
    try:
        requires = metadata.requires("nodyra-nodes") or []
    except metadata.PackageNotFoundError:  # pragma: no cover - source checkout
        return frozenset()
    names: set[str] = set()
    for raw in requires:
        try:
            names.add(canonicalize_name(Requirement(raw).name))
        except InvalidRequirement:
            continue
    return frozenset(names)


def find_missing_packages(graph: dict, env_packages: list[str]) -> dict[str, list[str]]:
    """Return {missing_specifier: [node ids needing it]} for a graph + env.

    Requirements whose PEP 508 sys_platform marker does not match the current
    server platform are skipped — they are not needed here.
    """
    reqs_by_type = {
        m.id: m.requirements for m in node_registry.manifests() if m.requirements
    }
    # Declared in the environment, plus whatever ships with the node library.
    have = {canonical_package_name(p) for p in env_packages if p.strip()}
    have |= bundled_packages()
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


def _installed_names(installed: dict[str, str] | list[str]) -> set[str]:
    if isinstance(installed, dict):
        source = installed.keys()
    else:
        source = installed
    names: set[str] = set()
    for entry in source:
        text = str(entry).strip()
        if not text:
            continue
        try:
            names.add(canonicalize_name(Requirement(text).name))
        except InvalidRequirement:
            names.add(canonicalize_name(canonical_package_name(text)))
    return names


def missing_workflow_requirements(
    *, workflow_requirements: list[str], installed: dict[str, str] | list[str]
) -> list[str]:
    """Workflow requirement lines whose distribution name is absent."""
    have = _installed_names(installed)
    missing: list[str] = []
    for line in workflow_requirements:
        text = str(line).strip()
        if not text or not _requirement_applies(text):
            continue
        try:
            req_name = Requirement(text).name
        except InvalidRequirement:
            missing.append(text)
            continue
        if canonicalize_name(req_name) not in have:
            missing.append(text)
    return missing


def format_missing_workflow_requirements(missing: list[str]) -> str:
    return (
        "This workflow's environment is missing packages declared in workflow "
        "requirements: "
        + ", ".join(missing)
        + ". Add them to the environment or switch the workflow to an env that has them."
    )
