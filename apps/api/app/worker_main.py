"""Standalone execution-worker entrypoint (program A1).

``python -m app.worker_main`` runs the execution plane with no HTTP surface:
the durable-queue dispatch loop (leasing local + docker entries), the warm
runtime pool, and the idle reaper. API replicas run dispatch_role=disabled
and only enqueue; run events reach browsers via the Redis-backed broker.

Graceful drain: SIGTERM/SIGINT set queue_drain (stop leasing), wait up to
``queue_dispatch_shutdown_timeout_seconds`` for in-flight runs, then cancel
laggards. On Windows consoles Ctrl+C may skip the drain (asyncio.run tears
down directly) — production workers are Linux.
"""

import asyncio
import contextlib
import logging
import signal

from app import tracing
from app.config import settings
from app.db import engine
from app.redis_client import redis_client
from app.services.environment_builds import run_environment_build_dispatch_loop
from app.services.events import broker, broker_reaper_loop
from app.services.queue import run_queue_dispatch_loop
from app.services.runner import (
    drain_active_runs,
    process_isolator,
    shutdown_active_runs,
)
from app.services.runtime_pool import idle_reaper_loop
from app.services.runtime_pool import pool as runtime_pool
from app.services.sandbox_policy import enforce_sandbox_policy
from app.services.sandbox_pool import init_sandbox
from app.services.sandbox_pool import pool as sandbox_pool
from app.services.stuck_run_detector import stuck_run_detector_loop
from app.tenancy import assert_safe_postgres_role, run_as_system

logger = logging.getLogger("noodle.worker")


def _validate() -> None:
    errors = settings.security_startup_errors()
    errors += settings.dispatch_topology_errors()
    if settings.dispatch_role != "worker":
        errors.append(
            "the worker entrypoint requires DISPATCH_ROLE=worker "
            f"(got {settings.dispatch_role!r})"
        )
    if errors:
        raise SystemExit("worker startup aborted:\n  - " + "\n  - ".join(errors))


def _as_system(loop_fn):
    """Workers operate across ALL orgs — same rationale as main.py's loops."""

    async def system_loop():
        with run_as_system():
            await loop_fn()

    return system_loop


async def _serve_metrics(port: int) -> asyncio.Server:
    """Minimal HTTP responder for GET /metrics. Anything else gets 404."""
    from app.services.metrics import get_metrics_text

    async def _handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            while (await asyncio.wait_for(reader.readline(), timeout=5.0)).strip():
                pass
            if request_line.startswith(b"GET /metrics"):
                body = get_metrics_text().encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain; version=0.0.4\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()

    return await asyncio.start_server(_handle, "0.0.0.0", port)


async def _amain() -> None:
    _validate()
    from app.services.licensing import reconcile_capabilities

    for warning in await reconcile_capabilities():
        logger.warning("licensing: %s", warning)
    await assert_safe_postgres_role(engine)
    # A5: no-ops unless OTEL_ENABLED=true. The worker has no HTTP surface, so
    # only SQLAlchemy gets instrumented; run spans come from the runner hooks.
    tracing.setup_tracing("noodle-worker")
    tracing.instrument_sqlalchemy(engine)
    if settings.artifact_storage_backend == "s3":
        from app.services.s3_artifact_backend import register_s3_backend  # noqa: PLC0415

        register_s3_backend()
    mode = await broker.connect()
    if mode != "redis":
        raise SystemExit(
            "worker requires a reachable Redis event broker "
            f"(REDIS_URL={settings.redis_url}); broker mode was {mode!r}"
        )

    # Phase D: the worker IS the execution plane, so the sandbox policy and
    # probe always apply here (no dispatch_inline gate like main.py).
    enforce_sandbox_policy()
    await init_sandbox()
    metrics_server: asyncio.Server | None = None
    if settings.worker_metrics_port > 0:
        metrics_server = await _serve_metrics(settings.worker_metrics_port)
        logger.info("worker metrics on :%d/metrics", settings.worker_metrics_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(_as_system(run_queue_dispatch_loop)()),
        asyncio.create_task(_as_system(run_environment_build_dispatch_loop)()),
        asyncio.create_task(broker_reaper_loop()),
        asyncio.create_task(_as_system(stuck_run_detector_loop)()),
    ]
    if settings.use_subprocess_runner and settings.runner_idle_seconds > 0:
        tasks.append(asyncio.create_task(_as_system(idle_reaper_loop)()))

    logger.info(
        "noodle worker up (drain timeout %.1fs)",
        settings.queue_dispatch_shutdown_timeout_seconds,
    )
    try:
        await stop.wait()
    finally:
        settings.queue_drain = True
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        drain_timeout = max(settings.queue_dispatch_shutdown_timeout_seconds, 0.0)
        if drain_timeout > 0:
            leftover = await drain_active_runs(drain_timeout)
            if leftover:
                logger.warning(
                    "drain expired with %d active run(s); forcing cancel", leftover
                )
        with contextlib.suppress(Exception):
            await shutdown_active_runs()
        with contextlib.suppress(Exception):
            await runtime_pool.shutdown()
        with contextlib.suppress(Exception):
            await sandbox_pool.flush()
        if metrics_server is not None:
            metrics_server.close()
            with contextlib.suppress(Exception):
                await metrics_server.wait_closed()
        process_isolator.shutdown()
        with contextlib.suppress(Exception):
            tracing.flush()
        with contextlib.suppress(Exception):
            await engine.dispose()
        with contextlib.suppress(Exception):
            await redis_client.aclose()


def main() -> None:
    # Match the API's structured JSON logging (H4) so worker lines correlate
    # with API lines in the same aggregator. Falls back to plain text when the
    # operator sets ``log_json=False``.
    from app.config import settings

    if settings.log_json:
        from app.logging import configure_logging

        configure_logging()
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
