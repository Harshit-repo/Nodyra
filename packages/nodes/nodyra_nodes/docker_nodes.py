"""Docker container management nodes."""

from __future__ import annotations

from typing import Any

from nodyra.sdk import node


def _docker():
    try:
        import docker  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Docker requires the `docker` package. "
            "Install with: uv pip install docker"
        ) from exc
    return docker


DEFAULT_TIMEOUT = 60


@node(
    name="Docker List Containers",
    id="docker_list_containers",
    category="DevOps",
    icon="brand:docker",
    params={
        "all": {
            "description": "Include stopped/exited containers.",
        },
        "limit": {
            "description": "Maximum containers to return.",
            "placeholder": "50",
        },
    },
)
def docker_list_containers(input: Any = None, all: bool = False, limit: int = 50) -> dict[str, Any]:
    """List Docker containers on the host."""
    client = _docker().from_env()
    containers = client.containers.list(
        all=bool(all),
        limit=max(1, min(1000, int(limit or 50))),
    )
    return {
        "containers": [
            {
                "id": c.id,
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else str(c.image),
                "status": c.status,
                "state": c.state,
                "ports": c.ports,
                "created": str(c.attrs.get("Created", "")),
            }
            for c in containers
        ],
        "count": len(containers),
    }


@node(
    name="Docker Run Container",
    id="docker_run_container",
    category="DevOps",
    icon="brand:docker",
    description="Run a Docker container. WARNING: Only use in single-tenant or isolated deployments — this node executes arbitrary images with access to the Docker daemon.",
    params={
        "image": {
            "placeholder": "python:3.12-slim",
            "description": "Container image to run.",
        },
        "command": {
            "placeholder": "python -c 'print(\"hello\")'",
            "description": "Command to run inside the container.",
        },
        "detach": {
            "description": "Run in background (detached mode).",
        },
        "env": {
            "key_value": True,
            "description": "Environment variables.",
        },
        "timeout": {
            "placeholder": "60",
            "description": "Max wait seconds for attached mode.",
        },
    },
)
def docker_run_container(
    input: Any = None,
    image: str = "",
    command: str = "",
    detach: bool = False,
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run a Docker container."""
    if not image:
        raise ValueError("docker_run_container: image is required")
    client = _docker().from_env()
    kwargs: dict[str, Any] = {
        "image": image,
        "detach": bool(detach),
    }
    if command:
        kwargs["command"] = command
    if env:
        kwargs["environment"] = env

    container = client.containers.run(**kwargs)
    if detach:
        return {
            "container_id": container.id,
            "name": container.name,
            "status": container.status,
        }

    timeout_s = max(1, int(timeout or DEFAULT_TIMEOUT))
    result = container.wait(timeout=timeout_s)
    logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
    container.remove()
    return {
        "exit_code": result.get("StatusCode", -1),
        "logs": logs,
        "container_id": container.id,
    }


@node(
    name="Docker Stop Container",
    id="docker_stop_container",
    category="DevOps",
    icon="brand:docker",
    params={
        "container_id": {
            "placeholder": "Container ID or name",
            "description": "ID or name of the container to stop.",
        },
        "timeout": {
            "placeholder": "10",
            "description": "Seconds to wait before force-killing.",
        },
    },
)
def docker_stop_container(
    input: Any = None,
    container_id: str = "",
    timeout: int = 10,
) -> dict[str, Any]:
    """Stop a running Docker container."""
    if not container_id:
        raise ValueError("docker_stop_container: container_id is required")
    client = _docker().from_env()
    container = client.containers.get(container_id)
    container.stop(timeout=max(0, int(timeout or 10)))
    container.reload()
    return {
        "container_id": container.id,
        "name": container.name,
        "status": container.status,
    }


__all__ = [
    "docker_list_containers",
    "docker_run_container",
    "docker_stop_container",
]
