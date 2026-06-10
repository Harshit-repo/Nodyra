"""B3/B4: cross-org ingress + run org pinning, end to end.

With multi-tenancy ON, a webhook delivery (external caller, no org identity)
must still find a non-default org's workflow, and the run it creates must be
stamped with the WORKFLOW's org — not the ingress request's default-org
context, and not left to the system context.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import models
from app.config import settings
from app.services import retention
from app.tenancy import DEFAULT_ORG_ID, current_org_id, run_as_system


def _webhook_graph(path: str) -> dict:
    return {
        "nodes": [
            {
                "id": "hook",
                "type": "webhook_trigger",
                "params": {"path": path, "http_method": "POST"},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "proc",
                "type": "code",
                "params": {"code": "output = input['body']"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "hook",
                "sourceHandle": "out",
                "target": "proc",
                "targetHandle": "in",
            }
        ],
    }


@pytest.fixture
def mt_on(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set(None)
    yield
    current_org_id.reset(token)


async def test_webhook_fires_foreign_org_workflow_and_pins_run(
    client: AsyncClient, mt_on
) -> None:
    # Seed an org-x workflow directly (the API surface for cross-org setup is
    # exercised in test_orgs_router; here we care about the ingress path).
    async with retention.SessionLocal() as session:
        session.add_all(
            [
                models.Organization(id=DEFAULT_ORG_ID, name="D", slug="default"),
                models.Organization(id="org-x", name="X", slug="x"),
            ]
        )
        token = current_org_id.set("org-x")
        try:
            wf = models.Workflow(
                name="x-hook",
                active=True,
                draft_graph=None,
            )
            wf.versions.append(
                models.WorkflowVersion(version=1, graph=_webhook_graph("xorders"))
            )
            session.add(wf)
            await session.commit()
            assert wf.org_id == "org-x"
            workflow_id = wf.id
        finally:
            current_org_id.reset(token)

    # External delivery: no auth, no X-Org-Id. Must match the org-x workflow.
    response = (await client.post("/webhook/xorders", json={"order": 1})).json()
    assert response.get("runs"), response

    async with retention.SessionLocal() as session:
        with run_as_system():
            run = await session.scalar(
                select(models.Run).where(models.Run.workflow_id == workflow_id)
            )
            assert run is not None
            assert run.org_id == "org-x"
