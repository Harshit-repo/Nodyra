"""Kubernetes runner-pool provider (split from remote_dispatch.py, A2).

The host creates a K8s Job whose pod connects back over the WS agent
protocol as a single-run agent. The pod's WebSocket terminates in the API
process that created the Job (futures/payloads live on the dispatcher
facade), so this provider — like ``agent`` — cannot be served by a
standalone dispatch worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.models import RunnerPool
from app.services.executors.base import EventCallback
from app.services.providers.agent import QUEUE_TTL_SECONDS, _AgentConnection

logger = logging.getLogger("app.services.remote_dispatch")


async def assign_k8s_run(
    d: Any,
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

    async with session_factory() as session:
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
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    # Use run_id as runner_id for K8s single-run agents.
    d._run_callbacks[run_id] = on_event
    d._k8s_futures[run_id] = future
    # The pod fetches its run payload over the WS once it connects.
    d._k8s_payloads[run_id] = {
        "type": "run_assigned",
        "run_id": run_id,
        "env": env_payload,
        "graph": graph,
        "cache": cache or {},
        "targets": targets or [],
        "workflow_modules": workflow_modules,
        "pause_on_approval": pause_on_approval,
        "agent_action_resume": agent_action_resume or {},
        "subworkflow_meta": subworkflow_meta or {},
    }

    try:
        await batch_v1.create_namespaced_job(namespace=namespace, body=job_body)
        logger.info("created k8s job %s for run %s", job_name, run_id)
        status = await asyncio.wait_for(future, timeout=QUEUE_TTL_SECONDS)
    except TimeoutError:
        status = "error"
    except Exception as exc:  # noqa: BLE001
        logger.exception("k8s job creation failed run_id=%s: %s", run_id, exc)
        status = "error"
    finally:
        d._k8s_futures.pop(run_id, None)
        d._k8s_payloads.pop(run_id, None)
        d._run_callbacks.pop(run_id, None)
        # Clean up job
        try:
            await batch_v1.delete_namespaced_job(
                name=job_name, namespace=namespace,
                body=k8s_client.V1DeleteOptions(propagation_policy="Background"),
            )
        except Exception:  # noqa: BLE001
            pass

    return status


async def handle_k8s_runner_connect(d: Any, run_id: str, ws: Any) -> None:
    """Called when a K8s pod connects back as a single-run agent."""
    conn = _AgentConnection(runner_id=run_id, ws=ws)
    async with d._lock:
        d._agents[run_id] = conn

    try:
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            if mtype == "runner_hello":
                # Deliver the pre-queued run assignment to the pod.
                payload = d._k8s_payloads.get(run_id)
                if payload is not None:
                    await conn.send(payload)
            elif mtype == "run_event":
                # This WS is authenticated for exactly one run (run_id ==
                # conn.runner_id). Ignore any other run_id the pod claims so
                # a compromised pod can't touch another run's stream (RD-1).
                rid = msg.get("run_id")
                if rid and rid == run_id:
                    cb = d._run_callbacks.get(rid)
                    if cb:
                        await cb(msg.get("event") or {})
            elif mtype == "run_finished":
                rid = msg.get("run_id")
                if rid and rid == run_id:
                    fut = getattr(d, "_k8s_futures", {}).pop(rid, None)
                    if fut and not fut.done():
                        fut.set_result(str(msg.get("status") or "error"))
                    d._run_callbacks.pop(rid, None)
    except Exception:  # noqa: BLE001
        pass
    finally:
        async with d._lock:
            d._agents.pop(run_id, None)
