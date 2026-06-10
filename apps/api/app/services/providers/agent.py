"""Agent runner-pool provider (split from remote_dispatch.py, A2).

Outbound-WS daemons on VMs/EC2 instances: the runner connects to
``/ws/runners/{runner_id}`` and accepts ``run_assigned`` messages. Also owns
the cloud auto-provisioning (AWS / GCP / Azure) and idle-terminate paths,
which create and tear down instances that run these agents.

Stateless function module: connection state (``_agents`` / ``_run_callbacks``
/ locks) stays on the ``RemoteDispatcher`` facade, passed in as ``d``;
``session_factory`` is the dispatcher module's (test-swappable)
``SessionLocal`` passed at call time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.models import Run, Runner, RunnerPool
from app.services.executors.base import EventCallback
from noodle.serialization import serialize_value

logger = logging.getLogger("app.services.remote_dispatch")

# How long to wait for a queued run before failing it.
QUEUE_TTL_SECONDS = 3600


class _QueuedError(Exception):
    """Raised when a run was queued rather than dispatched immediately."""


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


async def assign_agent_run(
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
) -> str:
    conn = await pick_agent(d, session_factory, pool_id)
    if conn is None:
        await d.queue_run(run_id)
        # Provision cloud instances if configured
        await maybe_provision(session_factory, pool_id)
        raise _QueuedError(f"run {run_id} queued — no available runners in pool {pool_id}")

    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    conn.active_runs[run_id] = future
    d._run_callbacks[run_id] = on_event

    # Update runner current_runs and persist the runner id on the run so
    # cancel_run can route a run_cancel to this agent.
    async with session_factory() as session:
        runner = await session.get(Runner, conn.runner_id)
        if runner is not None:
            runner.current_runs = max(0, runner.current_runs) + 1
            runner.status = "busy"
        run = await session.get(Run, run_id)
        if run is not None:
            run.runner_id = conn.runner_id
        await session.commit()

    # Multi-tenancy F/C5: remote runs carry the same org namespace and
    # amplification caps as local subprocess runs — the agent forwards
    # both to noodle_runtime verbatim.
    from app.services.runtime_pool import _org_run_limits_for, _resolve_run_org  # noqa: PLC0415

    run_org = await _resolve_run_org(run_id)
    await conn.send({
        "type": "run_assigned",
        "run_id": run_id,
        "env": env_payload,
        "graph": graph,
        "cache": cache or {},
        "targets": targets or [],
        "workflow_modules": workflow_modules,
        "pause_on_approval": pause_on_approval,
        "agent_action_resume": agent_action_resume or {},
        "artifact_key_prefix": run_org,
        "org_limits": await _org_run_limits_for(run_org),
    })

    try:
        status = await asyncio.wait_for(future, timeout=QUEUE_TTL_SECONDS)
    except TimeoutError:
        conn.active_runs.pop(run_id, None)
        d._run_callbacks.pop(run_id, None)
        status = "error"
    finally:
        async with session_factory() as session:
            runner = await session.get(Runner, conn.runner_id)
            if runner is not None:
                runner.current_runs = max(0, runner.current_runs - 1)
                if runner.current_runs == 0:
                    runner.status = "online"
                await session.commit()
        d.signal_capacity()

    return status


async def pick_agent(d: Any, session_factory, pool_id: str) -> _AgentConnection | None:
    """Return the least-loaded available agent connection in this pool.

    Enforces two ceilings before handing out a runner:

    * **Pool-level**: the sum of ``current_runs`` across the pool's runners
      must stay below ``pool.max_concurrent_runs``. This was previously
      stored but never honoured, so a single pool could be oversubscribed.
    * **Runner-level**: each runner's ``current_runs`` must stay below its
      own ``max_concurrent_runs``.

    Among eligible *connected* agents we pick the one with the most free
    capacity (least-loaded) so traffic spreads evenly instead of always
    landing on the first registered runner.
    """
    async with session_factory() as session:
        pool = await session.get(RunnerPool, pool_id)
        pool_cap = pool.max_concurrent_runs if pool is not None else 0
        runners = (
            await session.scalars(
                select(Runner).where(
                    Runner.pool_id == pool_id,
                    Runner.status.in_(["online", "busy"]),
                )
            )
        ).all()
        pool_active = sum(max(0, r.current_runs) for r in runners)
        runner_max = {r.id: r.max_concurrent_runs for r in runners}
        runner_db_load = {r.id: max(0, r.current_runs) for r in runners}

    # Pool ceiling reached → queue rather than oversubscribe.
    if pool_cap and pool_active >= pool_cap:
        return None

    async with d._lock:
        best: _AgentConnection | None = None
        best_free = 0
        for runner_id, conn in d._agents.items():
            cap = runner_max.get(runner_id)
            if cap is None:
                continue  # connected agent not (yet) a known runner row
            # Use whichever load count is higher so a just-assigned run that
            # hasn't been flushed to the DB row still counts against the cap.
            live_load = max(runner_db_load.get(runner_id, 0), len(conn.active_runs))
            effective_free = cap - live_load
            if effective_free > best_free:
                best_free = effective_free
                best = conn
        return best


async def handle_agent_message(
    d: Any, session_factory, conn: _AgentConnection, msg: dict
) -> None:
    mtype = msg.get("type")
    run_id = msg.get("run_id")

    if mtype == "runner_hello":
        async with session_factory() as session:
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
        # Scope to runs THIS connection owns so one runner can't inject
        # events into another runner's stream (RD-1).
        if run_id and run_id in conn.active_runs:
            cb = d._run_callbacks.get(run_id)
            if cb:
                await cb({"type": "env_building", "run_id": run_id,
                          "env_id": msg.get("env_id")})

    elif mtype == "env_ready":
        env_id = msg.get("env_id")
        packages_hash = msg.get("packages_hash", "")
        if env_id and packages_hash:
            cache_key = f"{env_id}-{packages_hash}"
            async with session_factory() as session:
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
            d._run_callbacks.pop(run_id, None)

    elif mtype == "run_event":
        # Only deliver events for a run THIS connection owns — a runner must
        # not be able to push events into another runner's run stream (RD-1).
        if run_id and run_id in conn.active_runs:
            event = msg.get("event") or {}
            cb = d._run_callbacks.get(run_id)
            if cb:
                await cb(event)

    elif mtype == "run_finished":
        if run_id:
            status = str(msg.get("status") or "error")
            fut = conn.active_runs.pop(run_id, None)
            if fut and not fut.done():
                fut.set_result(status)
            d._run_callbacks.pop(run_id, None)

    elif mtype == "call_workflow":
        # The runner's runtime hit an execute_workflow node; resolve the
        # sub-workflow host-side and send the result back. Run it on a task
        # so the agent receive loop keeps draining.
        asyncio.create_task(resolve_remote_subworkflow(conn, msg))

    elif mtype == "pong":
        async with session_factory() as session:
            runner = await session.get(Runner, conn.runner_id)
            if runner is not None:
                runner.last_seen_at = datetime.now(UTC)
                await session.commit()


async def resolve_remote_subworkflow(conn: _AgentConnection, msg: dict) -> None:
    """Run a sub-workflow host-side for a remote runner and reply.

    Reuses the host's ``_call_sub_workflow`` (deferred import to avoid the
    runner.py ↔ remote_dispatch.py cycle). With no ``parent_env_id`` it
    always returns a concrete leaf result — never an inline sentinel — so
    the value serializes cleanly back over the WS.
    """
    from app.services.runner import _call_sub_workflow  # noqa: PLC0415

    callback_id = msg.get("callback_id", "")
    try:
        _timeout = settings.subworkflow_spawn_timeout_seconds or None
        coro = _call_sub_workflow(
            str(msg.get("workflow_id") or ""), msg.get("input")
        )
        result = await (
            asyncio.wait_for(coro, timeout=_timeout) if _timeout else coro
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


# ---------------------------------------------------------------------------
# Cloud provisioning (AWS / GCP / Azure)
# ---------------------------------------------------------------------------

async def maybe_provision(session_factory, pool_id: str) -> None:
    """Provision a new cloud instance if the pool config supports it."""
    async with session_factory() as session:
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
        await provision_aws_instance(session_factory, pool_id, cfg)
    elif provider == "gcp":
        await provision_gcp_instance(session_factory, pool_id, cfg)
    elif provider == "azure":
        await provision_azure_instance(session_factory, pool_id, cfg)


def bootstrap_user_data(api_url: str, token: str, runner_id: str) -> str:
    """Cloud-init / startup script that installs and starts the agent.

    Security note: the runner token is embedded in the instance user-data
    script. User-data is accessible from within the instance via the
    metadata service (169.254.169.254). For higher-security deployments,
    rotate runner tokens regularly or use AWS Systems Manager Parameter
    Store / IAM instance roles instead of passing the token inline.
    """
    logger.warning(
        "provisioning cloud runner %s: token written to instance user-data; "
        "rotate this runner's token after use for production deployments",
        runner_id,
    )
    return (
        "#!/bin/bash\n"
        "set -e\n"
        "pip install noodle-runner --quiet\n"
        f"noodle-runner register --api-url {api_url} --token {token} "
        f"--name cloud-{runner_id[:8]}\n"
        "noodle-runner start &\n"
    )


async def provision_aws_instance(session_factory, pool_id: str, cfg: dict) -> None:
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

    runner_id, token = await create_runner_and_token(session_factory, pool_id, "cloud-auto")

    user_data = bootstrap_user_data(api_url, token, runner_id)

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

    loop = asyncio.get_running_loop()
    try:
        resp = await loop.run_in_executor(
            None,
            lambda: boto3.client("ec2", **ec2_kwargs).run_instances(**run_kwargs),
        )
        instance_id = resp["Instances"][0]["InstanceId"]
        logger.info("provisioned EC2 instance %s for pool %s", instance_id, pool_id)
        await update_runner_instance_id(session_factory, runner_id, instance_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("EC2 provisioning failed pool_id=%s: %s", pool_id, exc)


async def provision_gcp_instance(session_factory, pool_id: str, cfg: dict) -> None:
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

    runner_id, token = await create_runner_and_token(session_factory, pool_id, "gcp-auto")
    startup = bootstrap_user_data(api_url, token, runner_id)
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

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _create)
        logger.info("provisioned GCE instance %s for pool %s", instance_name, pool_id)
        await update_runner_instance_id(session_factory, runner_id, instance_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GCE provisioning failed pool_id=%s: %s", pool_id, exc)


async def provision_azure_instance(session_factory, pool_id: str, cfg: dict) -> None:
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

    runner_id, token = await create_runner_and_token(session_factory, pool_id, "azure-auto")
    import base64  # noqa: PLC0415
    custom_data = base64.b64encode(
        bootstrap_user_data(api_url, token, runner_id).encode()
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

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _create)
        logger.info("provisioned Azure VM %s for pool %s", vm_name, pool_id)
        await update_runner_instance_id(session_factory, runner_id, vm_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Azure provisioning failed pool_id=%s: %s", pool_id, exc)


async def create_runner_and_token(
    session_factory, pool_id: str, name_prefix: str
) -> tuple[str, str]:
    from app.services.crypto import create_payload_token  # noqa: PLC0415
    runner = Runner(
        pool_id=pool_id,
        name=f"{name_prefix}-{_uuid_hex()[:8]}",
        status="offline",
    )
    async with session_factory() as session:
        session.add(runner)
        await session.commit()
        runner_id = runner.id

    token = create_payload_token(
        {"sub": runner_id, "pool_id": pool_id, "kind": "runner_registration"},
        ttl_seconds=86_400,
    )
    return runner_id, token


async def update_runner_instance_id(
    session_factory, runner_id: str, instance_id: str
) -> None:
    async with session_factory() as session:
        runner = await session.get(Runner, runner_id)
        if runner is not None:
            caps = dict(runner.capabilities)
            caps["instance_id"] = instance_id
            runner.capabilities = caps
            await session.commit()


async def idle_terminate_cloud_runners(session_factory) -> None:
    """Terminate idle cloud-provisioned runners past their idle threshold."""

    async with session_factory() as session:
        runners = (await session.scalars(
            select(Runner).where(Runner.status == "online", Runner.current_runs == 0)
        )).all()

        pool_ids = {r.pool_id for r in runners if r.pool_id}
        pools: dict[str, RunnerPool] = {}
        if pool_ids:
            pools = {
                p.id: p
                for p in (
                    await session.scalars(
                        select(RunnerPool).where(RunnerPool.id.in_(pool_ids))
                    )
                ).all()
            }

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
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda iid=instance_id, c=cfg: _terminate_cloud_instance(iid, c),
            )
            async with session_factory() as session:
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
