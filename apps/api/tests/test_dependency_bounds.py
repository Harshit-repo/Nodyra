"""Every dependency needs an upper bound.

``packages/nodes`` declared ``mcp>=1.28.1`` with no upper bound. ``uv.lock`` pins
1.28.1, so the dev venv and CI never saw anything else — but a *workflow
environment* resolves its dependencies fresh, and got 2.1.1, which had removed a
symbol the code imports. ``import nodyra_nodes`` raised, the runtime never
emitted its ready event, and every freshly built environment was broken while
every test stayed green.

The lockfile protects the places that use it. It cannot protect the places that
resolve fresh, and this product has both. An upper bound is what protects the
second kind, so a maintainer cutting a major release is caught by a failed
resolve rather than by a user whose environment stops building.

Bounds are set at the first version that would be a breaking change, computed
from what is currently locked — so they constrain the future without changing
today's resolution. Relocking after they were added moved zero package versions.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

PYPROJECTS = [
    "pyproject.toml",
    "apps/api/pyproject.toml",
    "packages/client/pyproject.toml",
    "packages/core/pyproject.toml",
    "packages/exporter/pyproject.toml",
    "packages/importer/pyproject.toml",
    "packages/nodes/pyproject.toml",
    "packages/runner/pyproject.toml",
    "packages/runtime/pyproject.toml",
]

#: Workspace members are pinned by the workspace itself, not by a range.
INTERNAL = {"nodyra", "nodyra-nodes", "nodyra-runtime", "nodyra-exporter",
            "nodyra-importer", "nodyra-client", "nodyra-runner-agent"}


def _requirements(relative: str) -> list[str]:
    path = ROOT / relative
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    reqs = list(project.get("dependencies", []) or [])
    for group in (project.get("optional-dependencies", {}) or {}).values():
        reqs.extend(group)
    for group in (data.get("dependency-groups", {}) or {}).values():
        reqs.extend(r for r in group if isinstance(r, str))
    return reqs


def _name(spec: str) -> str:
    head = spec.split(";")[0].strip()
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", head)
    return (match.group(0) if match else head).lower().replace("_", "-")


def _has_upper_bound(spec: str) -> bool:
    constraint = spec.split(";")[0]
    return "<" in constraint or "==" in constraint or "~=" in constraint


@pytest.mark.parametrize("relative", PYPROJECTS)
def test_every_dependency_has_an_upper_bound(relative):
    unbounded = [
        spec
        for spec in _requirements(relative)
        if ">=" in spec.split(";")[0]
        and not _has_upper_bound(spec)
        and _name(spec) not in INTERNAL
    ]
    assert not unbounded, (
        f"{relative} declares {len(unbounded)} dependency/dependencies with a "
        f"lower bound and no upper bound: {unbounded}. The lockfile pins these "
        f"for dev and CI, but workflow environments resolve fresh — a major "
        f"release upstream would break every new environment while the suite "
        f"stays green, which is exactly what mcp 2.x did."
    )


def test_there_are_dependencies_to_check():
    """Guard the guard: if parsing silently returns nothing, every assertion
    above passes by checking an empty list."""
    total = sum(len(_requirements(p)) for p in PYPROJECTS)
    assert total > 50, f"only found {total} declared dependencies across the workspace"


def test_the_bound_that_broke_environments_is_present():
    """mcp is the specific case this suite exists for."""
    specs = _requirements("packages/nodes/pyproject.toml")
    mcp = next((s for s in specs if _name(s) == "mcp"), None)
    assert mcp is not None, "mcp is no longer declared by packages/nodes"
    assert _has_upper_bound(mcp), f"mcp is unbounded again: {mcp!r}"


def test_bounds_do_not_exclude_what_is_locked():
    """A bound that excludes the locked version would make the workspace
    unresolvable — the constraint has to describe the future, not forbid the
    present."""
    packaging = pytest.importorskip("packaging")
    from packaging.requirements import Requirement
    from packaging.version import Version

    lock = ROOT / "uv.lock"
    if not lock.exists():
        pytest.skip("no uv.lock in this checkout")
    locked = {
        str(p.get("name", "")).lower().replace("_", "-"): str(p.get("version", ""))
        for p in tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
    }
    assert packaging  # keep the import meaningful

    offenders = []
    for relative in PYPROJECTS:
        for spec in _requirements(relative):
            name = _name(spec)
            if name in INTERNAL or name not in locked:
                continue
            try:
                requirement = Requirement(spec.split(";")[0].strip())
                version = Version(locked[name])
            except Exception:  # noqa: BLE001 - unparseable specs are not this test's job
                continue
            if requirement.specifier and version not in requirement.specifier:
                offenders.append(f"{relative}: {spec!r} excludes locked {version}")

    assert not offenders, offenders
