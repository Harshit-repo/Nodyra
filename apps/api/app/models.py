"""SQLAlchemy ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class Environment(Base):
    """A Python environment: the global one or a user-created custom venv."""

    __tablename__ = "environments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_global: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    python_version: Mapped[str] = mapped_column(String(16), default="3.12", nullable=False)
    packages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    status_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    runner_pool_size: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    runner_pool_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_rss_estimate_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("environments.id"), nullable=True
    )
    draft_graph: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_alerts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list["WorkflowVersion"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowVersion.version",
    )


class User(Base):
    """A local user account."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    company: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Credential(Base):
    """An encrypted secret (API key, auth header, ...) referenced by nodes."""

    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False, default="generic")
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="global")
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    runner_pool_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    encrypted_data: Mapped[str] = mapped_column(Text, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditEvent(Base):
    """A record of a mutating action, for the activity log."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PinnedData(Base):
    """A frozen output value for a single node within a workflow."""

    __tablename__ = "pinned_data"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "node_id", name="uq_pinned_workflow_node"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Run(Base):
    """One execution of a workflow."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True, nullable=False
    )
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    workflow_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    deployment_id: Mapped[str | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    triggered_by_error_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    trigger_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    node_runs: Mapped[list["NodeRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class NodeRun(Base):
    """The result of executing a single node within a run."""

    __tablename__ = "node_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    debug: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    run: Mapped[Run] = relationship(back_populates="node_runs")


class Artifact(Base):
    """Metadata for a file/blob produced by a workflow run."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="binary", nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(160), default="application/octet-stream", nullable=False
    )
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_backend: Mapped[str] = mapped_column(
        String(40), default="local", nullable=False
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_metadata: Mapped[dict] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    preview: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped[Run] = relationship(back_populates="artifacts")


class CodeModule(Base):
    """A user-uploaded Python file whose top-level functions become nodes.

    Scoped Global / per-Environment / per-Workflow. The runtime registers
    them into the node registry on demand: workflow-scoped modules ride
    along with each run, while global / per-env modules can later be loaded
    into the warm subprocess once. v1 wires only the workflow scope.
    """

    __tablename__ = "code_modules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="workflow")
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contents: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Deployment(Base):
    """A schedulable, parametrized instance of a workflow.

    Generalises the per-workflow ``active`` flag + in-graph schedule_trigger:
    you can have several deployments of the same workflow, each with its own
    cron / interval, default parameters, environment override, and on/off
    toggle. When a workflow has any active deployments, the scheduler uses
    them as the canonical schedule source (ignoring the in-graph trigger),
    so the same workflow can't be scheduled twice.
    """

    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Schedule — set ``schedule_cron`` for full cron, or interval/every for the
    # simple mode. Either may be empty when the deployment is run-now-only.
    schedule_cron: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    schedule_interval: Mapped[str] = mapped_column(
        String(20), default="hours", nullable=False
    )
    schedule_every: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    schedule_tz: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    default_parameters: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("environments.id"), nullable=True
    )
    workflow_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    error_workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_alerts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_fired: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScheduleState(Base):
    """Durable record of when each workflow's schedule trigger last fired.

    Persisted (rather than kept in memory) so the scheduler survives an API
    restart without missing or double-firing scheduled runs.
    """

    __tablename__ = "schedule_state"

    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), primary_key=True
    )
    last_fired: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WorkflowVersion(Base):
    """An immutable snapshot of a workflow's graph. Every save creates one."""

    __tablename__ = "workflow_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    workflow: Mapped[Workflow] = relationship(back_populates="versions")


class SystemSetting(Base):
    """Singleton row holding workspace-wide runtime settings.

    Only one row exists (id="singleton"). Admin endpoints update it and the
    ``live_settings`` service reads it on each access so hot-reloadable
    consumers (retention loop, output cap, artifact limits, idle reaper)
    pick up changes without a restart. Pool sizing and concurrency limits
    are applied at next pool creation or restart — the UI surfaces that.
    """

    __tablename__ = "system_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="singleton")
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    runner_idle_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    run_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    run_retention_max_per_workflow: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    max_output_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=256 * 1024
    )
    max_artifact_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50 * 1024 * 1024
    )
    max_artifacts_per_run: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )
    app_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    worker_rss_soft_budget_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
