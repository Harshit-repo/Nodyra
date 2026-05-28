"""Remote runner dispatch service.

Three execution provider paths:

* ``agent``      — outbound-WS daemon on a VM/EC2 instance. The runner
  connects to ``/ws/runners/{runner_id}`` and accepts ``run_assigned``
  messages. Envs are built on the remote machine via ``uv`` and cached
  between runs by ``(env_id, packages_hash)``.

* ``docker``     — API drives containers via the Docker SDK. An image is
  built per-env (tagged ``noodle-env:{env_id}-{packages_hash}``) and each
  run spawns ``docker run --rm -i`` with stdin/stdout piped to the
  ``noodle_runtime`` JSON protocol.

* ``kubernetes`` — API creates a K8s Job whose pod connects back via the
  same WS agent protocol. Used for cloud-native deployments.

Run queueing: when no runner has capacity, the run row is set to
``status="queued"`` and a background ``queue_dispatch_loop`` retries
periodically or when a runner becomes available.

Cloud provisioning: when the pool's ``provider_config`` includes
``cloud_provider="aws"`` (and credentials), the queue loop will
auto-provision EC2 instances when the queue grows and idle-terminate them
after ``idle_terminate_seconds``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Run, Runner, RunnerPool
from noodle.serialization import serialize_value

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None]]

# How long to wait for a queued run before failing it.
_QUEUE_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Agent connection state
# ---------------------------------------------------------------------------

@dataclass
class _AgentConnection:
    runner_id: str
    ws: Any  # starlette WebSocket
    active_runs: dict[str, asyncio.Future] = field(default_factory=dict)
    _send_lock: asyncio.Lock | None = field(default=None, init=False)

    @property
    def send_lock(self) -> asyncio.Lock:
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        return self._send_lock

    async def send(self, msg: dict) -> None:
        async with self.send_lock:
            try:
                await self.ws.send_text(json.dumps(msg))
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

class RemoteDispatcher:
    def __init__(self) -> None:
        self._agents: dict[str, _AgentConnection] = {}
        # run_id → on_event callback; set during assign_run, cleared on finish
        self._run_callbacks: dict[str, EventCallback] = {}
        self._queue_loop_task: asyncio.Task | None = None
        # Lazily initialized lock — created on first async access so it binds
        # to the running event loop, not the module-import-time loop.
        self._lock_: asyncio.Lock | None = None
        # K8s single-run agents: run_id → future (resolved on run_finished)
        self._k8s_futures: dict[str, asyncio.Future] = {}
        # K8s single-run agents: run_id → run_assigned payload to deliver on connect
        self._k8s_payloads: dict[str, dict] = {}

    @property
    def _lock(self) -> asyncio.Lock:
        if self._lock_ is None:
            self._lock_ = asyncio.Lock()
        return self._lock_

    def _reset_loop_state(self) -> None:
        """Drop any asyncio objects bound to a now-gone event loop.

        Called at the start of ``queue_dispatch_loop`` so each lifespan
        (including the test-client lifespan) gets fresh primitives on the
        correct loop.
        """
        self._lock_ = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def assign_run(
        self,
        run_id: str,
        pool_id: str,
        env_payload: dict,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        workflow_modules: list[dict],
        on_event: EventCallback,
    ) -> str:
        """Dispatch a run to the pool. Returns the final run status string."""
        async with SessionLocal() as session:
            pool = await session.get(RunnerPool, pool_id)
            if pool is None:
                raise ValueError(f"runner pool '{pool_id}' not found")
            provider = pool.provider

        if provider == "agent":
            return await self._assign_agent_run(
                run_id, pool_id, env_payload, graph, cache, targets, workflow_modules, on_event
            )
        if provider == "docker":
            return await self._assign_docker_run(
                run_id, pool_id, env_payload, graph, cache, targets, workflow_modules, on_event
            )
        if provider == "kubernetes":
            return await self._assign_k8s_run(
                run_id, pool_id, env_payload, graph, cache, targets, workflow_modules, on_event
            )
        raise ValueError(f"unknown runner pool provider '{provider}'")

    async def handle_runner_connect(self, runner_id: str, ws: Any) -> None:
        """Called by the WS route when an agent runner connects.

        Runs the receive loop until the WebSocket closes.
        """
        conn = _AgentConnection(runner_id=runner_id, ws=ws)
        async with self._lock:
            self._agents[runner_id] = conn

        logger.info("runner connected runner_id=%s", runner_id)
        async with SessionLocal() as session:
            runner = await session.get(Runner, runner_id)
            if runner is not None:
                runner.status = "online"
                runner.last_seen_at = datetime.now(UTC)
                await session.commit()

        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_agent_message(conn, msg)
        except Exception:  # noqa: BLE001 - any disconnect / protocol error
            pass
        finally:
            await self.handle_runner_disconnect(runner_id)

    async def handle_runner_disconnect(self, runner_id: str) -> None:
        async with self._lock:
            conn = self._agents.pop(runner_id, None)

        logger.info("runner disconnected runner_id=%s", runner_id)
        async with SessionLocal() as session:
            runner = await session.get(Runner, runner_id)
            if runner is not None:
                runner.status = "offline"
                await session.commit()

        if conn is None:
            return

        # Fail all in-flight runs on this connection.
        for run_id, future in list(conn.active_runs.items()):
            if not future.done():
                future.set_exception(
                    RuntimeError("runner disconnected mid-run")
                )
            cb = self._run_callbacks.pop(run_id, None)
            if cb is not None:
                try:
                    await cb({
                        "type": "run_error",
                        "run_id": run_id,
                        "error": "runner disconnected",
                    })
                except Exception:  # noqa: BLE001
                    pass

    async def cancel_remote_run(self, run_id: str, runner_id: str) -> None:
        async with self._lock:
            conn = self._agents.get(runner_id)
        if conn is not None:
            await conn.send({"type": "run_cancel", "run_id": run_id})

    async def queue_run(self, run_id: str) -> None:
        """Mark a run as queued in the DB."""
        async with SessionLocal() as session:
            run = await session.get(Run, run_id)
            if run is not None:
                run.status = "queued"
                await session.commit()

    def signal_capacity(self) -> None:
        """No-op placeholder; the queue loop polls on a fixed interval."""

    async def shutdown(self) -> None:
        for conn in list(self._agents.values()):
            try:
                await conn.ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._agents.clear()

    # ------------------------------------------------------------------
    # Agent provider
    # ------------------------------------------------------------------

    async def _assign_agent_run(
        self,
        run_id: str,
        pool_id: str,
        env_payload: dict,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        workflow_modules: list[dict],
        on_event: EventCallback,
    ) -> str:
        conn = await self._pick_agent(pool_id)
        if conn is None:
            await self.queue_run(run_id)
            # Provision cloud instances if configured
            await self._maybe_provision(pool_id)
            raise _QueuedError(f"run {run_id} queued — no available runners in pool {pool_id}")

        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        conn.active_runs[run_id] = future
        self._run_callbacks[run_id] = on_event

        # Update runner current_runs and persist the runner id on the run so
        # cancel_run can route a run_cancel to this agent.
        async with SessionLocal() as session:
            runner = await session.get(Runner, conn.runner_id)
            if runner is not None:
                runner.current_runs = max(0, runner.current_runs) + 1
                runner.status = "busy"
            run = await session.get(Run, run_id)
            if run is not None:
                run.runner_id = conn.runner_id
            await session.commit()

        await conn.send({
            "type": "run_assigned",
            "run_id": run_id,
            "env": env_payload,
            "graph": graph,
            "cache": cache or {},
            "targets": targets or [],
            "workflow_modules": workflow_modules,
        })

        try:
            status = await asyncio.wait_for(future, timeout=_QUEUE_TTL_SECONDS)
        except TimeoutError:
            conn.active_runs.pop(run_id, None)
            self._run_callbacks.pop(run_id, None)
            status = "error"
        finally:
            async with SessionLocal() as session:
                runner = await session.get(Runner, conn.runner_id)
                if runner is not None:
                    runner.current_runs = max(0, runner.current_runs - 1)
                    if runner.current_runs == 0:
                        runner.status = "online"
                    await session.commit()
            self.signal_capacity()

        return status

    async def _pick_agent(self, pool_id: str) -> _AgentConnection | None:
        """Return an available agent connection in this pool, or None."""
        async with SessionLocal() as session:
            runners = (
                await session.scalars(
                    select(Runner).where(
                        Runner.pool_id == pool_id,
                        Runner.status.in_(["online", "busy"]),
                    )
                )
            ).all()
            runner_ids = {r.id for r in runners if r.current_runs < r.max_concurrent_runs}

        async with self._lock:
            for runner_id, conn in self._agents.items():
                if runner_id in runner_ids and len(conn.active_runs) < 1:
                    return conn
        return None

    async def _handle_agent_message(self, conn: _AgentConnection, msg: dict) -> None:
        mtype = msg.get("type")
        run_id = msg.get("run_id")

        if mtype == "runner_hello":
            async with SessionLocal() as session:
                runner = await session.get(Runner, conn.runner_id)
                if runner is not None:
                    caps = msg.get("capabilities") or {}
                    runner.capabilities = caps
                    if "max_concurrent" in caps:
                        runner.max_concurrent_runs = int(caps["max_concurrent"])
                    cached = msg.get("cached_env_ids") or []
                    runner.cached_env_ids = list(cached)
                    runner.last_seen_at = datetime.now(UTC)
                    await session.commit()

        elif mtype == "env_building":
            if run_id:
                cb = self._run_callbacks.get(run_id)
                if cb:
                    await cb({"type": "env_building", "run_id": run_id,
                              "env_id": msg.get("env_id")})

        elif mtype == "env_ready":
            env_id = msg.get("env_id")
            packages_hash = msg.get("packages_hash", "")
            if env_id and packages_hash:
                cache_key = f"{env_id}-{packages_hash}"
                async with SessionLocal() as session:
                    runner = await session.get(Runner, conn.runner_id)
                    if runner is not None:
                        existing = list(runner.cached_env_ids)
                        if cache_key not in existing:
                            existing.append(cache_key)
                            runner.cached_env_ids = existing
                        runner.last_seen_at = datetime.now(UTC)
                        await session.commit()

        elif mtype == "env_error":
            if run_id:
                fut = conn.active_runs.pop(run_id, None)
                if fut and not fut.done():
                    fut.set_exception(
                        RuntimeError(f"env build failed: {msg.get('error', 'unknown')}")
                    )
                self._run_callbacks.pop(run_id, None)

        elif mtype == "run_event":
            if run_id:
                event = msg.get("event") or {}
                cb = self._run_callbacks.get(run_id)
                if cb:
                    await cb(event)

        elif mtype == "run_finished":
            if run_id:
                status = str(msg.get("status") or "error")
                fut = conn.active_runs.pop(run_id, None)
                if fut and not fut.done():
                    fut.set_result(status)
                self._run_callbacks.pop(run_id, None)

        elif mtype == "call_workflow":
            # The runner's runtime hit an execute_workflow node; resolve the
            # sub-workflow host-side and send the result back. Run it on a task
            # so the agent receive loop keeps draining.
            asyncio.create_task(self._resolve_remote_subworkflow(conn, msg))

        elif mtype == "pong":
            async with SessionLocal() as session:
                runner = await session.get(Runner, conn.runner_id)
                if runner is not None:
                    runner.last_seen_at = datetime.now(UTC)
                    await session.commit()

    async def _resolve_remote_subworkflow(
        self, conn: _AgentConnection, msg: dict
    ) -> None:
        """Run a sub-workflow host-side for a remote runner and reply.

        Reuses the host's ``_call_sub_workflow`` (deferred import to avoid the
        runner.py ↔ remote_dispatch.py cycle). With no ``parent_env_id`` it
        always returns a concrete leaf result — never an inline sentinel — so
        the value serializes cleanly back over the WS.
        """
        from app.services.runner import _call_sub_workflow  # noqa: PLC0415

        callback_id = msg.get("callback_id", "")
        try:
            result = await _call_sub_workflow(
                str(msg.get("workflow_id") or ""), msg.get("input")
            )
            await conn.send({
                "type": "call_workflow_response",
                "callback_id": callback_id,
                "result": serialize_value(result),
            })
        except Exception as exc:  # noqa: BLE001 - surface back to the runner
            await conn.send({
                "type": "call_workflow_error",
                "callback_id": callback_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    # ------------------------------------------------------------------
    # Docker provider
    # ------------------------------------------------------------------

    async def _assign_docker_run(
        self,
        run_id: str,
        pool_id: str,
        env_payload: dict,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        workflow_modules: list[dict],
        on_event: EventCallback,
    ) -> str:
        try:
            import docker  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "Docker provider requires the 'docker' package: pip install docker"
            ) from exc

        async with SessionLocal() as session:
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

        loop = asyncio.get_event_loop()
        # Ensure image exists (build if not) — runs in a thread executor.
        await loop.run_in_executor(
            None, self._ensure_docker_image, client, image_tag, env_payload
        )

        container_name = f"noodle-run-{run_id[:12]}"
        run_msg = json.dumps({
            "type": "run",
            "request_id": run_id,
            "graph": graph,
            "cache": cache or {},
            "targets": targets or [],
            "workflow_modules": workflow_modules,
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
                    elif etype in ("node_started", "node_finished", "run_error",
                                   "run_cancelled", "module_error"):
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

    def _ensure_docker_image(self, client: Any, image_tag: str, env_payload: dict) -> None:
        """Build a Docker image for this env if it doesn't exist. Sync — runs in executor."""
        try:
            client.images.get(image_tag)
            return  # Cache hit
        except Exception:  # noqa: BLE001
            pass  # Image not found, build it

        python_version = env_payload.get("python_version", "3.12")
        packages = env_payload.get("packages") or []
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

    # ------------------------------------------------------------------
    # Kubernetes provider
    # ------------------------------------------------------------------

    async def _assign_k8s_run(
        self,
        run_id: str,
        pool_id: str,
        env_payload: dict,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        workflow_modules: list[dict],
        on_event: EventCallback,
    ) -> str:
        """Create a K8s Job whose pod connects back as a single-run agent."""
        try:
            from kubernetes_asyncio import (
                client as k8s_client,  # type: ignore[import-untyped]  # noqa: PLC0415
            )
            from kubernetes_asyncio import (
                config as k8s_config,  # type: ignore[import-untyped]  # noqa: PLC0415
            )
        except ImportError as exc:
            raise RuntimeError(
                "Kubernetes provider requires 'kubernetes-asyncio': pip install kubernetes-asyncio"
            ) from exc

        async with SessionLocal() as session:
            pool = await session.get(RunnerPool, pool_id)
            cfg = pool.provider_config if pool else {}

        kubeconfig_yaml = cfg.get("kubeconfig_yaml")
        namespace = cfg.get("namespace", "noodle")
        image_registry = cfg.get("image_registry", "")
        node_selector = cfg.get("node_selector") or {}

        image_tag = (
            f"noodle-env:{env_payload.get('id', 'default')}"
            f"-{env_payload.get('packages_hash', 'latest')}"
        )
        full_image = f"{image_registry}/{image_tag}" if image_registry else image_tag

        # Generate a one-time run token for this pod.
        from app.services.crypto import create_payload_token  # noqa: PLC0415
        token = create_payload_token({"sub": run_id, "kind": "k8s_run"}, ttl_seconds=3600)

        from app.config import settings as app_settings  # noqa: PLC0415
        api_url = getattr(app_settings, "public_api_url", "http://noodle-api:8000")

        # Load kubeconfig.
        if kubeconfig_yaml:
            import os  # noqa: PLC0415
            import tempfile  # noqa: PLC0415
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                f.write(kubeconfig_yaml)
                kube_path = f.name
            await k8s_config.load_kube_config(config_file=kube_path)
            os.unlink(kube_path)
        else:
            await k8s_config.load_incluster_config()

        batch_v1 = k8s_client.BatchV1Api()
        job_name = f"noodle-run-{run_id[:16]}"

        job_body = k8s_client.V1Job(
            metadata=k8s_client.V1ObjectMeta(name=job_name, namespace=namespace),
            spec=k8s_client.V1JobSpec(
                ttl_seconds_after_finished=300,
                template=k8s_client.V1PodTemplateSpec(
                    spec=k8s_client.V1PodSpec(
                        restart_policy="Never",
                        node_selector=node_selector or None,
                        containers=[
                            k8s_client.V1Container(
                                name="runner",
                                image=full_image,
                                command=[
                                    "python", "-u", "-m",
                                    "noodle_runner_agent.k8s_entrypoint",
                                ],
                                env=[
                                    k8s_client.V1EnvVar(name="NOODLE_API_URL", value=api_url),
                                    k8s_client.V1EnvVar(name="NOODLE_RUN_TOKEN", value=token),
                                    k8s_client.V1EnvVar(name="NOODLE_RUN_ID", value=run_id),
                                ],
                            )
                        ],
                    )
                ),
            ),
        )

        # Register a future that will be resolved when the pod connects back.
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        # Use run_id as runner_id for K8s single-run agents.
        self._run_callbacks[run_id] = on_event
        self._k8s_futures[run_id] = future
        # The pod fetches its run payload over the WS once it connects.
        self._k8s_payloads[run_id] = {
            "type": "run_assigned",
            "run_id": run_id,
            "env": env_payload,
            "graph": graph,
            "cache": cache or {},
            "targets": targets or [],
            "workflow_modules": workflow_modules,
        }

        try:
            await batch_v1.create_namespaced_job(namespace=namespace, body=job_body)
            logger.info("created k8s job %s for run %s", job_name, run_id)
            status = await asyncio.wait_for(future, timeout=_QUEUE_TTL_SECONDS)
        except TimeoutError:
            status = "error"
        except Exception as exc:  # noqa: BLE001
            logger.exception("k8s job creation failed run_id=%s: %s", run_id, exc)
            status = "error"
        finally:
            self._k8s_futures.pop(run_id, None)
            self._k8s_payloads.pop(run_id, None)
            self._run_callbacks.pop(run_id, None)
            # Clean up job
            try:
                await batch_v1.delete_namespaced_job(
                    name=job_name, namespace=namespace,
                    body=k8s_client.V1DeleteOptions(propagation_policy="Background"),
                )
            except Exception:  # noqa: BLE001
                pass

        return status

    async def handle_k8s_runner_connect(self, run_id: str, ws: Any) -> None:
        """Called when a K8s pod connects back as a single-run agent."""
        conn = _AgentConnection(runner_id=run_id, ws=ws)
        async with self._lock:
            self._agents[run_id] = conn

        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type")
                if mtype == "runner_hello":
                    # Deliver the pre-queued run assignment to the pod.
                    payload = self._k8s_payloads.get(run_id)
                    if payload is not None:
                        await conn.send(payload)
                elif mtype == "run_event":
                    rid = msg.get("run_id")
                    if rid:
                        cb = self._run_callbacks.get(rid)
                        if cb:
                            await cb(msg.get("event") or {})
                elif mtype == "run_finished":
                    rid = msg.get("run_id")
                    if rid:
                        fut = getattr(self, "_k8s_futures", {}).pop(rid, None)
                        if fut and not fut.done():
                            fut.set_result(str(msg.get("status") or "error"))
                        self._run_callbacks.pop(rid, None)
        except Exception:  # noqa: BLE001
            pass
        finally:
            async with self._lock:
                self._agents.pop(run_id, None)

    # ------------------------------------------------------------------
    # Cloud provisioning (AWS / GCP / Azure)
    # ------------------------------------------------------------------

    async def _maybe_provision(self, pool_id: str) -> None:
        """Provision a new cloud instance if the pool config supports it."""
        async with SessionLocal() as session:
            pool = await session.get(RunnerPool, pool_id)
            if pool is None:
                return
            cfg = pool.provider_config
            provider = cfg.get("cloud_provider")
            if provider not in ("aws", "gcp", "azure"):
                return
            max_instances = int(cfg.get("max_instances", 0))
            if max_instances <= 0:
                return
            existing = (await session.scalars(
                select(Runner).where(Runner.pool_id == pool_id)
            )).all()

        if len(existing) >= max_instances:
            return

        if provider == "aws":
            await self._provision_aws_instance(pool_id, cfg)
        elif provider == "gcp":
            await self._provision_gcp_instance(pool_id, cfg)
        elif provider == "azure":
            await self._provision_azure_instance(pool_id, cfg)

    def _bootstrap_user_data(self, api_url: str, token: str, runner_id: str) -> str:
        """Cloud-init / startup script that installs and starts the agent."""
        return (
            "#!/bin/bash\n"
            "set -e\n"
            "pip install noodle-runner --quiet\n"
            f"noodle-runner register --api-url {api_url} --token {token} "
            f"--name cloud-{runner_id[:8]}\n"
            "noodle-runner start &\n"
        )

    async def _provision_aws_instance(self, pool_id: str, cfg: dict) -> None:
        """Provision an EC2 instance and register a runner for it."""
        try:
            import boto3  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError:
            logger.warning("boto3 not installed — cannot auto-provision EC2 runners")
            return

        from app.config import settings as app_settings  # noqa: PLC0415
        api_url = getattr(app_settings, "public_api_url", "http://localhost:8000")
        region = cfg.get("region", "us-east-1")
        instance_type = cfg.get("instance_type", "t3.medium")
        ami_id = cfg.get("ami_id", "")
        key_pair = cfg.get("key_pair", "")
        security_groups = cfg.get("security_group_ids") or []
        aws_key = cfg.get("aws_access_key_id", "")
        aws_secret = cfg.get("aws_secret_access_key", "")

        if not ami_id:
            logger.warning("cloud provisioning skipped — no ami_id in pool config")
            return

        runner_id, token = await self._create_runner_and_token(pool_id, "cloud-auto")

        user_data = self._bootstrap_user_data(api_url, token, runner_id)

        import base64  # noqa: PLC0415
        encoded_ud = base64.b64encode(user_data.encode()).decode()

        ec2_kwargs: dict = {"region_name": region}
        if aws_key and aws_secret:
            ec2_kwargs["aws_access_key_id"] = aws_key
            ec2_kwargs["aws_secret_access_key"] = aws_secret

        run_kwargs: dict = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": encoded_ud,
        }
        if key_pair:
            run_kwargs["KeyName"] = key_pair
        if security_groups:
            run_kwargs["SecurityGroupIds"] = security_groups

        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: boto3.client("ec2", **ec2_kwargs).run_instances(**run_kwargs),
            )
            instance_id = resp["Instances"][0]["InstanceId"]
            logger.info("provisioned EC2 instance %s for pool %s", instance_id, pool_id)
            await self._update_runner_instance_id(runner_id, instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("EC2 provisioning failed pool_id=%s: %s", pool_id, exc)

    async def _provision_gcp_instance(self, pool_id: str, cfg: dict) -> None:
        """Provision a GCE instance and register a runner for it.

        ``provider_config`` keys: ``project``, ``zone``, ``machine_type``,
        ``source_image`` (e.g. ``projects/debian-cloud/global/images/family/
        debian-12``), ``network`` (default ``global/networks/default``),
        ``service_account_json`` (optional inline key).
        """
        try:
            from google.cloud import compute_v1  # type: ignore[import-untyped]  # noqa: PLC0415
            from google.oauth2 import (
                service_account,  # type: ignore[import-untyped]  # noqa: PLC0415
            )
        except ImportError:
            logger.warning(
                "google-cloud-compute not installed — cannot auto-provision GCE runners"
            )
            return

        from app.config import settings as app_settings  # noqa: PLC0415
        api_url = getattr(app_settings, "public_api_url", "http://localhost:8000")
        project = cfg.get("project", "")
        zone = cfg.get("zone", "us-central1-a")
        machine_type = cfg.get("machine_type", "e2-medium")
        source_image = cfg.get("source_image", "")
        network = cfg.get("network", "global/networks/default")
        sa_json = cfg.get("service_account_json")

        if not project or not source_image:
            logger.warning(
                "GCP provisioning skipped — project and source_image are required"
            )
            return

        runner_id, token = await self._create_runner_and_token(pool_id, "gcp-auto")
        startup = self._bootstrap_user_data(api_url, token, runner_id)
        instance_name = f"noodle-runner-{runner_id[:12]}"

        def _create() -> None:
            creds = None
            if sa_json:
                import json as _json  # noqa: PLC0415
                creds = service_account.Credentials.from_service_account_info(
                    _json.loads(sa_json) if isinstance(sa_json, str) else sa_json
                )
            client = compute_v1.InstancesClient(credentials=creds)
            instance = compute_v1.Instance(
                name=instance_name,
                machine_type=f"zones/{zone}/machineTypes/{machine_type}",
                disks=[
                    compute_v1.AttachedDisk(
                        boot=True,
                        auto_delete=True,
                        initialize_params=compute_v1.AttachedDiskInitializeParams(
                            source_image=source_image
                        ),
                    )
                ],
                network_interfaces=[
                    compute_v1.NetworkInterface(
                        network=network,
                        access_configs=[
                            compute_v1.AccessConfig(name="External NAT")
                        ],
                    )
                ],
                metadata=compute_v1.Metadata(
                    items=[compute_v1.Items(key="startup-script", value=startup)]
                ),
            )
            client.insert(project=project, zone=zone, instance_resource=instance)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _create)
            logger.info("provisioned GCE instance %s for pool %s", instance_name, pool_id)
            await self._update_runner_instance_id(runner_id, instance_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GCE provisioning failed pool_id=%s: %s", pool_id, exc)

    async def _provision_azure_instance(self, pool_id: str, cfg: dict) -> None:
        """Provision an Azure VM and register a runner for it.

        ``provider_config`` keys: ``subscription_id``, ``resource_group``,
        ``location``, ``vm_size``, ``image`` (e.g.
        ``Canonical:0001-com-ubuntu-server-jammy:22_04-lts:latest``),
        ``admin_username``, ``admin_password`` or ``ssh_public_key``,
        ``subnet_id``. Uses ``DefaultAzureCredential`` unless a service
        principal is supplied via ``tenant_id``/``client_id``/``client_secret``.
        """
        try:
            from azure.identity import (  # type: ignore[import-untyped]  # noqa: PLC0415
                ClientSecretCredential,
                DefaultAzureCredential,
            )
            from azure.mgmt.compute import (  # type: ignore[import-untyped]  # noqa: PLC0415
                ComputeManagementClient,
            )
        except ImportError:
            logger.warning(
                "azure SDK not installed — cannot auto-provision Azure VMs "
                "(need azure-identity + azure-mgmt-compute)"
            )
            return

        from app.config import settings as app_settings  # noqa: PLC0415
        api_url = getattr(app_settings, "public_api_url", "http://localhost:8000")
        sub = cfg.get("subscription_id", "")
        rg = cfg.get("resource_group", "")
        location = cfg.get("location", "eastus")
        vm_size = cfg.get("vm_size", "Standard_B2s")
        image = cfg.get("image", "")
        admin_user = cfg.get("admin_username", "noodle")
        admin_password = cfg.get("admin_password")
        subnet_id = cfg.get("subnet_id", "")

        if not (sub and rg and image and subnet_id):
            logger.warning(
                "Azure provisioning skipped — subscription_id, resource_group, "
                "image and subnet_id are required"
            )
            return

        runner_id, token = await self._create_runner_and_token(pool_id, "azure-auto")
        import base64  # noqa: PLC0415
        custom_data = base64.b64encode(
            self._bootstrap_user_data(api_url, token, runner_id).encode()
        ).decode()
        vm_name = f"noodle-runner-{runner_id[:12]}"

        def _create() -> None:
            if cfg.get("client_secret"):
                cred = ClientSecretCredential(
                    tenant_id=cfg["tenant_id"],
                    client_id=cfg["client_id"],
                    client_secret=cfg["client_secret"],
                )
            else:
                cred = DefaultAzureCredential()
            client = ComputeManagementClient(cred, sub)
            pub, offer, sku, version = (image.split(":") + ["", "", "", ""])[:4]
            poller = client.virtual_machines.begin_create_or_update(
                rg,
                vm_name,
                {
                    "location": location,
                    "hardware_profile": {"vm_size": vm_size},
                    "storage_profile": {
                        "image_reference": {
                            "publisher": pub,
                            "offer": offer,
                            "sku": sku,
                            "version": version or "latest",
                        }
                    },
                    "os_profile": {
                        "computer_name": vm_name,
                        "admin_username": admin_user,
                        "admin_password": admin_password,
                        "custom_data": custom_data,
                    },
                    "network_profile": {
                        "network_interface_configurations": [
                            {
                                "name": f"{vm_name}-nic",
                                "ip_configurations": [
                                    {
                                        "name": f"{vm_name}-ip",
                                        "subnet": {"id": subnet_id},
                                    }
                                ],
                            }
                        ]
                    },
                },
            )
            poller.result()

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _create)
            logger.info("provisioned Azure VM %s for pool %s", vm_name, pool_id)
            await self._update_runner_instance_id(runner_id, vm_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Azure provisioning failed pool_id=%s: %s", pool_id, exc)

    async def _create_runner_and_token(self, pool_id: str, name_prefix: str) -> tuple[str, str]:
        from app.models import Runner  # noqa: PLC0415
        from app.services.crypto import create_payload_token  # noqa: PLC0415
        runner = Runner(
            pool_id=pool_id,
            name=f"{name_prefix}-{_uuid_hex()[:8]}",
            status="offline",
        )
        async with SessionLocal() as session:
            session.add(runner)
            await session.commit()
            runner_id = runner.id

        token = create_payload_token(
            {"sub": runner_id, "pool_id": pool_id, "kind": "runner_registration"},
            ttl_seconds=86_400,
        )
        return runner_id, token

    async def _update_runner_instance_id(self, runner_id: str, instance_id: str) -> None:
        async with SessionLocal() as session:
            runner = await session.get(Runner, runner_id)
            if runner is not None:
                caps = dict(runner.capabilities)
                caps["instance_id"] = instance_id
                runner.capabilities = caps
                await session.commit()

    async def idle_terminate_cloud_runners(self) -> None:
        """Terminate idle cloud-provisioned runners past their idle threshold."""

        async with SessionLocal() as session:
            runners = (await session.scalars(
                select(Runner).where(Runner.status == "online", Runner.current_runs == 0)
            )).all()

            pools: dict[str, RunnerPool] = {}
            for runner in runners:
                if runner.pool_id not in pools:
                    pool = await session.get(RunnerPool, runner.pool_id)
                    if pool:
                        pools[runner.pool_id] = pool

            now = datetime.now(UTC)
            to_terminate = []
            for runner in runners:
                pool = pools.get(runner.pool_id)
                if pool is None:
                    continue
                cfg = pool.provider_config
                if cfg.get("cloud_provider") not in ("aws", "gcp", "azure"):
                    continue
                idle_threshold = int(cfg.get("idle_terminate_seconds", 300))
                if runner.last_seen_at is None:
                    continue
                last = runner.last_seen_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if (now - last).total_seconds() >= idle_threshold:
                    instance_id = runner.capabilities.get("instance_id")
                    if instance_id:
                        to_terminate.append((runner, instance_id, cfg))

        for runner, instance_id, cfg in to_terminate:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda iid=instance_id, c=cfg: _terminate_cloud_instance(iid, c),
                )
                async with SessionLocal() as session:
                    r = await session.get(Runner, runner.id)
                    if r is not None:
                        await session.delete(r)
                        await session.commit()
                logger.info("terminated idle cloud instance %s runner %s", instance_id, runner.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to terminate instance %s: %s", instance_id, exc)


def _terminate_cloud_instance(instance_id: str, cfg: dict) -> None:
    provider = cfg.get("cloud_provider")
    if provider == "aws":
        _terminate_ec2(instance_id, cfg)
    elif provider == "gcp":
        _terminate_gce(instance_id, cfg)
    elif provider == "azure":
        _terminate_azure(instance_id, cfg)


def _terminate_ec2(instance_id: str, cfg: dict) -> None:
    import boto3  # noqa: PLC0415
    kw: dict = {"region_name": cfg.get("region", "us-east-1")}
    if cfg.get("aws_access_key_id"):
        kw["aws_access_key_id"] = cfg["aws_access_key_id"]
    if cfg.get("aws_secret_access_key"):
        kw["aws_secret_access_key"] = cfg["aws_secret_access_key"]
    boto3.client("ec2", **kw).terminate_instances(InstanceIds=[instance_id])


def _terminate_gce(instance_name: str, cfg: dict) -> None:
    from google.cloud import compute_v1  # noqa: PLC0415
    from google.oauth2 import service_account  # noqa: PLC0415
    creds = None
    sa_json = cfg.get("service_account_json")
    if sa_json:
        import json as _json  # noqa: PLC0415
        creds = service_account.Credentials.from_service_account_info(
            _json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        )
    client = compute_v1.InstancesClient(credentials=creds)
    client.delete(
        project=cfg["project"], zone=cfg.get("zone", "us-central1-a"),
        instance=instance_name,
    )


def _terminate_azure(vm_name: str, cfg: dict) -> None:
    from azure.identity import (  # noqa: PLC0415
        ClientSecretCredential,
        DefaultAzureCredential,
    )
    from azure.mgmt.compute import ComputeManagementClient  # noqa: PLC0415
    if cfg.get("client_secret"):
        cred = ClientSecretCredential(
            tenant_id=cfg["tenant_id"], client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
        )
    else:
        cred = DefaultAzureCredential()
    client = ComputeManagementClient(cred, cfg["subscription_id"])
    client.virtual_machines.begin_delete(cfg["resource_group"], vm_name).result()


def _uuid_hex() -> str:
    import uuid  # noqa: PLC0415
    return uuid.uuid4().hex


def build_env_payload(
    env_id: str, python_version: str, packages: list[str], noodle_version: str = "0.0.1"
) -> dict:
    packages_hash = hashlib.sha256(
        json.dumps(sorted(packages)).encode()
    ).hexdigest()[:16]
    return {
        "id": env_id,
        "python_version": python_version,
        "packages": packages,
        "packages_hash": packages_hash,
        "noodle_version": noodle_version,
    }


class _QueuedError(Exception):
    """Raised when a run was queued rather than dispatched immediately."""


# ---------------------------------------------------------------------------
# Queue dispatch background loop
# ---------------------------------------------------------------------------

async def queue_dispatch_loop() -> None:
    """Retry queued runs every 30 seconds.

    Also triggers idle-terminate of cloud runners on each tick.
    """
    dispatcher._reset_loop_state()
    while True:
        await asyncio.sleep(30)
        try:
            await _dispatch_queued_runs()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
        try:
            await dispatcher.idle_terminate_cloud_runners()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass


async def _dispatch_queued_runs() -> None:
    """Attempt to dispatch pending queued runs.

    Runs that have sat queued longer than ``_QUEUE_TTL_SECONDS`` are failed
    with a "queue timeout" rather than retried forever.
    """
    from app.services.runner import _execute_queued_run  # noqa: PLC0415

    async with SessionLocal() as session:
        queued = (await session.scalars(
            select(Run)
            .where(Run.status == "queued", Run.runner_pool_id.is_not(None))
            .order_by(Run.started_at.asc())
            .limit(50)
        )).all()

    now = datetime.now(UTC)
    for run in queued:
        started = run.started_at
        if started is not None:
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if (now - started).total_seconds() >= _QUEUE_TTL_SECONDS:
                await _fail_queued_run(run.id, "queue timeout — no runner became available")
                continue
        try:
            await _execute_queued_run(run.id)
        except _QueuedError:
            pass  # Still no capacity — will retry next tick
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("queued run dispatch error run_id=%s: %s", run.id, exc)


async def _fail_queued_run(run_id: str, error: str) -> None:
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None or run.status != "queued":
            return
        run.status = "error"
        run.finished_at = datetime.now(UTC)
        await session.commit()
    logger.warning("failed queued run %s: %s", run_id, error)


# Singleton
dispatcher = RemoteDispatcher()
