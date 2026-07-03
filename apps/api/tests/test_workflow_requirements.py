"""Per-workflow Python requirements."""

from __future__ import annotations

from httpx import AsyncClient


async def test_requirements_roundtrip(client: AsyncClient) -> None:
    workflow = (await client.post("/workflows", json={"name": "req-wf"})).json()

    response = await client.put(
        f"/workflows/{workflow['id']}",
        json={"requirements": ["pandas>=2.0", "requests"]},
    )

    assert response.status_code == 200, response.text
    detail = (await client.get(f"/workflows/{workflow['id']}")).json()
    assert detail["requirements"] == ["pandas>=2.0", "requests"]


async def test_invalid_requirement_line_422(client: AsyncClient) -> None:
    workflow = (await client.post("/workflows", json={"name": "req-bad"})).json()

    response = await client.put(
        f"/workflows/{workflow['id']}",
        json={"requirements": ["pandas >=== nope!!"]},
    )

    assert response.status_code == 422


def test_preflight_reports_missing_workflow_requirements() -> None:
    from app.services.package_preflight import missing_workflow_requirements

    missing = missing_workflow_requirements(
        workflow_requirements=["pandas>=2.0", "definitely-not-installed-xyz"],
        installed={"pandas": "2.2.0"},
    )
    assert missing == ["definitely-not-installed-xyz"]
