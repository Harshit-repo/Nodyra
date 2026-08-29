"""Tenant-boundary hardening for artifact persistence (F-02, F-06).

Artifact refs are built by the run's own Python — user code — so nothing in a
ref may be trusted to address storage or to widen which secrets get decrypted.
"""

import pytest

from app.services import artifacts as artifacts_service
from app.services.artifacts import _row_from_ref, canonical_storage_keys


def _ref(**overrides) -> dict:
    ref = {
        "__nodyra_artifact__": True,
        "artifact_id": "a" * 32,
        "node_id": "node-1",
        "name": "report.csv",
        "kind": "table",
        "content_type": "text/csv",
        "size_bytes": 12,
        "storage_backend": "local",
    }
    ref.update(overrides)
    return ref


# ── F-06: storage keys must stay inside the run that produced them ──────────


def test_canonical_key_matches_the_store_formula(tmp_path):
    """The allowlist must mirror LocalArtifactStore or valid writes get rewritten
    to a key with no bytes behind it."""
    from nodyra.artifacts import LocalArtifactStore

    store = LocalArtifactStore(tmp_path, "run-1", key_prefix="org-a")
    written = store._storage_key("aid", "node-1", "report.csv")

    assert written in canonical_storage_keys(
        run_id="run-1",
        node_id="node-1",
        artifact_id="aid",
        name="report.csv",
        org_id="org-a",
    )


def test_supplied_key_for_this_run_is_preserved():
    ref = _ref(storage_key=f"org-a/runs/run-1/node-1/{'a' * 32}-report.csv")
    row = _row_from_ref(ref, "run-1", [], org_id="org-a")
    assert row.storage_key == ref["storage_key"]


def test_unprefixed_legacy_key_is_preserved():
    """A store built without an org id writes the unprefixed key; those rows
    must keep addressing their real bytes."""
    ref = _ref(storage_key=f"runs/run-1/node-1/{'a' * 32}-report.csv")
    row = _row_from_ref(ref, "run-1", [], org_id="org-a")
    assert row.storage_key == ref["storage_key"]


@pytest.mark.parametrize(
    "hostile_key",
    [
        # Another tenant's run, inside the artifact root so the backend's
        # traversal guard never fires.
        "org-victim/runs/run-victim/node-1/deadbeef-secrets.csv",
        # Same org, a different run the caller does not own.
        "org-a/runs/run-other/node-1/deadbeef-secrets.csv",
        # Right run, but a forged artifact id pointing at a sibling's bytes.
        "org-a/runs/run-1/node-1/0000-someone-elses.csv",
        # Absolute-looking and traversal forms.
        "/etc/passwd",
        "org-a/runs/run-1/../../run-victim/node-1/x-y.csv",
    ],
)
def test_crafted_key_is_replaced_with_the_canonical_key(hostile_key):
    row = _row_from_ref(_ref(storage_key=hostile_key), "run-1", [], org_id="org-a")

    assert row.storage_key != hostile_key
    assert row.storage_key == f"org-a/runs/run-1/node-1/{'a' * 32}-report.csv"


def test_missing_key_falls_back_to_the_canonical_key():
    row = _row_from_ref(_ref(), "run-1", [], org_id="org-a")
    assert row.storage_key == f"org-a/runs/run-1/node-1/{'a' * 32}-report.csv"


def test_key_components_are_sanitised():
    """A separator smuggled through node_id or name must not create a new path
    segment that escapes the run directory."""
    row = _row_from_ref(
        _ref(node_id="../../victim", name="../../../etc/passwd"),
        "run-1",
        [],
        org_id="org-a",
    )
    assert row.storage_key.startswith("org-a/runs/run-1/")
    assert ".." not in row.storage_key


# ── F-02: redaction must decrypt one tenant's secrets, not every tenant's ────


async def test_persist_uses_the_org_scoped_secret_loader(monkeypatch):
    """Regression: persist_artifact_refs called the all-orgs loader, decrypting
    every organization's credential plaintext into one process cache for a
    single run's redaction."""
    calls: dict[str, list] = {"all_orgs": [], "scoped": []}

    async def _all_orgs(session):
        calls["all_orgs"].append(True)
        return []

    async def _scoped(org_id, session):
        calls["scoped"].append(org_id)
        return []

    monkeypatch.setattr(artifacts_service, "load_secret_values", _all_orgs)
    monkeypatch.setattr(artifacts_service, "load_secret_values_for_org", _scoped)

    class _Session:
        async def scalar(self, _stmt):
            return "org-a"

        async def scalars(self, _stmt):
            class _R:
                def all(self_inner):
                    return []

            return _R()

        def add(self, _obj):
            pass

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(artifacts_service, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(
        artifacts_service, "path_for_artifact", lambda row: (_ for _ in ()).throw(ValueError)
    )

    await artifacts_service.persist_artifact_refs("run-1", [_ref()])

    assert calls["scoped"] == ["org-a"], "expected the org-scoped loader"
    assert calls["all_orgs"] == [], "all-orgs loader must not run when the org is known"
