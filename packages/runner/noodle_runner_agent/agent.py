"""Noodle runner agent — CLI + run loop.

    noodle-runner register --api-url URL --token TOKEN [--name NAME]
        Saves runner_id (decoded from the token) + token to config.json.

    noodle-runner start
        Connects the WS, accepts run_assigned messages, builds the env,
        runs the workflow, and streams events back. Reconnects on drop.

The registration token is a signed JWT-like payload whose ``sub`` is the
runner id the API pre-created. We decode it (without verifying — the API
verifies on connect) to recover that id.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import uuid
from typing import Any

from noodle_runner_agent import env_manager
from noodle_runner_agent.config import AgentConfig, load_config, save_config
from noodle_runner_agent.process_pool import run_workflow_subprocess
from noodle_runner_agent.ws_client import AgentWSClient

logger = logging.getLogger("noodle_runner")


def _decode_runner_id(token: str) -> str:
    """Recover the runner id (``sub``) from the unverified token body."""
    try:
        body = token.split(".")[0]
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return str(payload.get("sub") or "")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"could not decode runner id from token: {exc}") from exc


# ---------------------------------------------------------------------------
# Agent run loop
# ---------------------------------------------------------------------------


class RunnerAgent:
    def __init__(self, cfg: AgentConfig, max_concurrent: int = 4) -> None:
        self._cfg = cfg
        self._max_concurrent = max_concurrent
        self._active = 0
        self._run_tasks: dict[str, asyncio.Task] = {}
        # callback_id → future, for sub-workflow calls brokered through the API
        self._pending_calls: dict[str, asyncio.Future] = {}
        self._client = AgentWSClient(
            ws_url=cfg.ws_url,
            hello_payload=self._hello_payload,
            on_message=self._on_message,
        )

    def _hello_payload(self) -> dict:
        return {
            "type": "runner_hello",
            "capabilities": {"max_concurrent": self._max_concurrent},
            "cached_env_ids": env_manager.list_cached_env_ids(),
        }

    async def run_forever(self) -> None:
        await self._client.run_forever()

    async def _on_message(self, msg: dict, ws: Any) -> None:
        mtype = msg.get("type")
        if mtype == "run_assigned":
            run_id = str(msg.get("run_id") or "")
            task = asyncio.create_task(self._handle_run(msg))
            self._run_tasks[run_id] = task
            task.add_done_callback(lambda _t: self._run_tasks.pop(run_id, None))
        elif mtype == "ping":
            await self._client.send({"type": "pong"})
        elif mtype == "run_cancel":
            run_id = str(msg.get("run_id") or "")
            task = self._run_tasks.get(run_id)
            if task and not task.done():
                logger.info("cancelling run %s", run_id)
                task.cancel()
        elif mtype in ("call_workflow_response", "call_workflow_error"):
            fut = self._pending_calls.get(msg.get("callback_id", ""))
            if fut and not fut.done():
                if mtype == "call_workflow_error":
                    fut.set_exception(
                        RuntimeError(msg.get("error", "sub-workflow error"))
                    )
                else:
                    fut.set_result(msg.get("result"))

    async def _broker_subworkflow(self, run_id: str, workflow_id: str, input_value: Any) -> Any:
        """Ask the API to run a sub-workflow and return its result."""
        callback_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_calls[callback_id] = fut
        await self._client.send({
            "type": "call_workflow",
            "run_id": run_id,
            "callback_id": callback_id,
            "workflow_id": workflow_id,
            "input": input_value,
        })
        try:
            return await fut
        finally:
            self._pending_calls.pop(callback_id, None)

    async def _handle_run(self, msg: dict) -> None:
        run_id = str(msg.get("run_id") or "")
        env = msg.get("env") or {}
        env_id = str(env.get("id") or "default")
        packages_hash = str(env.get("packages_hash") or "nohash")
        self._active += 1
        try:
            # Build / reuse the env.
            await self._client.send({
                "type": "env_building", "run_id": run_id, "env_id": env_id,
            })
            try:
                python = await env_manager.build_env(
                    env_id,
                    str(env.get("python_version") or "3.12"),
                    list(env.get("packages") or []),
                    packages_hash,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("env build failed run_id=%s", run_id)
                await self._client.send({
                    "type": "env_error", "run_id": run_id,
                    "env_id": env_id, "error": str(exc),
                })
                return
            await self._client.send({
                "type": "env_ready", "env_id": env_id,
                "packages_hash": packages_hash,
            })

            async def on_event(event: dict) -> None:
                await self._client.send({
                    "type": "run_event", "run_id": run_id, "event": event,
                })

            async def broker(workflow_id: str, input_value: Any) -> Any:
                return await self._broker_subworkflow(run_id, workflow_id, input_value)

            status = "error"
            try:
                status = await run_workflow_subprocess(
                    python=python,
                    run_id=run_id,
                    graph=msg.get("graph") or {},
                    cache=msg.get("cache") or None,
                    targets=msg.get("targets") or None,
                    workflow_modules=msg.get("workflow_modules") or [],
                    on_event=on_event,
                    artifacts_upload_url=self._cfg.artifact_upload_url,
                    artifacts_runner_token=self._cfg.token,
                    call_workflow=broker,
                    pause_on_approval=bool(msg.get("pause_on_approval")),
                    agent_action_resume=msg.get("agent_action_resume") or {},
                    artifact_key_prefix=str(msg.get("artifact_key_prefix") or ""),
                    org_limits=msg.get("org_limits") or {},
                )
            except asyncio.CancelledError:
                logger.info("run %s cancelled", run_id)
                await self._client.send({
                    "type": "run_finished", "run_id": run_id, "status": "cancelled",
                })
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("run failed run_id=%s", run_id)
                await on_event({"type": "run_error", "error": str(exc)})
                status = "error"

            await self._client.send({
                "type": "run_finished", "run_id": run_id, "status": status,
            })
        finally:
            self._active -= 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_register(args: argparse.Namespace) -> None:
    runner_id = _decode_runner_id(args.token)
    if not runner_id:
        raise SystemExit("token did not contain a runner id (sub)")
    cfg = AgentConfig(
        api_url=args.api_url,
        runner_id=runner_id,
        token=args.token,
        name=args.name or "",
    )
    save_config(cfg)
    print(f"Registered runner {runner_id} for {args.api_url}")
    print("Run 'noodle-runner start' to begin accepting workflow runs.")


def _cmd_start(args: argparse.Namespace) -> None:
    cfg = load_config()
    if cfg is None:
        raise SystemExit(
            "No config found. Run 'noodle-runner register ...' first."
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    agent = RunnerAgent(cfg, max_concurrent=args.max_concurrent)
    logger.info("starting runner %s (max_concurrent=%s)",
                cfg.runner_id, args.max_concurrent)
    try:
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        logger.info("shutting down")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="noodle-runner")
    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="register this machine as a runner")
    reg.add_argument("--api-url", required=True, help="Noodle API base URL")
    reg.add_argument("--token", required=True, help="registration token")
    reg.add_argument("--name", default="", help="display name for this runner")
    reg.set_defaults(func=_cmd_register)

    start = sub.add_parser("start", help="connect and accept workflow runs")
    start.add_argument(
        "--max-concurrent", type=int, default=4,
        help="max concurrent runs on this machine",
    )
    start.set_defaults(func=_cmd_start)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
