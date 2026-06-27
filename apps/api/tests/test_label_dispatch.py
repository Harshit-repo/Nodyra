"""Tests for label-aware agent dispatch."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.providers.agent import pick_agent


def _make_dispatcher(agents: dict):
    d = MagicMock()
    d._agents = agents
    d._lock = __import__("asyncio").Lock()
    return d


def _make_session_factory(pool_cap: int, runners: list[dict]):
    """Build a mock session_factory that returns pool + runner rows."""
    from app.models import Runner, RunnerPool

    pool = MagicMock(spec=RunnerPool)
    pool.max_concurrent_runs = pool_cap

    runner_objs = []
    for r in runners:
        obj = MagicMock(spec=Runner)
        obj.id = r["id"]
        obj.status = r.get("status", "online")
        obj.max_concurrent_runs = r.get("max_concurrent_runs", 2)
        obj.current_runs = r.get("current_runs", 0)
        obj.capabilities = r.get("capabilities", {})
        runner_objs.append(obj)

    async def _scalars_result(query):
        result = MagicMock()
        result.all.return_value = runner_objs
        return result

    async def _get(model, pk):
        if model.__name__ == "RunnerPool":
            return pool
        return None

    session = MagicMock()
    session.get = AsyncMock(side_effect=_get)
    session.scalars = AsyncMock(side_effect=_scalars_result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    def factory():
        return session

    return factory


@pytest.mark.asyncio
async def test_pick_agent_no_labels_picks_least_loaded() -> None:
    conn_a = MagicMock()
    conn_a.runner_id = "r1"
    conn_a.active_runs = {}
    conn_b = MagicMock()
    conn_b.runner_id = "r2"
    conn_b.active_runs = {"run-x": None}  # 1 active

    d = _make_dispatcher({"r1": conn_a, "r2": conn_b})
    sf = _make_session_factory(
        pool_cap=10,
        runners=[
            {"id": "r1", "capabilities": {}, "current_runs": 0},
            {"id": "r2", "capabilities": {}, "current_runs": 1},
        ],
    )

    result = await pick_agent(d, sf, "pool-1")
    assert result is conn_a  # r1 has more free capacity


@pytest.mark.asyncio
async def test_pick_agent_label_filter_excludes_non_matching() -> None:
    conn_a = MagicMock()
    conn_a.runner_id = "r1"
    conn_a.active_runs = {}
    conn_b = MagicMock()
    conn_b.runner_id = "r2"
    conn_b.active_runs = {}

    d = _make_dispatcher({"r1": conn_a, "r2": conn_b})
    sf = _make_session_factory(
        pool_cap=10,
        runners=[
            {"id": "r1", "capabilities": {"region": "us"}},
            {"id": "r2", "capabilities": {"region": "eu", "gpu": "a100"}},
        ],
    )

    result = await pick_agent(d, sf, "pool-1", required_labels={"region": "eu", "gpu": "a100"})
    assert result is conn_b


@pytest.mark.asyncio
async def test_pick_agent_no_matching_labels_returns_none() -> None:
    conn_a = MagicMock()
    conn_a.runner_id = "r1"
    conn_a.active_runs = {}

    d = _make_dispatcher({"r1": conn_a})
    sf = _make_session_factory(
        pool_cap=10,
        runners=[{"id": "r1", "capabilities": {"region": "us"}}],
    )

    result = await pick_agent(d, sf, "pool-1", required_labels={"gpu": "a100"})
    assert result is None


@pytest.mark.asyncio
async def test_pick_agent_draining_runner_not_in_online_query() -> None:
    """Draining runners have status='draining', not in (online, busy) query.
    The DB query already excludes them; this test verifies the filter."""
    conn_a = MagicMock()
    conn_a.runner_id = "r1"
    conn_a.active_runs = {}

    d = _make_dispatcher({"r1": conn_a})
    # Simulate no runners returned from DB (draining runner not in query result)
    sf = _make_session_factory(pool_cap=10, runners=[])

    result = await pick_agent(d, sf, "pool-1")
    assert result is None
