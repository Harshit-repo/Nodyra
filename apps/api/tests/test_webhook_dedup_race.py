"""Concurrent deliveries of the same webhook event.

Two deliveries can both pass the "have I seen this key?" read before either
commits, and then both insert. The unique index on ``runs.deduplication_key``
arbitrates — that is what it is for — but the loser used to surface as a 500,
and a 500 tells a webhook provider to retry, during the very retry storm that
produced the collision.

Found by firing eight concurrent copies of one payment event at a live server:
one processed, six acknowledged as duplicates, one 500.
"""

import pytest
from httpx import AsyncClient

from app.exceptions import DuplicateRun

DEDUP_GRAPH = {
    "nodes": [
        {
            "id": "hook",
            "type": "webhook_trigger",
            "params": {
                "http_method": "POST",
                "path": "race-hook",
                "dedup": "on",
                "dedup_key": "{{ $json.body['id'] }}",
            },
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "handle",
            "type": "code",
            "params": {"code": "output = {'ok': True}"},
            "position": {"x": 240, "y": 0},
        },
    ],
    "edges": [{"source": "hook", "target": "handle"}],
}


async def _publish_dedup_workflow(client: AsyncClient) -> str:
    workflow_id = (await client.post("/workflows", json={"name": "Race"})).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": DEDUP_GRAPH, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    return workflow_id


async def test_a_delivery_that_loses_the_race_is_acknowledged(
    client: AsyncClient, monkeypatch
) -> None:
    """The loser gets 200 with no run, exactly like a duplicate caught earlier."""
    await _publish_dedup_workflow(client)

    from app.services import triggers

    real_start_run = triggers.start_run
    calls = {"n": 0}

    async def racing_start_run(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return await real_start_run(*args, **kwargs)
        raise DuplicateRun("A run for deduplication key 'evt-race' already exists.")

    monkeypatch.setattr(triggers, "start_run", racing_start_run)

    first = await client.post("/webhook/race-hook", json={"id": "evt-race"})
    assert first.status_code == 200
    assert len(first.json()["runs"]) == 1

    loser = await client.post("/webhook/race-hook", json={"id": "evt-race"})
    assert loser.status_code == 200, loser.text
    assert loser.json()["runs"] == []
    assert "Duplicate" in loser.text


async def test_start_run_reports_a_duplicate_rather_than_an_integrity_error(
    client: AsyncClient,
) -> None:
    """The unique index is the arbiter; the caller needs a usable answer.

    Letting sqlalchemy's IntegrityError escape turned a correct de-duplication
    into "Internal server error".
    """
    workflow_id = await _publish_dedup_workflow(client)

    from app.services.runner import start_run

    common = {
        "mode": "production",
        "trigger_type": "webhook",
        "deduplication_key": "evt-same-key",
    }
    await start_run(workflow_id, DEDUP_GRAPH, 1, **common)

    with pytest.raises(DuplicateRun) as excinfo:
        await start_run(workflow_id, DEDUP_GRAPH, 1, **common)

    assert "evt-same-key" in str(excinfo.value)


async def test_an_integrity_error_without_a_dedup_key_still_raises(
    client: AsyncClient, monkeypatch
) -> None:
    """Only a dedup-key collision is a duplicate. Other constraint failures
    are real bugs and must not be silently reported as de-duplication."""
    from sqlalchemy.exc import IntegrityError

    workflow_id = await _publish_dedup_workflow(client)

    from app.services import runner

    async def exploding_flush(self):
        raise IntegrityError("INSERT ...", {}, Exception("some other constraint"))

    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.AsyncSession.flush", exploding_flush, raising=True
    )

    with pytest.raises(IntegrityError):
        await runner.start_run(
            workflow_id, DEDUP_GRAPH, 1, mode="production", trigger_type="webhook"
        )
