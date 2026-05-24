from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    # Run the in-process schedule loop. Disable on multi-replica deployments
    # that drive scheduled runs from Celery Beat instead (avoids double-fire).
    enable_inprocess_scheduler: bool = True
    # Default IANA timezone for the app. Used as the fallback when a
    # schedule_trigger has no explicit ``tz`` field set. Blank → detect the
    # server's local timezone at startup; set explicitly in .env to pin it
    # (e.g. APP_TIMEZONE=Australia/Sydney).
    app_timezone: str = ""
    # Run history retention. The retention loop ticks periodically and drops
    # old runs so the DB stays bounded. 0 disables the corresponding rule.
    run_retention_days: int = 14
    run_retention_max_per_workflow: int = 0
    run_retention_tick_seconds: int = 3600
    # Per-NodeRun output cap (bytes of the JSON-serialised value). Outputs
    # above this are replaced with a small {_truncated, size, preview} stub
    # before persisting so one fat DataFrame can't bloat the DB. 0 disables.
    max_output_bytes: int = 256 * 1024
    workflow_run_timeout_seconds: float = 120.0
    auth_required: bool = False
    secret_key: str = "noodle-dev-secret-change-me-in-production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
