"""A3: host-side sub-workflow resolver — child Run rows, draft selection."""

from httpx import AsyncClient
from sqlalchemy import select

from app.models import Run
from app.services import subworkflows as subworkflows_module
from app.services.subworkflows import resolve_subworkflow
from nodyra.engine.subworkflows import InlineSubworkflow, SubworkflowCall


def _doubler_graph(code: str = "output = input * 2") -> dict:
    return {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "c", "type": "code", "params": {"code": code},
             "position": {"x": 200, "y": 0}},
        ],
        "edges": [
            {"id": "e", "source": "t", "source_output": "main",
             "target": "c", "target_input": "input"},
        ],
    }


async def _make_sub(
    client: AsyncClient,
    code: str = "output = input * 2",
    env_id: str | None = None,
) -> str:
    sub = (await client.post("/workflows", json={"name": "Sub"})).json()
    body: dict = {"graph": _doubler_graph(code)}
    if env_id is not None:
        body["environment_id"] = env_id
    await client.put(f"/workflows/{sub['id']}", json=body)
    await client.post(f"/workflows/{sub['id']}/publish", json={})
    return sub["id"]


def _call(workflow_id: str, value, **kw) -> SubworkflowCall:
    defaults = dict(
        parameters=value, use_published=True, parent_run_id=None,
        depth=1, call_chain=frozenset({workflow_id}),
    )
    defaults.update(kw)
    return SubworkflowCall(workflow_id=workflow_id, **defaults)


async def test_resolver_runs_child_in_process(client: AsyncClient) -> None:
    sub_id = await _make_sub(client)
    result = await resolve_subworkflow(_call(sub_id, 21))
    assert result == 42


async def test_resolver_creates_child_run_row(client: AsyncClient) -> None:
    sub_id = await _make_sub(client)

    # Parent run row to inherit org from / link to.
    parent = (await client.post("/workflows", json={"name": "Parent"})).json()
    await client.put(f"/workflows/{parent['id']}", json={"graph": _doubler_graph()})
    parent_run_id = (
        await client.post(f"/workflows/{parent['id']}/run", json={})
    ).json()["run_id"]

    await resolve_subworkflow(_call(sub_id, 1, parent_run_id=parent_run_id))

    # conftest patches the module's SessionLocal to the per-test DB.
    async with subworkflows_module.SessionLocal() as session:
        child = await session.scalar(
            select(Run).where(Run.parent_run_id == parent_run_id)
        )
    assert child is not None
    assert child.workflow_id == sub_id
    assert child.mode == "subworkflow"
    assert child.status == "success"
    assert child.finished_at is not None


async def test_resolver_prefers_draft_when_not_published_mode(
    client: AsyncClient,
) -> None:
    # Published version doubles; a NEWER unpublished draft multiplies by 10.
    sub_id = await _make_sub(client, code="output = input * 2")
    draft = _doubler_graph(code="output = input * 10")
    await client.put(f"/workflows/{sub_id}", json={"graph": draft})

    published = await resolve_subworkflow(_call(sub_id, 3, use_published=True))
    draft_result = await resolve_subworkflow(_call(sub_id, 3, use_published=False))
    assert published == 6
    assert draft_result == 30


async def test_resolver_returns_inline_directive_for_same_env(
    client: AsyncClient,
) -> None:
    """Same env in subprocess mode → InlineSubworkflow directive, no spawn.

    The pre-A3 "no nested workflow calls" inline restriction is gone: the
    engine adapter carries chain/depth explicitly now.
    """
    from app.config import settings as live_settings

    env = (await client.post(
        "/environments",
        json={"name": "Inline Env", "python_version": "3.12", "packages": []},
    )).json()
    sub_id = await _make_sub(client, code="output = input", env_id=env["id"])

    previous_mode = live_settings.use_subprocess_runner
    live_settings.use_subprocess_runner = True
    try:
        outcome = await resolve_subworkflow(
            _call(sub_id, {"hello": "world"}), parent_env_id=env["id"]
        )
    finally:
        live_settings.use_subprocess_runner = previous_mode

    assert isinstance(outcome, InlineSubworkflow)
    # Trigger-gating still applies, and sources let the engine extract leaves.
    assert set(outcome.targets or []) == {"t", "c"}
    assert "t" in outcome.sources and "c" not in outcome.sources
    # The child's trigger is seeded with the call parameters.
    assert (outcome.cache or {})["t"]["main"] == {"hello": "world"}
