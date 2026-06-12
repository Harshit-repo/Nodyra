"""Docker runner-pool provider (split from remote_dispatch.py, A2).

The host drives containers via the Docker SDK: an image is built per-env
(tagged ``noodle-env:{env_id}-{packages_hash}``) and each run spawns a
container with stdin/stdout piped to the ``noodle_runtime`` JSON protocol.
Self-contained — needs no WebSocket terminating in this process, so a
standalone worker (dispatch_role=worker) can execute docker-pool runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import settings
from app.models import RunnerPool
from app.services.container_runtime import (
    ensure_docker_image,
    image_tag_for,
)
from app.services.executors.base import EventCallback

logger = logging.getLogger("app.services.remote_dispatch")


async def assign_docker_run(
    session_factory,
    run_id: str,
    pool_id: str,
    env_payload: dict,
    graph: dict,
    cache: dict | None,
    targets: list[str] | None,
    workflow_modules: list[dict],
    on_event: EventCallback,
    pause_on_approval: bool = False,
    agent_action_resume: dict | None = None,
    subworkflow_meta: dict | None = None,
) -> str:
    try:
        import docker  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "Docker provider requires the 'docker' package: pip install docker"
        ) from exc

    async with session_factory() as session:
        pool = await session.get(RunnerPool, pool_id)
        cfg = pool.provider_config if pool else {}

    docker_host = cfg.get("docker_host")
    client = (
        docker.from_env()
        if not docker_host
        else docker.DockerClient(base_url=docker_host)
    )

    image_tag = image_tag_for(env_payload)
    network = cfg.get("network", "bridge")

    loop = asyncio.get_running_loop()
    # Ensure image exists (build if not) — runs in a thread executor.
    await loop.run_in_executor(
        None, ensure_docker_image, client, image_tag, env_payload
    )

    container_name = f"noodle-run-{run_id[:12]}"
    run_msg = json.dumps({
        "type": "run",
        "request_id": run_id,
        "graph": graph,
        "cache": cache or {},
        "targets": targets or [],
        "workflow_modules": workflow_modules,
        "pause_on_approval": pause_on_approval,
        "agent_action_resume": agent_action_resume or {},
        "subworkflow_meta": subworkflow_meta or {},
    }) + "\n"

    node_events: dict[str, dict] = {}
    status = "error"

    try:
        container = await loop.run_in_executor(
            None,
            lambda: client.containers.run(
                image_tag,
                detach=True,
                stdin_open=True,
                remove=False,
                name=container_name,
                network=network,
            ),
        )

        # Attach to the container and drive the noodle_runtime protocol.
        sock = await loop.run_in_executor(None, lambda: container.attach_socket(
            params={"stdin": True, "stdout": True, "stderr": False, "stream": True}
        ))

        # Write the run message to stdin.
        await loop.run_in_executor(None, sock._sock.sendall, run_msg.encode())

        # Bound the recv loop so a crashed container never hangs the caller.
        _recv_timeout = settings.workflow_run_timeout_seconds or 3600.0
        await loop.run_in_executor(
            None, sock._sock.settimeout, _recv_timeout
        )

        # Read events line by line until result.
        buf = b""
        while True:
            chunk = await loop.run_in_executor(None, sock._sock.recv, 4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")
                if etype == "ready":
                    # Send the run message now that the runtime is ready.
                    await loop.run_in_executor(None, sock._sock.sendall, run_msg.encode())
                elif etype in (
                    "node_started",
                    "node_finished",
                    "agent_action_requested",
                    "agent_tool_started",
                    "agent_tool_approval_required",
                    "agent_tool_auto_approved",
                    "agent_tool_finished",
                    "agent_action_completed",
                    "agent_tool_approval_decided",
                    "run_error",
                    "run_cancelled",
                    "module_error",
                ):
                    await on_event(event)
                    if etype == "node_finished":
                        nid = event.get("node_id")
                        if nid:
                            node_events[nid] = event
                elif etype == "result":
                    status = str(event.get("status", "error"))
                    break
            else:
                continue
            break

    except Exception as exc:  # noqa: BLE001
        logger.exception("docker run failed run_id=%s: %s", run_id, exc)
        await on_event({"type": "run_error", "error": str(exc)})
        status = "error"
    finally:
        try:
            await loop.run_in_executor(
                None,
                lambda: client.containers.get(container_name).remove(force=True),
            )
        except Exception:  # noqa: BLE001
            pass

    return status
