"""Checkpoint commits are debounced to one per interval, with a final flush."""
from __future__ import annotations

from app.services.runner import _CheckpointDebouncer


def test_debouncer_allows_first_then_suppresses_within_interval() -> None:
    clock = [100.0]
    debouncer = _CheckpointDebouncer(interval=0.25, clock=lambda: clock[0])

    assert debouncer.should_persist() is True
    debouncer.mark_persisted()
    clock[0] += 0.1
    assert debouncer.should_persist() is False
    assert debouncer.has_deferred is True
    clock[0] += 0.2
    assert debouncer.should_persist() is True
    debouncer.mark_persisted()
    assert debouncer.has_deferred is False


def test_debouncer_deferred_flag_drives_final_flush() -> None:
    clock = [0.0]
    debouncer = _CheckpointDebouncer(interval=0.25, clock=lambda: clock[0])

    assert debouncer.should_persist() is True
    debouncer.mark_persisted()
    clock[0] += 0.01
    assert debouncer.should_persist() is False
    assert debouncer.has_deferred is True
