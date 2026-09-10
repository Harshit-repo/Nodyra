"""A bad artifact reference is a client error, not a server error.

The refs in a run's ``cache`` / pinned data arrive in the request body, so a
stale or tampered one is the caller's mistake. Both validations raised a bare
ValueError, which escaped as "500 Internal server error" with no detail —
telling the caller nothing, and filing their bad input as our bug in whatever
watches 5xx.

Reached simply by replaying a DatasetRef from an earlier run into the
single-node test endpoint, which is a plausible thing for the editor to do.
"""

import pytest
from httpx import AsyncClient

from app.exceptions import ArtifactRefInvalid
from app.services import artifacts as artifacts_service


def test_the_exception_is_a_client_error() -> None:
    assert ArtifactRefInvalid("x").http_status == 400


async def test_a_ref_whose_metadata_does_not_match_is_rejected(
    client: AsyncClient, monkeypatch
) -> None:
    """Checksum/size/name must match the stored row — that is the tamper check."""

    class _Row:
        id = "a" * 32
        name = "rows.parquet"
        run_id = "run-1"
        storage_key = "runs/run-1/rows.parquet"
        size_bytes = 100
        checksum_sha256 = "a" * 64
        org_id = "default"

    class _Session:
        async def scalar(self, _stmt):
            return _Row()

    tampered = {
        "__nodyra_artifact__": True,
        "version": 1,
        "artifact_id": "a" * 32,
        "name": "rows.parquet",
        "run_id": "run-1",
        "storage_key": "runs/run-1/rows.parquet",
        "size_bytes": 999_999,  # does not match the row
        "checksum_sha256": "a" * 64,
    }

    with pytest.raises(ArtifactRefInvalid) as excinfo:
        await artifacts_service.prepare_artifact_inputs(
            _Session(), tampered, run_id="run-2", org_id="default"
        )

    assert "does not match its stored file" in str(excinfo.value)


async def test_a_malformed_artifact_id_is_rejected(client: AsyncClient) -> None:
    """The store rejects a bad id with a plain ValueError; still a client error."""

    class _Session:
        async def scalar(self, _stmt):  # pragma: no cover - never reached
            raise AssertionError("validation should fail before the lookup")

    with pytest.raises(ArtifactRefInvalid) as excinfo:
        await artifacts_service.prepare_artifact_inputs(
            _Session(),
            {
                "__nodyra_artifact__": True,
                "version": 1,
                "artifact_id": "not-a-valid-id",
                "name": "x",
                "run_id": "r",
                "storage_key": "k",
                "size_bytes": 1,
                "checksum_sha256": "d" * 64,
            },
            run_id="run-2",
            org_id="default",
        )

    assert "Invalid artifact reference" in str(excinfo.value)


async def test_a_ref_to_another_org_is_rejected(
    client: AsyncClient, monkeypatch
) -> None:
    """The row lookup is org-scoped, so a foreign id simply is not found."""

    class _Session:
        async def scalar(self, _stmt):
            return None

    foreign = {
        "__nodyra_artifact__": True,
        "version": 1,
        "artifact_id": "b" * 32,
        "name": "secret.csv",
        "run_id": "someone-elses-run",
        "storage_key": "runs/someone-elses-run/secret.csv",
        "size_bytes": 10,
        "checksum_sha256": "b" * 64,
    }

    with pytest.raises(ArtifactRefInvalid) as excinfo:
        await artifacts_service.prepare_artifact_inputs(
            _Session(), foreign, run_id="run-2", org_id="default"
        )

    assert "unavailable in this organization" in str(excinfo.value)


async def test_the_endpoint_answers_400_not_500(client: AsyncClient) -> None:
    """End to end: the editor's single-node test must not report a 5xx."""
    graph = {
        "nodes": [
            {"id": "s", "type": "manual_trigger", "params": {"data": {}},
             "position": {"x": 0, "y": 0}},
            {"id": "rows", "type": "dataset_to_records", "params": {"max_rows": 100},
             "position": {"x": 200, "y": 0}},
        ],
        "edges": [{"source": "s", "target": "rows"}],
    }
    workflow_id = (await client.post("/workflows", json={"name": "Ref"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    stale = {
        "__nodyra_dataset__": True,
        "version": 1,
        "dataset_id": "gone",
        "format": "parquet",
        "artifact": {
            "__nodyra_artifact__": True,
            "version": 1,
            "artifact_id": "c" * 32,
            "name": "rows.parquet",
            "run_id": "long-gone-run",
            "storage_key": "runs/long-gone-run/rows.parquet",
            "size_bytes": 10,
            "checksum_sha256": "c" * 64,
        },
    }

    response = await client.post(
        f"/workflows/{workflow_id}/nodes/rows/test",
        json={"cache": {"s": {"main": stale}}},
    )

    assert response.status_code == 400, response.text
    assert "artifact" in response.text.lower()


# ---------------------------------------------------------------------------
# The single-node test enforces an output cap that a full run does not.


def test_an_output_cap_failure_says_it_is_a_test_limit() -> None:
    """Otherwise it reads as a broken node and sends the reader to rewrite it."""
    from app.routers.workflows import _explain_node_test_error

    explained = _explain_node_test_error(
        "ValueError: node output of 400000 bytes exceeds limit of 262144 bytes"
    )

    assert "400000" in explained  # the original message survives
    assert "single-node test" in explained
    assert "A full run does not fail here" in explained
    assert "max_output_bytes" in explained


def test_other_errors_are_passed_through_untouched() -> None:
    from app.routers.workflows import _explain_node_test_error

    assert _explain_node_test_error("KeyError: 'missing'") == "KeyError: 'missing'"
    assert _explain_node_test_error(None) is None
    assert _explain_node_test_error("") == ""
