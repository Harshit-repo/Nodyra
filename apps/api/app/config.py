from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Known KMS provider names used in settings validation.
KMS_PROVIDERS = frozenset({"env", "vault", "aws", "gcp"})

# The shipped placeholder secret. Centralised so the field default, the
# advisory warning, and the hard startup guard all reference one value.
DEFAULT_SECRET_KEY = "noodle-dev-secret-change-me-in-production"


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
    # Execution-plane topology (program A1), mirroring scheduler_role:
    #   inline   -> this process leases its own durable-queue entries and
    #               executes them (single-process default; today's behaviour)
    #   worker   -> standalone execution role, started via
    #               ``python -m app.worker_main`` (no HTTP surface). Leases
    #               local + docker entries (execution it can host itself).
    #   control  -> control plane that ALSO dispatches agent/kubernetes pools:
    #               parks every run on the durable queue (like ``disabled``)
    #               but runs a dispatch loop leasing only the WS-terminating
    #               providers {agent, kubernetes}, whose run assignment must
    #               originate from the process holding the runner WebSocket
    #               (this one). Pair with a ``worker`` for local/docker.
    #   disabled -> pure control plane: no dispatch loop, no runtime pool;
    #               every run is parked on the durable queue for workers.
    #               Agent/kubernetes pools STAY queued (nothing leases them) —
    #               use ``control`` instead when you run those pools.
    # worker/control/disabled require Redis (run events must cross processes —
    # the in-process broker would strand WebSocket clients on the API replica)
    # and Postgres (SKIP LOCKED queue leasing). See dispatch_topology_errors().
    dispatch_role: Literal["inline", "worker", "control", "disabled"] = "inline"
    # ``ingress`` (default) is the production posture: this process serves the
    # public ``/webhook/{path}`` routes. ``inline`` is the same routing for a
    # minimal single-user setup. ``disabled`` unmounts the public webhook routes
    # (control-plane-only replica) while keeping the editor ``/webhook-test/*``
    # capture paths available. See ``app/routers/webhooks.py`` + ``app/main.py``.
    webhook_role: Literal["inline", "ingress", "disabled"] = "ingress"
    # Max seconds a synchronous webhook (response_mode = Last Node / Respond
    # Node) waits for its run to finish before returning 504. The run keeps
    # executing in the background past the timeout.
    webhook_response_timeout_seconds: float = 30.0
    # When True, every webhook trigger node MUST configure at least one auth
    # method (basic, header, bearer, jwt, hmac) — auth_type="none" is rejected
    # at publish time.  Default True in production mode so a misconfigured
    # workflow can't expose an unauthenticated public endpoint by accident.
    # Set False to allow open webhooks (e.g. for internal trusted-tenant use).
    webhook_require_auth: bool = True
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
    # SQLAlchemy async connection pool tuning. Ignored for SQLite (NullPool).
    # pool_size: steady-state connections kept open; max_overflow adds burst
    # headroom; pool_recycle prevents stale connections after long idle periods;
    # pool_timeout: seconds to wait for a connection from the pool before error.
    db_pool_size: int = 5
    db_pool_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800
    db_pool_timeout: int = 30
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"
    envs_dir: str = "./envs"
    enable_venv_builds: bool = True
    run_synchronously: bool = False
    # Subprocess execution is the safe default: each run is isolated in a warm
    # worker process, and admission is bounded by ``max_concurrent_runs``
    # (global) plus per-env pool caps, so a burst of runs can't melt the host.
    # The in-process path (set False) is kept for tests/dev; it is now also
    # bounded by the same global ceiling (see runner._execute_run).
    use_subprocess_runner: bool = True
    # Parallel runs: warm runner processes kept per environment, and a global
    # ceiling on simultaneously executing top-level runs. Default pool size 1
    # because each warm process re-imports the env's (often heavy) packages —
    # raise it deliberately when you have RAM to spare.
    runner_pool_size: int = 1
    max_concurrent_runs: int = 8
    # Bound on simultaneously-spawned sub-workflow subprocesses (fan-out
    # throttle). 0 → fall back to ``max_concurrent_runs``. This is a SOFT cap:
    # because sub-workflows nest (A→B→C) and each ancestor holds its slot while
    # awaiting the child, we proceed without a slot after
    # ``subworkflow_spawn_timeout_seconds`` rather than risk deadlocking deep
    # chains. It smooths wide fan-out (a parent calling many subs at once)
    # without hard-blocking legitimate nesting.
    max_concurrent_subworkflows: int = 0
    subworkflow_spawn_timeout_seconds: float = 30.0
    # A3: hard ceiling on sub-workflow nesting depth (root = 0). Cycle
    # detection catches A->B->A; this catches runaway A->B->C->... chains.
    # 0 = unlimited.
    max_subworkflow_depth: int = 16
    # Close warm runner processes that have been idle longer than this.
    # 0 disables reaping (warm forever). Sweep interval is separate so the
    # cost stays low even with a low idle threshold.
    runner_idle_seconds: int = 600
    runner_idle_tick_seconds: int = 60
    # Pool autoscaler: when queued runs exceed this threshold, the global
    # pool concurrency ceiling grows up to pool_autoscale_max.  0 disables.
    pool_autoscale_enabled: bool = True
    pool_autoscale_threshold: int = 8
    pool_autoscale_max: int = 32
    pool_autoscale_cooldown_seconds: int = 60
    # Server → agent heartbeat. Ping every ``runner_heartbeat_interval_seconds``;
    # if no ``pong`` (i.e. no ``last_seen_at`` update) within
    # ``runner_offline_after_seconds``, the runner is marked offline and any
    # in-flight runs assigned to it are requeued for another runner to pick
    # up. The offline window must be a multiple of the ping interval so a
    # single missed pong doesn't cause flapping.
    runner_heartbeat_interval_seconds: int = 15
    runner_offline_after_seconds: int = 60
    # Token lifetime for runner registration tokens. Default 1 year (365 days).
    # Tokens are revocable at any time by deleting the runner row.
    runner_token_ttl_days: int = 365
    # Ghost runner cleanup: delete runners that never connected (last_seen_at
    # IS NULL) and were created more than this many hours ago. Set to 0 to
    # disable auto-cleanup.
    runner_ghost_ttl_hours: int = 48
    # Run the in-process schedule loop. Multi-replica deployments keep this
    # on and set scheduler_role=leader so one replica owns it (avoids
    # double-fire); set false to disable scheduling in this process entirely.
    enable_inprocess_scheduler: bool = True
    # Seconds between scheduler ticks. 30 is the default — operators who need
    # sub-30-second cron precision can lower this at the cost of more DB polls.
    scheduler_tick_seconds: float = 30.0
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
    # Durable environment-build queue tuning. These jobs rebuild Python
    # environments after create/package/backend changes. Keep leases much
    # longer than run leases because package resolution can legitimately take
    # minutes; a heartbeat extends active leases while the build process lives.
    environment_build_queue_poll_seconds: float = 1.0
    environment_build_queue_lease_seconds: int = 1800
    environment_build_queue_default_max_attempts: int = 3
    environment_build_queue_retry_backoff_base_seconds: int = 10
    environment_build_queue_retry_backoff_max_seconds: int = 600
    environment_build_queue_max_concurrent_jobs: int = 2
    # Local durable queue: when a LOCAL run (in-process / subprocess pool,
    # no remote runner pool) can't get an admission slot immediately, leave
    # it as a durable ``queued`` ``RunQueueEntry`` instead of blocking a
    # coroutine on the pool semaphore. The dispatch loop then leases it as
    # capacity frees. Gives visible queue depth + restart durability for
    # local execution. Disable to fall back to the old block-on-semaphore
    # behaviour.
    local_queue_enabled: bool = True
    # Soft RSS budget (bytes) across concurrently executing top-level runs.
    # 0 disables RSS gating. Boot default; the live value is the
    # ``system_settings.worker_rss_soft_budget_bytes`` row when present.
    # Before a run acquires a worker, its env's measured
    # ``worker_rss_estimate_bytes`` is reserved against this ceiling so a
    # burst of heavy-env runs can't OOM the host (count caps alone can't tell
    # an 80 MB env from a 1.2 GB one). Soft: a run is always admitted when no
    # other run is reserved, even if it alone exceeds the budget.
    worker_rss_soft_budget_bytes: int = 0

    # Run history retention. The retention loop ticks periodically and drops
    # old runs so the DB stays bounded. 0 disables the corresponding rule.
    run_retention_days: int = 14
    run_retention_max_per_workflow: int = 0
    run_retention_tick_seconds: int = 3600
    # Audit log retention. Rows older than this many days are purged nightly
    # by the maintenance loop. 0 disables the automatic purge.
    audit_log_retention_days: int = 90
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
    # Multi-tenancy master switch. Off (default): single-tenant behaviour,
    # zero filtering, the existing suite must pass unchanged. On: every
    # request resolves an organization (X-Org-Id header validated against
    # memberships), ORM SELECTs are auto-scoped to it, and on Postgres the
    # app.current_org GUC backs the RLS policies. See app/tenancy.py.
    multi_tenancy_enabled: bool = False
    # Sandboxed execution (MT Phase D slice 1). "off": runs use the warm
    # subprocess pool (today's behaviour). "auto": use disposable hardened
    # containers when a Docker daemon is reachable, else fall back to
    # subprocess with a startup warning. "required": refuse to start the
    # dispatching process without a usable daemon + runtime.
    execution_sandbox: str = "off"
    # Container isolation runtime: auto-probe (kata > runsc > runc) or pin.
    sandbox_runtime: str = "auto"
    # Docker daemon for sandbox containers; empty = environment default
    # (DOCKER_HOST / the mounted socket).
    sandbox_docker_host: str = ""
    # Dedicated bridge network for run containers — keeps tenant code off
    # the compose project network (no postgres/redis/minio reachability).
    sandbox_network: str = "noodle-sandbox"
    # Per-container resource ceilings.
    sandbox_mem_limit: str = "1g"
    sandbox_cpu_limit: float = 1.0
    sandbox_pids_limit: int = 256
    sandbox_tmpfs_size: str = "256m"
    # Warm pool: idle containers kept per (org, env) key / globally, idle
    # TTL, and a recycle ceiling bounding state accumulation per container.
    sandbox_warm_per_key: int = 1
    sandbox_warm_total: int = 8
    sandbox_warm_ttl_seconds: float = 300.0
    sandbox_max_runs_per_container: int = 50
    # Maximum runs a single warm subprocess services before being recycled.
    # 0 → unlimited.  A positive value prevents gradual global-state
    # accumulation from different workflows sharing the same subprocess.
    runner_max_runs_per_subprocess: int = 100
    # Grace period (seconds) before a running run with no node progress is
    # declared stuck and marked error by the stuck-run detector loop.
    stuck_run_grace_seconds: float = 1800.0
    # Seconds to wait for a fresh container's {"type":"ready"} handshake.
    sandbox_ready_timeout_seconds: float = 60.0
    # When True (default), multi_tenancy_enabled requires
    # execution_sandbox=required at startup. Setting False acknowledges
    # shared-kernel execution for trusted-tenant deployments.
    sandbox_policy_strict: bool = True
    # A5: OpenTelemetry tracing. Off by default — when disabled no SDK objects
    # are created and every tracing hook is a single boolean check (zero
    # overhead). Endpoint is the OTLP/HTTP collector traces URL, e.g.
    # http://localhost:4318/v1/traces; blank uses the SDK default
    # (http://localhost:4318/v1/traces). Standard OTEL_* env vars are also
    # honoured by the SDK for anything not surfaced here.
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    # "http" (default) uses OTLP/HTTP POST to /v1/traces.  "grpc" switches to
    # OTLP/gRPC (port 4317) for collectors that speak the gRPC protocol.
    otel_exporter_protocol: str = "http"
    # MCP server: exposes POST /mcp (workflow run + builder tools) when on.
    mcp_server_enabled: bool = True
    # Licensing (see app/services/licensing.py). A signed Ed25519 license key
    # set here (env NOODLE_LICENSE_KEY) takes precedence over the DB-stored key.
    # Blank → resolve from system_settings.license_key, else Community edition.
    license_key: str = ""
    # PEM-encoded Ed25519 public key used to verify license keys. Blank → use
    # the key baked into app/services/licensing.py. Tests override this.
    license_public_key: str = ""
    # Community Node Registry (MS4 Slice 4E). When False, the registry feature
    # is disabled (air-gapped / maximum-security deployments). Default True.
    allow_registry: bool = True
    # URL of the community registry index JSON. The MVPC uses a GitHub-backed
    # JSON file — a PR-based registry index hosted in a public repo.
    registry_index_url: str = "https://raw.githubusercontent.com/noodle-registry/packages/main/index.json"
    auth_required: bool = False
    auth_allow_registration: bool = False
    auth_registration_role: str = "viewer"
    auth_token_ttl_seconds: int = 86_400
    # P1-3: When True, only email-verified users can authenticate.
    auth_require_verified_email: bool = False
    # P1-6: When True, session tokens are bound to the client IP present at
    # login.  Rotating IPs (mobile, VPN) will cause re-auth; set False where
    # that friction is unacceptable.
    auth_bind_token_to_ip: bool = False
    # Per-IP sliding-window cap on /auth/login + /auth/register attempts.
    # Tunes brute-force friction; set ``auth_rate_limit_enabled=False`` to
    # disable entirely (e.g. when fronted by a WAF that already throttles).
    # Root log level + structured JSON logging (H4). JSON is the production
    # posture (log aggregators index request_id/org_id/user_id/trace_id);
    # operators can keep human-readable text with ``log_json=False``.
    log_level: str = "INFO"
    log_json: bool = True
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_per_minute: int = 10
    # Per-(path, IP) sliding-window cap on public /webhook/{path} ingress (H5).
    # Protects against a single sender hammering an unauthenticated webhook URL
    # into a run-queue flood. Set ``webhook_rate_limit_enabled=False`` when a
    # WAF/CDN already throttles ingress.
    webhook_rate_limit_enabled: bool = True
    webhook_rate_limit_per_minute: int = 120
    # Per-workflow run rate limit (runs/minute).  0 = unlimited.  Applied at
    # start_run admission — bursts above this ceiling are 429-rejected.
    workflow_run_rate_per_minute: int = 0
    # Lower limit for the editor test URL (/webhook-test/*).  The listen gate
    # already prevents unauthorized callers, but within an active listen window
    # a tighter cap guards against accidental or deliberate flooding.
    webhook_test_rate_limit_per_minute: int = 30
    # Number of trusted reverse-proxy hops in front of the API.  When > 0 the
    # rate limiter reads the real client IP from X-Forwarded-For (skipping the
    # last N entries which belong to the proxies).  Leave at 0 for direct
    # exposure or when the proxy is not trusted to set that header correctly.
    trusted_proxy_count: int = 0
    secret_key: str = DEFAULT_SECRET_KEY
    # KMS provider for master KEK encryption. "env" (default) uses the legacy
    # Fernet key derived from SECRET_KEY. "vault", "aws", and "gcp" delegate
    # to external key-management services (requires EXTERNAL_KMS feature).
    kms_provider: Literal["env", "vault", "aws", "gcp"] = "env"
    # HashiCorp Vault Transit engine settings (used when kms_provider="vault").
    vault_url: str | None = None
    vault_token: str | None = None
    vault_transit_mount: str = "transit"
    vault_transit_key: str = "noodle-master"
    # AWS KMS settings (used when kms_provider="aws").
    # AWS credentials come from the standard boto3 chain (env vars, IAM role, profile).
    aws_kms_key_id: str | None = None
    aws_kms_region: str = "us-east-1"
    # GCP Cloud KMS settings (used when kms_provider="gcp").
    # GCP credentials come from Application Default Credentials (ADC).
    gcp_kms_key_name: str | None = None  # projects/*/locations/*/keyRings/*/cryptoKeys/* (NOT cryptoKeyVersions)
    # Shared secret the worker presents to call /internal/* endpoints.
    # Blank = no check (fine for local dev where only your machine reaches
    # the API). Set this when exposing the API to anything else.
    internal_api_token: str = ""
    # Public API base URL used to build OAuth redirect URIs. Blank falls back
    # to the incoming request URL, which is fine for local dev/tests but should
    # be explicit behind production proxies.
    oauth_redirect_base_url: str = ""
    # Public API base URL used for remote runners and provider webhook callback
    # URLs. Blank falls back to localhost in non-request lifecycle paths.
    public_api_url: str = ""
    # Optional external OAuth 2.1 authorization-server issuer used by remote
    # MCP clients. Noodle remains the protected resource and also supports
    # org-scoped personal access tokens for preconfigured clients.
    mcp_authorization_server_url: str = ""
    mcp_oauth_introspection_url: str = ""
    mcp_oauth_client_id: str = ""
    mcp_oauth_client_secret: str = ""
    # C3: Session hardening — httpOnly cookie auth + CSRF + WS tickets.
    # When auth_required=True, the SPA can authenticate via either:
    #   1. Bearer token in Authorization header (existing, unchanged)
    #   2. httpOnly session cookie set by POST /auth/login or /auth/register
    # Cookie-based sessions require a CSRF double-submit token on state-changing
    # requests; Bearer auth is CSRF-safe and exempt.
    session_cookie_name: str = "noodle_session"
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["strict", "lax", "none"] = "lax"
    csrf_cookie_name: str = "noodle_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    # One-time WS ticket TTL (seconds). Browser fetches a short-lived ticket
    # via POST /auth/ws-ticket, then passes ?ticket=<token> on the WS URL.
    # Avoids putting Bearer/session tokens in server access logs.
    ws_ticket_ttl_seconds: int = 30

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    slack_oauth_client_id: str = ""
    slack_oauth_client_secret: str = ""
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""

    @model_validator(mode="after")
    def _harden_multi_tenant_defaults(self) -> "Settings":
        """M7: multi-tenant SaaS should not silently deploy risky nodes.

        When multi-tenancy is on and the operator has *not* explicitly chosen a
        policy, raise the default from the single-tenant ``warn`` to
        ``require_approval`` so deploying a Code/HTTP-bearing workflow needs an
        explicit acknowledgement. An explicit ``unsafe_node_policy`` (env or
        kwarg) is always honoured — it appears in ``model_fields_set``.
        """
        if (
            self.multi_tenancy_enabled
            and "unsafe_node_policy" not in self.model_fields_set
            and self.unsafe_node_policy == "warn"
        ):
            object.__setattr__(self, "unsafe_node_policy", "require_approval")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.runtime_mode == "production"

    def dispatch_topology_errors(self) -> list[str]:
        """Hard misconfigurations for split dispatch topologies. Unlike
        ``runtime_warnings()`` these abort startup: a worker/disabled process
        that silently fell back to in-process events or SQLite leasing would
        lose runs, not just degrade."""
        if self.dispatch_role == "inline":
            return []
        errors: list[str] = []
        if self.queue_backend != "redis":
            errors.append(
                f"dispatch_role={self.dispatch_role} requires queue_backend=redis "
                "so run events reach API replicas across processes."
            )
        if not self.database_url.startswith("postgresql"):
            errors.append(
                f"dispatch_role={self.dispatch_role} requires a PostgreSQL "
                "database_url (SKIP LOCKED queue leasing)."
            )
        return errors

    def security_startup_errors(self) -> list[str]:
        """Hard, fail-closed security misconfigurations that abort startup.

        Unlike ``runtime_warnings()`` (advisory, production-mode only, surfaced
        via /ops/runtime-mode), these always run and raise — the same treatment
        ``dispatch_topology_errors()`` gets — because shipping them is
        catastrophic, not merely degraded (AUTH-1/AUTH-3):

        * default ``secret_key`` → token-signing HMAC and the credential master
          KEK are public: anyone can forge a session token for any user and
          decrypt every stored credential.
        * blank ``internal_api_token`` in a split topology → unauthenticated
          worker-level access to ``/internal/*``.

        ``runtime_allow_insecure=True`` is the explicit, logged escape hatch for
        operators who knowingly accept this (e.g. a throwaway local instance).
        Auth-disabled single-user dev is unaffected: the guard only trips when
        an auth/tenancy boundary is actually being relied upon.
        """
        if self.runtime_allow_insecure:
            return []
        errors: list[str] = []
        boundary_enforced = self.auth_required or self.multi_tenancy_enabled
        if self.secret_key == DEFAULT_SECRET_KEY and boundary_enforced:
            errors.append(
                "SECRET_KEY is the built-in default while auth/multi-tenancy is "
                "enabled: session tokens are forgeable and stored credentials are "
                "decryptable by anyone. Set a strong random SECRET_KEY (or "
                "RUNTIME_ALLOW_INSECURE=true to override for a trusted local run)."
            )
        if not self.internal_api_token and self.dispatch_role != "inline":
            errors.append(
                "INTERNAL_API_TOKEN is empty in a split dispatch topology "
                f"(dispatch_role={self.dispatch_role}): /internal/* would accept "
                "unauthenticated worker-level calls. Set a strong shared secret "
                "(or RUNTIME_ALLOW_INSECURE=true to override)."
            )
        if "*" in self.cors_origin_list and boundary_enforced:
            # The CORS middleware sends ``Access-Control-Allow-Credentials: true``;
            # pairing that with a wildcard origin lets *any* site drive
            # credentialed cross-origin requests against an authenticated API
            # (M1). Browsers reject the combination outright, so it is never a
            # working config — only a footgun. Fail closed.
            errors.append(
                "CORS allows a wildcard origin ('*') while auth/multi-tenancy is "
                "enabled and credentials are sent: any browser origin could make "
                "authenticated cross-origin requests. Set cors_origins to the "
                "explicit list of allowed frontend URLs (or "
                "RUNTIME_ALLOW_INSECURE=true to override)."
            )
        if self.mcp_authorization_server_url and not self.mcp_oauth_introspection_url:
            errors.append(
                "MCP_AUTHORIZATION_SERVER_URL is set without "
                "MCP_OAUTH_INTROSPECTION_URL; OAuth access tokens could not be validated."
            )
        # KMS provider validation: external providers require certain fields.
        if self.kms_provider == "vault" and not self.vault_url:
            errors.append(
                "kms_provider=vault requires VAULT_URL to be set. "
                "Also set VAULT_TOKEN, VAULT_TRANSIT_MOUNT, and VAULT_TRANSIT_KEY."
            )
        if self.kms_provider == "vault" and not self.vault_token:
            errors.append(
                "kms_provider=vault requires VAULT_TOKEN to be set."
            )
        if self.kms_provider == "aws" and not self.aws_kms_key_id:
            errors.append(
                "kms_provider=aws requires AWS_KMS_KEY_ID to be set (key ID, ARN, or alias)."
            )
        if self.kms_provider == "gcp" and not self.gcp_kms_key_name:
            errors.append(
                "kms_provider=gcp requires GCP_KMS_KEY_NAME to be set "
                "(full resource path of a symmetric CryptoKey)."
            )
        return errors

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
        if self.webhook_role == "inline":
            warnings.append(
                "RUNTIME_MODE=production with webhook_role=inline; inbound "
                "webhook bursts share the API/editor process. Prefer "
                "webhook_role=ingress (the default) so webhook intake is a "
                "dedicated, queue-backed tier."
            )
        if "*" in self.cors_origin_list:
            warnings.append(
                "CORS is configured with a wildcard origin ('*'); any browser "
                "origin can make credentialed requests. Set cors_origins to the "
                "explicit list of allowed frontend URLs."
            )
        if self.secret_key == DEFAULT_SECRET_KEY:
            warnings.append(
                "SECRET_KEY is the default development value; set a strong "
                "random secret in production to prevent token forgery."
            )
        if not self.auth_required:
            warnings.append(
                "auth_required=False in production mode means any request is "
                "accepted without authentication. Set auth_required=True to "
                "enforce login."
            )
        if not self.internal_api_token:
            warnings.append(
                "internal_api_token is empty; any caller that can reach the "
                "/internal/* endpoints has full worker-level access. Set a "
                "strong shared secret for production deployments."
            )
        if self.dispatch_role == "disabled":
            warnings.append(
                "dispatch_role=disabled: agent/kubernetes runner-pool runs "
                "need their WebSocket-terminating API replica to dispatch "
                "them; in an api+worker split those pools stay queued. Use "
                "dispatch_role=control on the API replica (keeps the split — "
                "the worker still runs local/docker) if you use those pools."
            )
        return warnings


settings = Settings()
