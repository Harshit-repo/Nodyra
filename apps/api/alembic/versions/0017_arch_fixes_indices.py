"""composite indices on runs + node_runs for hot list/lookup queries

Revision ID: 0017_arch_fixes_indices
Revises: 0016_environment_runner_pool_max
Create Date: 2026-05-27

Three composite indices that back queries the API runs on every request
but were previously full scans within their leading-column partition:

* ``runs (workflow_id, started_at DESC)`` — the per-workflow runs list
  (``GET /workflows/{id}/runs``) and the workflow-summary last-run lookup.
* ``runs (status, started_at)`` — the startup ``_mark_interrupted_runs``
  sweep and the retention loop's age filter.
* ``node_runs (run_id, node_id)`` — every retry/rerun replay and the
  per-run timeline view.

All three are pure additions; downgrade drops them. Safe on Postgres and
SQLite (Alembic's ``batch_alter_table`` handles the SQLite "no DROP INDEX
in transactional DDL" wart automatically).
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "0017_arch_fixes_indices"
down_revision: str | None = "0016_environment_runner_pool_max"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RUNS_BY_WORKFLOW_TIME = "ix_runs_workflow_id_started_at"
_RUNS_BY_STATUS_TIME = "ix_runs_status_started_at"
_NODE_RUNS_BY_RUN_NODE = "ix_node_runs_run_id_node_id"


def _existing_indices(table: str) -> set[str]:
    return {idx["name"] for idx in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    runs_indices = _existing_indices("runs")
    if _RUNS_BY_WORKFLOW_TIME not in runs_indices:
        op.create_index(
            _RUNS_BY_WORKFLOW_TIME, "runs", ["workflow_id", "started_at"]
        )
    if _RUNS_BY_STATUS_TIME not in runs_indices:
        op.create_index(
            _RUNS_BY_STATUS_TIME, "runs", ["status", "started_at"]
        )

    node_runs_indices = _existing_indices("node_runs")
    if _NODE_RUNS_BY_RUN_NODE not in node_runs_indices:
        op.create_index(
            _NODE_RUNS_BY_RUN_NODE, "node_runs", ["run_id", "node_id"]
        )


def downgrade() -> None:
    node_runs_indices = _existing_indices("node_runs")
    if _NODE_RUNS_BY_RUN_NODE in node_runs_indices:
        op.drop_index(_NODE_RUNS_BY_RUN_NODE, table_name="node_runs")

    runs_indices = _existing_indices("runs")
    if _RUNS_BY_STATUS_TIME in runs_indices:
        op.drop_index(_RUNS_BY_STATUS_TIME, table_name="runs")
    if _RUNS_BY_WORKFLOW_TIME in runs_indices:
        op.drop_index(_RUNS_BY_WORKFLOW_TIME, table_name="runs")
