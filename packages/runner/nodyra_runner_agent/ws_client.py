"""Outbound WebSocket client with auto-reconnect.

The agent always connects *out* to the API — the API never opens an inbound
connection to the runner. On any drop the client reconnects with a fixed 5s
backoff and re-announces itself with ``runner_hello``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

logger = logging.getLogger("nodyra_runner")

MessageHandler = Callable[[dict, Any], Awaitable[None]]


class AgentWSClient:
    def __init__(
        self,
        ws_url: str,
        hello_payload: Callable[[], dict],
        on_message: MessageHandler,
        reconnect_seconds: float = 5.0,
    ) -> None:
        self._ws_url = ws_url
        self._hello_payload = hello_payload
        self._on_message = on_message
        self._reconnect_seconds = reconnect_seconds
        self._ws: Any = None

    async def send(self, msg: dict) -> None:
        ws = self._ws
        if ws is None:
            logger.warning("send dropped — not connected: %s", msg.get("type"))
            return
        try:
            await ws.send(json.dumps(msg))
        except Exception:  # noqa: BLE001 - connection died mid-send
            logger.warning("send failed — connection lost")

    async def run_forever(self) -> None:
        while True:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    self._ws = ws
                    logger.info("connected to %s", _redact(self._ws_url))
                    await ws.send(json.dumps(self._hello_payload()))
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await self._on_message(msg, ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any connect/drop error
                logger.warning("connection error: %s — retrying in %ss",
                               exc, self._reconnect_seconds)
            finally:
                self._ws = None
            await asyncio.sleep(self._reconnect_seconds)


def _redact(url: str) -> str:
    """Strip the token query param from a URL for logging."""
    if "?token=" in url:
        return url.split("?token=")[0] + "?token=***"
    return url
