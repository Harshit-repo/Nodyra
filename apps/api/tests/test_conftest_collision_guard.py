"""Guard: a whole-repo ``pytest`` run must still reach the tests.

Every test directory in this monorepo is named ``tests`` and carries an
``__init__.py``. Under pytest's default package-based naming that makes each
``tests/conftest.py`` the module ``tests.conftest``, and the second one to be
collected aborts the entire run:

    ValueError: Plugin already registered under a different name:
    packages/nodes/tests/conftest.py=<module 'tests.conftest' from
    'apps/api/tests/conftest.py'>

That is a collection error, not a test failure -- zero tests run, in CI too,
which invokes a rootdir-scoped ``uv run pytest``. ``consider_namespace_packages``
makes the derived name rootdir-relative and therefore unique. This test pins
the two facts together so adding the next ``tests/conftest.py`` cannot silently
take the suite down.
"""

import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _ini() -> dict:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return config["tool"]["pytest"]["ini_options"]


def _legacy_module_name(conftest: Path) -> str:
    """The module name pytest derives without namespace-package resolution."""
    parts = [conftest.stem]
    parent = conftest.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts))


def _testpath_conftests() -> list[Path]:
    """Repo-owned conftests under ``testpaths``.

    Sourced from git rather than a filesystem walk: ``apps/api/envs`` holds a
    per-environment virtualenv tree, and the vendored numpy/pandas/pyarrow
    conftests in there are neither ours nor collected.
    """
    testpaths = _ini()["testpaths"]
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *testpaths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        REPO_ROOT / line for line in listed if line.endswith("/conftest.py")
    ]


def test_colliding_conftest_names_are_disambiguated() -> None:
    conftests = _testpath_conftests()
    names: dict[str, list[Path]] = {}
    for conftest in conftests:
        names.setdefault(_legacy_module_name(conftest), []).append(conftest)
    collisions = {name: paths for name, paths in names.items() if len(paths) > 1}
    if not collisions:
        return

    rendered = "; ".join(
        f"{name}: {[str(p.relative_to(REPO_ROOT)) for p in paths]}"
        for name, paths in sorted(collisions.items())
    )
    assert _ini().get("consider_namespace_packages") is True, (
        "conftest.py files share a package-derived module name and would abort "
        f"a whole-repo collection: {rendered}. Set "
        "consider_namespace_packages = true under [tool.pytest.ini_options]."
    )
