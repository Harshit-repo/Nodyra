"""Kubernetes single-run entrypoint.

The API creates a K8s Job whose pod runs this module. It reads its run token,
run id, and API URL from env vars, connects outbound to the runner WS, sends
``runner_hello``, accepts exactly one ``run_assigned`` message (the one the
API holds queued for this run id), executes it, reports ``run_finished``, then
exits so the Job completes and is reaped.

The pod IS a single-run agent — it reuses the full agent WS protocol, but the
``runner_id`` in the URL is the run id (the API's K8s handler keys on that).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import websockets

from noodle_runner_agent import env_manager
from noodle_runner_agent.process_pool import run_workflow_subprocess

logger = logging.getLogger("noodle_runner.k8s")


def _ws_url(api_url: str, run_id: str, token: str) -> str:
    base = api_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/runner-pools/ws/runners/{run_id}?token={token}"


async def _run_once(api_url: str, run_id: str, token: str) -> None:
    url = _ws_url(api_url, run_id, token)
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "runner_hello",
                                  "capabilities": {"max_concurrent": 1}}))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != "run_assigned":
                continue

            env = msg.get("env") or {}
            python = await env_manager.build_env(
                str(env.get("id") or "default"),
                str(env.get("python_version") or "3.12"),
                list(env.get("packages") or []),
                str(env.get("packages_hash") or "nohash"),
            )

            async def on_event(event: dict) -> None:
                await ws.send(json.dumps(
                    {"type": "run_event", "run_id": run_id, "event": event}
                ))

            try:
                status = await run_workflow_subprocess(
                    python=python,
                    run_id=run_id,
                    graph=msg.get("graph") or {},
                    cache=msg.get("cache") or None,
                    targets=msg.get("targets") or None,
                    workflow_modules=msg.get("workflow_modules") or [],
                    on_event=on_event,
                    pause_on_approval=bool(msg.get("pause_on_approval")),
                    agent_action_resume=msg.get("agent_action_resume") or {},
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("k8s run failed run_id=%s", run_id)
                await on_event({"type": "run_error", "error": str(exc)})
                status = "error"

            await ws.send(json.dumps(
                {"type": "run_finished", "run_id": run_id, "status": status}
            ))
            return  # single-run: done after one assignment


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    api_url = os.environ.get("NOODLE_API_URL", "")
    run_id = os.environ.get("NOODLE_RUN_ID", "")
    token = os.environ.get("NOODLE_RUN_TOKEN", "")
    if not (api_url and run_id and token):
        raise SystemExit(
            "NOODLE_API_URL, NOODLE_RUN_ID, NOODLE_RUN_TOKEN must all be set"
        )
    asyncio.run(_run_once(api_url, run_id, token))


if __name__ == "__main__":
    main()
