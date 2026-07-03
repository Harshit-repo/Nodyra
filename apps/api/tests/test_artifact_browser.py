from app.models import Artifact
from app.services import retention


async def _seed_artifact() -> Artifact:
    async with retention.SessionLocal() as session:
        row = Artifact(
            id="artifact_browser_seed",
            run_id=None,
            node_id="node-1",
            name="report.csv",
            kind="table",
            content_type="text/csv",
            size_bytes=12,
            checksum_sha256="0" * 64,
            storage_backend="local",
            storage_key="uploads/artifact_browser_seed/report.csv",
            artifact_metadata={},
            preview=None,
        )
        session.add(row)
        await session.commit()
        return row


async def test_list_artifacts_workspace(client):
    seeded_artifact = await _seed_artifact()

    resp = await client.get("/artifacts", params={"limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(a["id"] == seeded_artifact.id for a in body["items"])
    assert body["items"][0]["checksum_sha256"] == "0" * 64


async def test_list_artifacts_filters_by_kind(client):
    await _seed_artifact()

    resp = await client.get("/artifacts", params={"kind": "definitely-not-a-kind"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
