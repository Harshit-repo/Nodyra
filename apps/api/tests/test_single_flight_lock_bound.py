import asyncio

from app.services import runner as runner_mod


def test_lock_map_evicts_unlocked_entries():
    runner_mod._workflow_single_flight_locks.clear()
    for i in range(runner_mod._SINGLE_FLIGHT_LOCKS_MAX + 10):
        runner_mod._workflow_single_flight_locks[f"wf-{i}"] = asyncio.Lock()
    runner_mod._prune_single_flight_locks()
    assert (
        len(runner_mod._workflow_single_flight_locks)
        <= runner_mod._SINGLE_FLIGHT_LOCKS_MAX
    )
