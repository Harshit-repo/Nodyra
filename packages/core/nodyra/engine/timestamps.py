"""Monotonic finish-time stamps for node events.

A fast run can complete several nodes within a single clock tick. Downstream
readers (webhook "Last Node" responses, the MCP run_workflow output) order
NodeRun rows by ``finished_at``; tied stamps make the FIRST-finished node
(the trigger) win over the actual leaf. Strictly increasing stamps preserve
the true finish order across a run.
"""

from __future__ import annotations

import time

_finish_guard: float = 0.0


def stamp_finish() -> float:
    """Return a finish time that is strictly greater than every earlier stamp
    in this process."""
    global _finish_guard
    now = time.time()
    if now <= _finish_guard:
        now = _finish_guard + 1e-6
    _finish_guard = now
    return now
