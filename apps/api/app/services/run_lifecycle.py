"""Explicit run lifecycle contract shared by orchestration services."""

from __future__ import annotations

from typing import Final

RUN_STATES: Final = frozenset(
    {"pending", "queued", "running", "waiting", "success", "error", "timed_out", "cancelled"}
)
TERMINAL_RUN_STATES: Final = frozenset({"success", "error", "timed_out", "cancelled"})

_ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "pending": frozenset({"queued", "running", "cancelled", "error"}),
    "queued": frozenset({"running", "cancelled", "error"}),
    "running": frozenset({"success", "error", "timed_out", "cancelled", "waiting"}),
    "waiting": frozenset({"queued", "cancelled", "error"}),
    "success": frozenset(),
    "error": frozenset(),
    "timed_out": frozenset(),
    "cancelled": frozenset(),
}


class InvalidRunTransition(RuntimeError):
    """Raised when orchestration attempts to rewrite run history."""


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def require_transition(current: str, target: str, *, run_id: str) -> None:
    if current not in RUN_STATES:
        raise InvalidRunTransition(f"run {run_id} has unknown current state {current!r}")
    if target not in RUN_STATES:
        raise InvalidRunTransition(f"run {run_id} has unknown target state {target!r}")
    if not can_transition(current, target):
        raise InvalidRunTransition(
            f"run {run_id} cannot transition from {current!r} to {target!r}"
        )


def lifecycle_contract() -> dict[str, object]:
    return {
        "states": sorted(RUN_STATES),
        "terminal_states": sorted(TERMINAL_RUN_STATES),
        "transitions": {
            state: sorted(targets) for state, targets in _ALLOWED_TRANSITIONS.items()
        },
    }
