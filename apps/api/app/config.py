from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Explicit runtime topology. `local` is the easy single-process default for
    # development and single-user self-hosting; `production` is the durable,
    # horizontally-scalable mode. Production must not be "local plus env vars" —
    # ``runtime_warnings()`` surfaces configurations that silently fall back to
    # local-only behaviour (SQLite, on-disk artifacts, no shared queue backend).
    runtime_mode: Literal["local", "production"] = "local"
    queue_backend: Literal["none", "redis"] = "none"
    scheduler_role: Literal["inline", "leader", "disabled"] = "inline"
    webhook_role: Literal["inline", "ingress", "disabled"] = "inline"
    # Gate that decides what happens when a deployment is activated against a
    # workflow that contains risky nodes (Code, HTTP→private IP, SQL with
    # expressions, SSH, exec command). See ``app.services.unsafe_nodes``.
    # ``warn`` is the default: findings are returned in the API response but
    # don't block. ``require_approval`` forces the caller to pass
    # ``approve_unsafe_nodes=True``. ``block`` rejects activation outright.
    unsafe_node_policy: Literal["allow", "warn", "require_approval", "block"] = "warn"
    # Deliberately run production despite the warnings below (e.g. a small
    # single-node production deployment that knowingly uses local artifacts).
    runtime_allow_insecure: bool = False

    database_url: str = "postgresql+asyncpg://noodle:noodle@localhost:5432/noodle"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"
    envs_dir: str = "./envs"
    enable_venv_builds: bool = True
    run_synchronously: bool = False
    use_subprocess_runner: bool = False
    # Parallel runs: warm runner processes kept per environment, and a global
    # ceiling on simultaneously executing top-level runs. Default pool size 1
    # because each warm process re-imports the env's (often heavy) packages —
    # raise it deliberately when you have RAM to spare.
    runner_pool_size: int = 1
    max_concurrent_runs: int = 8
    # Close warm runner processes that have been idle longer than this.
    # 0 disables reaping (warm forever). Sweep interval is separate so the
    # cost stays low even with a low idle threshold.
    runner_idle_seconds: int = 600
    runner_idle_tick_seconds: int = 60
    # Server → agent heartbeat. Ping every ``runner_heartbeat_interval_seconds``;
    # if no ``pong`` (i.e. no ``last_seen_at`` update) within
    # ``runner_offline_after_seconds``, the runner is marked offline and any
    # in-flight runs assigned to it are requeued for another runner to pick
    # up. The offline window must be a multiple of the ping interval so a
    # single missed pong doesn't cause flapping.
    runner_heartbeat_interval_seconds: int = 15
    runner_offline_after_seconds: int = 60
    # Run the in-process schedule loop. Disable on multi-replica deployments
    # that drive scheduled runs from Celery Beat instead (avoids double-fire).
    enable_inprocess_scheduler: bool = True
    # Default IANA timezone for the app. Used as the fallback when a
    # schedule_trigger has no explicit ``tz`` field set. Blank → detect the
    # server's local timezone at startup; set explicitly in .env to pin it
    # (e.g. APP_TIMEZONE=Australia/Sydney).
    app_timezone: str = ""
    # Durable run-queue tuning. These were hard-coded in
    # ``app.services.queue`` and are surfaced as config so operators can tune
    # backpressure without code changes (see "Production-readiness gaps" in
    # docs/architecture-improvement-plan.md, item 4).
    queue_lease_seconds: int = 30
    queue_retry_backoff_base_seconds: int = 5
    queue_retry_backoff_max_seconds: int = 300
    queue_default_max_attempts: int = 3
    queue_dispatch_poll_seconds: float = 1.0
    queue_max_dispatches_per_tick: int = 25
    queue_dispatch_shutdown_timeout_seconds: float = 5.0
    # When true, the dispatch loop stops leasing new entries; in-flight
    # leased runs continue. Set this before shutdown to drain gracefully.
    queue_drain: bool = False

    # Run history retention. The retention loop ticks periodically and drops
    # old runs so the DB stays bounded. 0 disables the corresponding rule.
    run_retention_days: int = 14
    run_retention_max_per_workflow: int = 0
    run_retention_tick_seconds: int = 3600
    # Per-NodeRun output cap (bytes of the JSON-serialised value). Outputs
    # above this are replaced with a small {_truncated, size, preview} stub
    # before persisting so one fat DataFrame can't bloat the DB. 0 disables.
    max_output_bytes: int = 256 * 1024
    # Files/dataframes/reports produced by nodes are written outside the DB.
    # Node outputs carry small artifact refs; these limits bound local storage.
    artifacts_dir: str = "./artifacts"
    artifact_storage_backend: str = "local"
    # S3-compatible backend. Bucket is required when backend is "s3";
    # endpoint is required for non-AWS stores (MinIO, R2, B2). Credentials
    # come from the standard boto3 chain (env vars, instance role, profile).
    artifact_s3_bucket: str = ""
    artifact_s3_region: str = ""
    artifact_s3_endpoint: str = ""
    max_artifact_bytes: int = 50 * 1024 * 1024
    max_artifacts_per_run: int = 100
    # Overall wall-clock cap for a single workflow run. 0 (default) means *no*
    # cap — long-running data workflows run until they finish or the run is
    # cancelled. Set a positive value (or a per-workflow ``run_timeout_seconds``
    # override) to fail runaway runs fast.
    workflow_run_timeout_seconds: float = 0.0
    # Default per-node timeout (seconds) for ``code`` nodes when the node
    # doesn't set its own ``timeout_seconds``. 0 means *no* per-node cap so a
    # long-running Python node isn't cancelled mid-flight — it's then bounded
    # only by ``workflow_run_timeout_seconds``. Set a positive value to guard
    # against runaway user code.
    code_node_timeout_seconds: float = 0.0
    auth_required: bool = False
    auth_allow_registration: bool = False
    auth_registration_role: str = "viewer"
    auth_token_ttl_seconds: int = 86_400
    # Per-IP sliding-window cap on /auth/login + /auth/register attempts.
    # Tunes brute-force friction; set ``auth_rate_limit_enabled=False`` to
    # disable entirely (e.g. when fronted by a WAF that already throttles).
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_per_minute: int = 10
    secret_key: str = "noodle-dev-secret-change-me-in-production"
    # Shared secret the worker presents to call /internal/* endpoints.
    # Blank = no check (fine for local dev where only your machine reaches
    # the API). Set this when exposing the API to anything else.
    internal_api_token: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.runtime_mode == "production"

    def runtime_warnings(self) -> list[str]:
        """Configuration issues that make ``production`` mode behave like
        local mode. Always returned for observability (surfaced via
        ``/ops/runtime-mode``); ``runtime_allow_insecure`` lets an operator
        run anyway but does not hide the warnings.
        """
        if self.runtime_mode != "production":
            return []
        warnings: list[str] = []
        if self.database_url.startswith("sqlite"):
            warnings.append(
                "RUNTIME_MODE=production with a SQLite database_url; "
                "use PostgreSQL for durable, concurrent production storage."
            )
        if self.artifact_storage_backend == "local":
            warnings.append(
                "RUNTIME_MODE=production with local artifact storage; "
                "use S3-compatible storage (artifact_storage_backend=s3) so "
                "artifacts survive and are shareable across workers."
            )
        if self.artifact_storage_backend == "s3" and not self.artifact_s3_bucket:
            warnings.append(
                "artifact_storage_backend=s3 but artifact_s3_bucket is empty; "
                "uploads will fail until the bucket is configured."
            )
        if self.queue_backend == "none":
            warnings.append(
                "RUNTIME_MODE=production with no shared queue backend; "
                "set queue_backend=redis so multiple workers share one durable "
                "run queue."
            )
        return warnings


settings = Settings()
