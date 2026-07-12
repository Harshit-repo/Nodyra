"""Run a workflow in a disposable hardened container from the agent.

When a run is dispatched with ``sandbox_required`` and the runner advertises
``capabilities.sandbox``, the agent executes it in a throwaway container
instead of a local subprocess. The container is built per-env from the API's
wheel index (the same ``--find-links`` page the subprocess env build uses), so
a clean Docker host needs no pre-pulled Nodyra image.

Isolation floor (mirrors the platform's
``apps/api/app/services/container_runtime.py::hardening_kwargs`` — the agent
cannot import ``app.*``, so this is a small, deliberately-synced copy):
cap_drop ALL, no-new-privileges, read-only rootfs, tmpfs ``/tmp`` only,
non-root uid, and pids/mem/cpu ceilings.

Network: the run container joins the default bridge (not ``none``) because the
``nodyra_runtime`` inside uploads artifacts back to the API over HTTPS using
the runner token — the same egress the subprocess path uses. The cap-drop /
read-only / non-root floor is the isolation boundary; the image build (which
needs the wheel index) also runs before the run, while network is available.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tarfile
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("nodyra_runner")

EventCallback = Callable[[dict], Awaitable[None]]
CallWorkflow = Callable[[dict], Awaitable[Any]]

# Image schema — bump when the generated Dockerfile below changes shape so a
# stale cached image built from the old recipe is never reused.
SANDBOX_IMAGE_SCHEMA = "v1"

# PEP 508 specifier allowlist (defence-in-depth against shell metacharacters in
# a package name interpolated into the Dockerfile RUN line). Mirrors
# container_runtime._PKG_SPEC_RE.
_PKG_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(\[[A-Za-z0-9._,-]+\])?"
    r"((===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._-]+"
    r"(,(===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._-]+)*)?$"
)
_PY_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+){0,2}$")

_NODYRA_RUNTIME_PACKAGES = ("nodyra-runtime", "nodyra-core", "nodyra-nodes")


def _validate_packages(packages: list[str]) -> list[str]:
    safe: list[str] = []
    for raw in packages:
        spec = str(raw).strip()
        if not spec:
            continue
        if not _PKG_SPEC_RE.match(spec):
            raise ValueError(f"invalid package specifier {spec!r}")
        safe.append(spec)
    return safe


def _validate_python_version(version: str) -> str:
    v = str(version or "3.12").strip()
    if not _PY_VERSION_RE.match(v):
        raise ValueError(f"invalid python_version {version!r}")
    return v


def sandbox_run_kwargs(*, cpu: float, memory_mb: int, pids: int) -> dict[str, Any]:
    """The non-overridable container hardening floor for a sandboxed run."""
    return {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": {"/tmp": "size=256m"},
        "mem_limit": f"{int(memory_mb)}m",
        "nano_cpus": int(float(cpu) * 1_000_000_000),
        "pids_limit": int(pids),
        "init": True,
        "environment": {"HOME": "/tmp"},
    }


def _client():
    import docker  # noqa: PLC0415

    return docker.from_env()


# Dedicated bridge for sandboxed run containers. Isolates them from unrelated
# containers on a shared daemon (the default bridge permits inter-container
# traffic unless the daemon disables icc) while still allowing egress to the
# API for artifact upload — mirrors the platform's ensure_sandbox_network.
SANDBOX_NETWORK = "nodyra-agent-sandbox"


def _ensure_network(client, name: str = SANDBOX_NETWORK) -> str:
    """Get-or-create the dedicated sandbox bridge network. Tolerates a
    concurrent create by re-checking after a failed create.

    Fails closed: if the dedicated network can neither be found nor created,
    raise rather than silently falling back to the default ``bridge`` — that
    bridge permits inter-container traffic and would defeat the isolation this
    network exists to provide.
    """
    try:
        client.networks.get(name)
        return name
    except Exception:  # noqa: BLE001 — NotFound
        pass
    try:
        client.networks.create(name, driver="bridge")
    except Exception as exc:  # noqa: BLE001 — possibly a concurrent create
        try:
            client.networks.get(name)
        except Exception:  # noqa: BLE001
            raise RuntimeError(
                f"cannot provision the isolated sandbox network {name!r}; "
                "refusing to run on the shared default bridge"
            ) from exc
    return name


class _Demuxer:
    """Strips 8-byte multiplex frame headers from a no-TTY attach stream."""

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> bytes:
        self._buf += chunk
        out = b""
        while len(self._buf) >= 8:
            size = int.from_bytes(self._buf[4:8], "big")
            if len(self._buf) < 8 + size:
                break
            out += self._buf[8:8 + size]
            self._buf = self._buf[8 + size:]
        return out


def sandbox_image_tag(env_payload: dict) -> str:
    env_id = str(env_payload.get("id") or "default")
    packages_hash = str(env_payload.get("packages_hash") or "nohash")
    return f"nodyra-sandbox-env:{env_id}-{packages_hash}-{SANDBOX_IMAGE_SCHEMA}"


def _image_dockerfile(python_version: str, packages: list[str], wheel_index_url: str) -> str:
    installs = list(_NODYRA_RUNTIME_PACKAGES) + packages
    find_links = f" --find-links {wheel_index_url}" if wheel_index_url else ""
    return (
        f"FROM python:{python_version}-slim\n"
        f"RUN pip install --no-cache-dir {' '.join(installs)}{find_links}\n"
        "RUN useradd --uid 65533 --create-home --shell /usr/sbin/nologin sbx\n"
        "USER sbx\n"
        'ENTRYPOINT ["python", "-u", "-m", "nodyra_runtime"]\n'
    )


def _build_context(dockerfile: str) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = dockerfile.encode()
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def _ensure_image(client, env_payload: dict, wheel_index_url: str) -> str:
    """Build the per-env sandbox image if absent. Sync — call in executor."""
    tag = sandbox_image_tag(env_payload)
    try:
        client.images.get(tag)
        return tag
    except Exception:  # noqa: BLE001 — NotFound; build below
        pass
    python_version = _validate_python_version(env_payload.get("python_version", "3.12"))
    packages = _validate_packages(env_payload.get("packages") or [])
    dockerfile = _image_dockerfile(python_version, packages, wheel_index_url)
    client.images.build(
        fileobj=_build_context(dockerfile), custom_context=True, tag=tag, rm=True
    )
    logger.info("built sandbox env image %s", tag)
    return tag


async def run_workflow_sandboxed(
    *,
    run_id: str,
    graph: dict,
    cache: dict | None,
    targets: list[str] | None,
    workflow_modules: list[dict],
    on_event: EventCallback,
    env_payload: dict,
    wheel_index_url: str = "",
    artifacts_upload_url: str | None = None,
    artifacts_runner_token: str | None = None,
    call_workflow: CallWorkflow | None = None,
    pause_on_approval: bool = False,
    agent_action_resume: dict | None = None,
    artifact_key_prefix: str = "",
    org_limits: dict | None = None,
    subworkflow_meta: dict | None = None,
) -> str:
    """Execute a run in a hardened disposable container. Returns the status
    string ("success" / "error" / "waiting"), mirroring
    ``process_pool.run_workflow_subprocess``."""
    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001
        await on_event(
            {"type": "run_error", "error": f"sandbox requested but no Docker daemon: {exc}"}
        )
        return "error"

    overrides = (env_payload or {}).get("sandbox") or {}
    spawn_kwargs = sandbox_run_kwargs(
        cpu=overrides.get("cpu", 1.0),
        memory_mb=overrides.get("memory_mb", 1024),
        pids=overrides.get("pids", 256),
    )

    loop = asyncio.get_running_loop()
    try:
        image_tag = await loop.run_in_executor(
            None, _ensure_image, client, env_payload, wheel_index_url
        )
    except Exception as exc:  # noqa: BLE001
        await on_event({"type": "run_error", "error": f"sandbox image build failed: {exc}"})
        return "error"
    network = await loop.run_in_executor(None, _ensure_network, client)
    spawn_kwargs["network"] = network
    # On a custom bridge, ``host.docker.internal`` does not resolve on Linux by
    # default — map it to the host gateway so a sandbox run can reach an API
    # advertised at host.docker.internal (the local-daemon default in
    # _resolve_api_url) to upload artifacts. Harmless on Docker Desktop, where
    # the name already resolves.
    spawn_kwargs["extra_hosts"] = {"host.docker.internal": "host-gateway"}

    run_msg: dict[str, Any] = {
        "type": "run",
        "request_id": run_id,
        "run_id": run_id,
        "graph": graph,
        "cache": cache or None,
        "targets": targets or None,
        "workflow_modules": workflow_modules,
        "pause_on_approval": pause_on_approval,
        "agent_action_resume": agent_action_resume or {},
        "subworkflow_meta": subworkflow_meta or {},
        "artifact_key_prefix": artifact_key_prefix,
        "org_limits": org_limits or {},
    }
    if artifacts_upload_url:
        run_msg["artifacts_upload_url"] = artifacts_upload_url
        run_msg["artifacts_runner_token"] = artifacts_runner_token or ""
    run_line = (json.dumps(run_msg) + "\n").encode()

    container_name = f"nodyra-sbx-{run_id[:12]}"
    callbacks: set[asyncio.Task] = set()
    status = "error"

    async def _write(sock, payload: bytes) -> None:
        await loop.run_in_executor(None, sock.sendall, payload)

    async def _handle_call_workflow(sock, event: dict) -> None:
        callback_id = event.get("callback_id", "")
        try:
            if call_workflow is None:
                raise RuntimeError("no sub-workflow broker on this runner")
            payload = {k: v for k, v in event.items() if k not in ("type", "callback_id")}
            result = await call_workflow(payload)
            await _write(
                sock,
                (json.dumps({"type": "call_workflow_response",
                             "callback_id": callback_id, "result": result}) + "\n").encode(),
            )
        except Exception as exc:  # noqa: BLE001
            await _write(
                sock,
                (json.dumps({"type": "call_workflow_error", "callback_id": callback_id,
                             "error": f"{type(exc).__name__}: {exc}"}) + "\n").encode(),
            )

    try:
        container = await loop.run_in_executor(
            None,
            lambda: client.containers.run(
                image_tag,
                detach=True,
                stdin_open=True,
                remove=False,
                name=container_name,
                **spawn_kwargs,
            ),
        )

        raw = await loop.run_in_executor(
            None,
            lambda: container.attach_socket(
                params={"stdin": True, "stdout": True, "stderr": False, "stream": True}
            ),
        )
        sock = getattr(raw, "_sock", raw)
        await loop.run_in_executor(None, sock.settimeout, 3600.0)

        demux = _Demuxer()
        buf = b""
        sent_run = False
        while True:
            chunk = await loop.run_in_executor(None, sock.recv, 4096)
            if not chunk:
                break
            buf += demux.feed(chunk)
            done = False
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
                if etype == "ready" and not sent_run:
                    await _write(sock, run_line)
                    sent_run = True
                elif etype == "call_workflow":
                    task = asyncio.create_task(_handle_call_workflow(sock, event))
                    callbacks.add(task)
                    task.add_done_callback(callbacks.discard)
                elif etype == "result":
                    if callbacks:
                        await asyncio.gather(*callbacks, return_exceptions=True)
                    status = str(event.get("status", "success"))
                    done = True
                    break
                elif etype == "error":
                    await on_event({"type": "run_error", "error": event.get("error", "")})
                    status = "error"
                    done = True
                    break
                else:
                    await on_event({k: v for k, v in event.items() if k != "request_id"})
            if done:
                break
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("sandboxed run failed run_id=%s", run_id)
        await on_event({"type": "run_error", "error": str(exc)})
        status = "error"
    finally:
        for task in callbacks:
            task.cancel()
        try:
            await loop.run_in_executor(
                None, lambda: client.containers.get(container_name).remove(force=True)
            )
        except Exception:  # noqa: BLE001
            pass

    return status
