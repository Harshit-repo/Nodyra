"""Streamed artifacts must carry a checksum, like buffered ones do.

Nodyra has two ways to produce an artifact:

  ``LocalArtifactStore.write_bytes()``  buffered — hashes the bytes it holds
  ``reserve_artifact_path`` + ``finalize_artifact_ref``
                                          streamed — the node writes straight to
                                          the path so a large parquet or CSV is
                                          never held in memory

The streamed path set ``size_bytes``, ``metadata`` and ``preview``, but never
``checksum_sha256``. Every artifact produced that way — which is every dataset
export and every CSV — reached the database with a NULL checksum.

Found by following the in-app activation checklist, whose third step is:

    "Inspect output or an artifact — Preview the produced data and **verify its
     checksum and lineage**."

There was no checksum to verify. Four artifacts from the starter template, all
NULL, on the one path every new user is walked through.
"""

from __future__ import annotations

import hashlib

import pytest

from nodyra.artifacts import LocalArtifactStore
from nodyra.datasets import finalize_artifact_ref, reserve_artifact_path


@pytest.fixture
def store(tmp_path, monkeypatch):
    made = LocalArtifactStore(tmp_path, "run-checksum")
    monkeypatch.setattr("nodyra.datasets._store", lambda: made)
    monkeypatch.setattr("nodyra.datasets.current_node_id", _fixed_node("writer"), raising=False)
    monkeypatch.setattr("nodyra.artifacts.current_node_id", _fixed_node("writer"), raising=False)
    return made


def _fixed_node(value: str):
    class _Ctx:
        @staticmethod
        def get():
            return value

    return _Ctx


def test_the_buffered_path_has_always_had_a_checksum(store):
    """Guard the guard: the two paths should agree, and this is the one that
    was already right."""
    payload = b"id,region\n1,west\n"
    ref = store.write_bytes(payload, name="buffered.csv", kind="csv_export")

    assert ref["checksum_sha256"] == hashlib.sha256(payload).hexdigest()


def test_the_streamed_path_also_produces_a_checksum(store):
    """The bug. A node that writes straight to the reserved path must still end
    up with a verifiable artifact."""
    payload = b"id,region,amount\n1,west,120\n3,west,240\n"
    path, partial = reserve_artifact_path("filtered-sales.csv", kind="csv_export")
    path.write_bytes(payload)

    ref = finalize_artifact_ref(path, partial, metadata={"format": "csv", "rows": 2})

    assert ref.get("checksum_sha256") == hashlib.sha256(payload).hexdigest(), (
        "streamed artifacts still reach the database with a NULL checksum"
    )


def test_both_paths_agree_on_identical_bytes(store):
    """The property that makes the checksum worth anything: it depends only on
    the content, not on how the artifact happened to be written."""
    payload = b"same bytes, different route\n"

    buffered = store.write_bytes(payload, name="buffered.bin", kind="binary")
    path, partial = reserve_artifact_path("streamed.bin", kind="binary")
    path.write_bytes(payload)
    streamed = finalize_artifact_ref(path, partial)

    assert buffered["checksum_sha256"] == streamed["checksum_sha256"]


def test_the_checksum_actually_detects_a_change(store):
    """A digest that does not change when the bytes change is decoration."""
    first, partial_a = reserve_artifact_path("a.bin", kind="binary")
    first.write_bytes(b"original")
    ref_a = finalize_artifact_ref(first, partial_a)

    second, partial_b = reserve_artifact_path("b.bin", kind="binary")
    second.write_bytes(b"tampered")
    ref_b = finalize_artifact_ref(second, partial_b)

    assert ref_a["checksum_sha256"] != ref_b["checksum_sha256"]


def test_an_empty_artifact_still_gets_the_empty_digest(store):
    """Zero bytes is a legitimate artifact, and has a well-defined sha256."""
    path, partial = reserve_artifact_path("empty.csv", kind="csv_export")
    path.write_bytes(b"")

    ref = finalize_artifact_ref(path, partial)

    assert ref["size_bytes"] == 0
    assert ref["checksum_sha256"] == hashlib.sha256(b"").hexdigest()


def test_a_large_artifact_is_hashed_without_being_held_whole(store):
    """The streamed path exists so big files are never fully in memory; hashing
    must not undo that. This asserts correctness on a payload larger than one
    read chunk."""
    payload = bytes(range(256)) * 8192  # 2 MiB, spans multiple chunks
    path, partial = reserve_artifact_path("big.bin", kind="binary")
    path.write_bytes(payload)

    ref = finalize_artifact_ref(path, partial)

    assert ref["size_bytes"] == len(payload)
    assert ref["checksum_sha256"] == hashlib.sha256(payload).hexdigest()
