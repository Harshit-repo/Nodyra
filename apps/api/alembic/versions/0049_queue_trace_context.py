"""A5 (architecture program Phase 5): run_queue.trace_context.

W3C trace-context carrier (``{"traceparent": ...}``) injected at enqueue so a
worker leasing the entry in another process can join the same trace. Additive
and nullable — NULL whenever tracing is disabled.

Revision ID: 0049_queue_trace_context
Revises: 0048_run_parent
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_queue_trace_context"
down_revision: str | None = "0048_run_parent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_queue", sa.Column("trace_context", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_queue", "trace_context")
