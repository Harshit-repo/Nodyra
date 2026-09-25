"""Static RLS coverage gate (F-03).

``test_tenancy_isolation_pg.py`` proves policy behaviour against a real
Postgres, but that lane is skipped wherever Postgres is unreachable — which is
every local run and most CI lanes. The gap that shipped four unprotected
org-scoped tables was therefore invisible on the lanes developers actually run.

This test needs no database. It parses the migration scripts and asserts that
every model carrying an ``org_id`` has a table named in an RLS statement
somewhere in the migration history. It is deliberately a *static* check: it
catches the drift the moment a model is added, on every lane.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import app.models  # noqa: F401 — populate the mapper registry
from app.tenancy import org_scoped_models

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

_RLS_STATEMENT = re.compile(
    r"(?:ENABLE ROW LEVEL SECURITY|CREATE POLICY)", re.IGNORECASE
)
# Table names appear either interpolated from a module-level tuple/list/dict of
# names, or written literally into the SQL string.
_LITERAL_TABLE = re.compile(
    r"(?:ALTER TABLE|CREATE POLICY \w+ ON)\s+(\w+)", re.IGNORECASE
)


def _string_constants(node: ast.AST) -> set[str]:
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _tables_with_rls() -> set[str]:
    """Every table name a migration puts under row-level security."""
    covered: set[str] = set()
    for path in sorted(VERSIONS_DIR.glob("[0-9]*.py")):
        source = path.read_text(encoding="utf-8")
        if not _RLS_STATEMENT.search(source):
            continue

        covered.update(_LITERAL_TABLE.findall(source))

        # Loop-driven migrations interpolate an f-string over a collection of
        # names: `for table in _TABLES: op.execute(f"ALTER TABLE {table} ...")`.
        # Collect the string constants those collections hold.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and "TABLE" in t.id.upper()
                for t in node.targets
            ):
                covered.update(_string_constants(node.value))
            elif isinstance(node, ast.For):
                covered.update(_string_constants(node.iter))
    return covered


def test_migration_scripts_are_discoverable() -> None:
    """Guard the guard: an empty parse would make the check below vacuous."""
    assert len(list(VERSIONS_DIR.glob("[0-9]*.py"))) > 50
    assert "workflows" in _tables_with_rls()


def test_every_org_scoped_model_has_an_rls_policy() -> None:
    """Both isolation layers must cover the same tables.

    The ORM ``do_orm_execute`` hook auto-discovers any model with an ``org_id``,
    so adding one silently enrols it in layer 2 while layer 1 — Postgres RLS —
    stays behind unless a migration is written. That asymmetry is what let
    environment_build_jobs, memberships, workflow_checks and workflow_revisions
    ship with a single layer of protection.
    """
    # The mapper registry is process-global, so models a sibling test module
    # defines (test_tenancy_scoping.OrgWidget) appear here in a full-suite run
    # but have no migration and never reach a real database.
    expected = {
        model.__tablename__
        for model in org_scoped_models()
        if not model.__tablename__.startswith("test_")
        and not model.__module__.startswith(("test", "tests", "apps.api.tests"))
    }
    assert expected, "no org-scoped models discovered — the check would be vacuous"

    missing = sorted(expected - _tables_with_rls())

    assert not missing, (
        "org-scoped tables with no RLS policy in any migration: "
        f"{missing}\n"
        "Every model with an org_id needs a Postgres row-level-security policy "
        "as its second isolation layer. Add one in a new migration following "
        "0093_rls_coverage_gap, then re-run."
    )
