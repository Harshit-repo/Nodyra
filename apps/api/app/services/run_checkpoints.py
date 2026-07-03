"""Durable-execution checkpointing (extracted from runner.py - A2 follow-up)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from sqlalchemy import update

from app.db import SessionLocal
from app.models import Run
from noodle.serialization import (
    _approx_json_length,
    serialize_value,
    truncate_serialized_value,
)

logger = logging.getLogger(__name__)

_MAX_CHECKPOINT_BYTES: int = 1_048_576  # 1 MiB per-run cap


class _CheckpointDebouncer:
    """Coalesce per-node checkpoint commits while preserving a final flush."""

    def __init__(self, interval: float = 0.25, *, clock=None) -> None:
        self._interval = interval
        self._clock = clock or time.monotonic
        self._last_persist: float | None = None
        self.has_deferred = False

    def should_persist(self) -> bool:
        now = self._clock()
        if self._last_persist is None or now - self._last_persist >= self._interval:
            return True
        self.has_deferred = True
        return False

    def mark_persisted(self) -> None:
        self._last_persist = self._clock()
        self.has_deferred = False


def _serialize_checkpoint_outputs(
    node_outputs: dict[str, dict],
) -> dict[str, dict]:
    """Serialize node outputs for checkpoint storage.

    Uses ``serialize_value`` with ``dataframe_max_rows=100``. Values that
    cannot be serialised are skipped with a warning so a single bad output
    never blocks the checkpoint.
    """
    result: dict[str, dict] = {}
    for node_id, outputs in node_outputs.items():
        if not isinstance(outputs, dict):
            continue
        try:
            serialized = serialize_value(outputs, dataframe_max_rows=100)
            if isinstance(serialized, dict):
                result[node_id] = serialized
        except Exception:  # noqa: BLE001
            logger.warning(
                "checkpoint skip node_id=%s: serialization failed", node_id
            )
    return result


async def _save_checkpoint(
    run_id: str,
    node_outputs: dict[str, dict],
    completed: set[str],
    last_node_id: str,
    *,
    _accumulated: dict[str, dict] | None = None,
) -> bool:
    """Persist execution state to ``Run.checkpoint`` after a node completes.

    When *_accumulated* is provided, only newly-seen nodes are serialised
    and added to it; the FULL dict is then persisted. This avoids O(n^2)
    re-serialisation of already-checkpointed outputs.

    Bounded to ``_MAX_CHECKPOINT_BYTES``. Returns True when the payload had
    to be truncated to fit. Failures are logged but never propagated - a
    checkpoint save must not interrupt the run.
    """
    if _accumulated is not None:
        for nid, outputs in node_outputs.items():
            if nid in _accumulated or not isinstance(outputs, dict):
                continue
            try:
                s = serialize_value(outputs, dataframe_max_rows=100)
                if isinstance(s, dict):
                    _accumulated[nid] = s
            except Exception:  # noqa: BLE001
                logger.warning("checkpoint skip node_id=%s: serialization failed", nid)
        checkpoint_data = dict(_accumulated)
    else:
        checkpoint_data = _serialize_checkpoint_outputs(node_outputs)

    payload: dict = {
        "node_outputs": checkpoint_data,
        "completed_nodes": sorted(completed),
        "last_node_id": last_node_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    try:
        approx = _approx_json_length(payload)
    except (TypeError, ValueError):
        logger.warning("run_id=%s checkpoint size estimate failed, skipping", run_id)
        return False
    was_truncated = False
    if approx > _MAX_CHECKPOINT_BYTES:
        truncated = truncate_serialized_value(payload, _MAX_CHECKPOINT_BYTES)
        was_truncated = True
        logger.warning(
            "run_id=%s checkpoint %d bytes exceeds %d byte limit, truncating",
            run_id,
            approx,
            _MAX_CHECKPOINT_BYTES,
        )
        if not isinstance(truncated, dict):
            logger.warning("run_id=%s checkpoint truncated to non-dict, skipping", run_id)
            return False
        payload = truncated

    try:
        async with SessionLocal() as session:
            await session.execute(
                update(Run).where(Run.id == run_id).values(checkpoint=payload)
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("run_id=%s failed to save checkpoint", run_id)
    return was_truncated
