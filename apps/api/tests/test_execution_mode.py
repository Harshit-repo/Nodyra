"""Workflow execution-mode precedence: fail-closed, escalate-only."""

from app.config import settings
from app.services.sandbox_policy import resolve_execution_mode

MANUAL_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {},
            "position": {"x": 0, "y": 0},
        }
    ],
    "edges": [],
}


def test_multi_tenant_strict_always_sandboxed(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "sandbox_policy_strict", True)
    assert resolve_execution_mode(run_override=None, workflow_mode="standard") == "sandboxed"


def test_required_mode_sandboxes_everything(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    assert resolve_execution_mode(run_override=None, workflow_mode="standard") == "sandboxed"


def test_inherit_follows_deployment_default(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    monkeypatch.setattr(settings, "sandbox_workflow_default", "standard")
    assert resolve_execution_mode(run_override=None, workflow_mode="inherit") == "standard"
    monkeypatch.setattr(settings, "sandbox_workflow_default", "sandboxed")
    assert resolve_execution_mode(run_override=None, workflow_mode="inherit") == "sandboxed"


def test_inherit_with_sandbox_off_is_standard(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    assert resolve_execution_mode(run_override=None, workflow_mode="inherit") == "standard"


def test_run_override_escalates_but_never_downgrades(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    assert (
        resolve_execution_mode(run_override="sandboxed", workflow_mode="standard")
        == "sandboxed"
    )
    assert (
        resolve_execution_mode(run_override="standard", workflow_mode="sandboxed")
        == "sandboxed"
    )


def test_workflow_sandboxed_wins_even_when_sandbox_off(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    assert resolve_execution_mode(run_override=None, workflow_mode="sandboxed") == "sandboxed"


async def test_sandboxed_workflow_409s_when_sandbox_off(client, monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    wf = (await client.post("/workflows", json={"name": "Sandboxed"})).json()
    resp = await client.put(
        f"/workflows/{wf['id']}",
        json={"graph": MANUAL_GRAPH, "execution_mode": "sandboxed"},
    )
    assert resp.status_code == 200
    assert resp.json()["execution_mode"] == "sandboxed"

    resp = await client.post(f"/workflows/{wf['id']}/run", json={})
    assert resp.status_code == 409
    assert "sandbox" in resp.json()["detail"].lower()


async def test_sandboxed_workflow_409s_on_non_container_pool(client, monkeypatch):
    """A sandboxed run bound to a non-container runner pool is refused even
    when the sandbox itself is available — remote dispatch would otherwise
    execute it unsandboxed on the agent host."""
    from app import models
    from app.services import runner as runner_module

    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    async with runner_module.SessionLocal() as db:
        pool = models.RunnerPool(name="agents", provider="agent")
        db.add(pool)
        await db.commit()
        pool_id = pool.id

    wf = (await client.post("/workflows", json={"name": "SandboxedPooled"})).json()
    resp = await client.put(
        f"/workflows/{wf['id']}",
        json={
            "graph": MANUAL_GRAPH,
            "execution_mode": "sandboxed",
            "default_runner_pool_id": pool_id,
        },
    )
    assert resp.status_code == 200

    resp = await client.post(f"/workflows/{wf['id']}/run", json={})
    assert resp.status_code == 409
    assert "container provider" in resp.json()["detail"]
