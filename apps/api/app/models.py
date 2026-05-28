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
    Index,
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


class RunnerPool(Base):
    """A named pool of remote execution targets.

    ``provider`` is one of ``"agent"`` (outbound-WS daemon on a VM),
    ``"docker"`` (API manages containers via Docker SDK), or
    ``"kubernetes"`` (API creates K8s Jobs). ``provider_config`` holds
    provider-specific settings: Docker socket URL, kubeconfig YAML, AWS
    credentials for cloud provisioning, etc.
    """

    __tablename__ = "runner_pools"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")
    provider_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    runners: Mapped[list["Runner"]] = relationship(
        back_populates="pool", cascade="all, delete-orphan"
    )


class Runner(Base):
    """An agent runner instance registered to a pool.

    Only relevant for the ``agent`` provider — Docker/K8s pools have no
    persistent runner rows. ``token_hash`` stores a bcrypt/sha256 digest
    of the one-time registration JWT so artifact-upload requests can be
    re-verified. ``cached_env_ids`` is a list of ``"{env_id}-{packages_hash}"``
    strings representing envs this runner has already built locally.
    """

    __tablename__ = "runners"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    pool_id: Mapped[str] = mapped_column(
        ForeignKey("runner_pools.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="offline")
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    cached_env_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # SSH-onboarded runners store "user@host:port" + a Fernet-encrypted JSON
    # blob of the SSH credentials so the machine can be restarted later.
    ssh_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    pool: Mapped["RunnerPool"] = relationship(back_populates="runners")


class RunBatch(Base):
    """A parameter-matrix batch job: one parent tracking N child runs.

    Created by ``POST /workflows/{id}/batch-runs``. Each child run carries
    ``batch_id`` pointing here. ``parameters`` is the full list of param
    dicts supplied at creation time so the batch can be re-run or inspected.
    """

    __tablename__ = "run_batches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True, nullable=False
    )
    deployment_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    runner_pool_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    total_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancelled_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parameters: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    environment_id: Mapped[str | None] = mapped_column(
        ForeignKey("environments.id"), nullable=True
    )
    default_runner_pool_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True
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
    runner_pool_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    runner_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    queue_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

    # Composite indices backing hot list queries — per-workflow runs feed,
    # status-based sweeps (retention prune, interrupted-runs cleanup).
    __table_args__ = (
        Index("ix_runs_workflow_id_started_at", "workflow_id", "started_at"),
        Index("ix_runs_status_started_at", "status", "started_at"),
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

    # Composite index for fast per-run, per-node lookup during replay/retry.
    __table_args__ = (
        Index("ix_node_runs_run_id_node_id", "run_id", "node_id"),
    )


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
    runner_pool_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
