import pytest

from app.services.run_lifecycle import (
    InvalidRunTransition,
    can_transition,
    lifecycle_contract,
    require_transition,
)


def test_run_lifecycle_allows_resume_without_rewriting_terminal_history() -> None:
    assert can_transition("pending", "queued")
    assert can_transition("running", "waiting")
    assert can_transition("waiting", "queued")
    assert not can_transition("success", "running")
    assert not can_transition("error", "queued")


def test_run_lifecycle_rejects_invalid_or_unknown_transitions() -> None:
    with pytest.raises(InvalidRunTransition, match="cannot transition"):
        require_transition("success", "running", run_id="run-1")
    with pytest.raises(InvalidRunTransition, match="unknown target state"):
        require_transition("running", "mystery", run_id="run-1")


def test_lifecycle_contract_marks_waiting_as_resumable_not_terminal() -> None:
    contract = lifecycle_contract()
    assert "waiting" not in contract["terminal_states"]
    assert contract["transitions"]["waiting"] == ["cancelled", "error", "queued"]
