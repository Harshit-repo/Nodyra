"""Tests for noodle.datasets — DatasetRef envelope helpers and hook registration."""
import pytest

from noodle import datasets
from noodle.artifacts import ARTIFACT_MARKER, ARTIFACT_VERSION, LocalArtifactStore
from noodle.context import artifact_store, current_node_id
from noodle.datasets import (
    DATASET_MARKER,
    DATASET_VERSION,
    dataset_from_records,
    is_dataset_ref,
    make_dataset_ref,
    materialize_dataset_rows,
    register_dataset_writer,
    register_materializer,
    reserve_artifact_path,
)


def _artifact_ref(artifact_id: str = "abc123") -> dict:
    return {
        ARTIFACT_MARKER: True,
        "version": ARTIFACT_VERSION,
        "artifact_id": artifact_id,
    }


def _valid_dataset_ref(**overrides) -> dict:
    base = {
        DATASET_MARKER: True,
        "version": DATASET_VERSION,
        "dataset_id": "ds123",
        "artifact": _artifact_ref(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# is_dataset_ref
# ---------------------------------------------------------------------------

def test_is_dataset_ref_accepts_valid_envelope() -> None:
    ref = make_dataset_ref(_artifact_ref())
    assert is_dataset_ref(ref) is True


def test_is_dataset_ref_rejects_non_dict() -> None:
    assert is_dataset_ref("string") is False
    assert is_dataset_ref(None) is False
    assert is_dataset_ref(42) is False
    assert is_dataset_ref([]) is False


def test_is_dataset_ref_rejects_missing_marker() -> None:
    ref = _valid_dataset_ref()
    del ref[DATASET_MARKER]
    assert is_dataset_ref(ref) is False


def test_is_dataset_ref_rejects_marker_set_to_false() -> None:
    ref = _valid_dataset_ref()
    ref[DATASET_MARKER] = False
    assert is_dataset_ref(ref) is False


def test_is_dataset_ref_rejects_wrong_version() -> None:
    ref = _valid_dataset_ref()
    ref["version"] = 999
    assert is_dataset_ref(ref) is False


def test_is_dataset_ref_rejects_missing_dataset_id() -> None:
    ref = _valid_dataset_ref()
    del ref["dataset_id"]
    assert is_dataset_ref(ref) is False


def test_is_dataset_ref_rejects_non_string_dataset_id() -> None:
    ref = _valid_dataset_ref()
    ref["dataset_id"] = 123
    assert is_dataset_ref(ref) is False


def test_is_dataset_ref_rejects_bad_artifact() -> None:
    ref = _valid_dataset_ref()
    ref["artifact"] = {"not": "an artifact ref"}
    assert is_dataset_ref(ref) is False


def test_is_dataset_ref_rejects_missing_artifact() -> None:
    ref = _valid_dataset_ref()
    del ref["artifact"]
    assert is_dataset_ref(ref) is False


# ---------------------------------------------------------------------------
# make_dataset_ref
# ---------------------------------------------------------------------------

def test_make_dataset_ref_produces_valid_envelope() -> None:
    art = _artifact_ref()
    ref = make_dataset_ref(art, row_count=5, schema=[{"name": "col1"}])
    assert ref[DATASET_MARKER] is True
    assert ref["version"] == DATASET_VERSION
    assert isinstance(ref["dataset_id"], str)
    assert ref["row_count"] == 5
    assert ref["column_count"] == 1


def test_make_dataset_ref_rejects_non_artifact_input() -> None:
    with pytest.raises(ValueError, match="artifact ref"):
        make_dataset_ref({"not": "an artifact"})


def test_make_dataset_ref_generates_unique_ids_each_call() -> None:
    art = _artifact_ref()
    ids = {make_dataset_ref(art)["dataset_id"] for _ in range(10)}
    assert len(ids) == 10


def test_make_dataset_ref_default_values() -> None:
    ref = make_dataset_ref(_artifact_ref())
    assert ref["preview"] == []
    assert ref["schema"] == []
    assert ref["row_count"] is None
    assert ref["column_count"] is None
    assert ref["format"] == "parquet"
    assert ref["metadata"] == {}
    assert ref["preview_truncated"] is False


def test_make_dataset_ref_stores_artifact() -> None:
    art = _artifact_ref("myid")
    ref = make_dataset_ref(art)
    assert ref["artifact"] is art


def test_make_dataset_ref_with_preview() -> None:
    art = _artifact_ref()
    preview = [{"a": 1}, {"a": 2}]
    ref = make_dataset_ref(art, preview=preview, preview_truncated=True)
    assert ref["preview"] == preview
    assert ref["preview_truncated"] is True


def test_reserve_artifact_path_uses_store_key_prefix(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1", key_prefix="org-a")
    store_token = artifact_store.set(store)
    node_token = current_node_id.set("node1")
    try:
        path, partial = reserve_artifact_path("data.parquet")
    finally:
        current_node_id.reset(node_token)
        artifact_store.reset(store_token)

    assert partial["storage_key"].startswith("org-a/runs/run1/node1/")
    assert path == (tmp_path / partial["storage_key"]).resolve()


def test_reserve_artifact_path_keeps_legacy_layout_without_prefix(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    store_token = artifact_store.set(store)
    node_token = current_node_id.set("node1")
    try:
        _, partial = reserve_artifact_path("data.parquet")
    finally:
        current_node_id.reset(node_token)
        artifact_store.reset(store_token)

    assert partial["storage_key"].startswith("runs/run1/node1/")


# ---------------------------------------------------------------------------
# materialize_dataset_rows — error paths
# ---------------------------------------------------------------------------

def test_materialize_raises_when_no_materializer() -> None:
    saved = datasets._materializer
    datasets._materializer = None
    try:
        ref = make_dataset_ref(_artifact_ref())
        with pytest.raises(RuntimeError, match="no dataset materializer registered"):
            materialize_dataset_rows(ref, cap=100)
    finally:
        datasets._materializer = saved


def test_materialize_raises_for_non_dataset_ref() -> None:
    with pytest.raises(ValueError, match="DatasetRef"):
        materialize_dataset_rows({"not": "a dataset"}, cap=100)


def test_materialize_raises_for_plain_dict() -> None:
    with pytest.raises(ValueError, match="DatasetRef"):
        materialize_dataset_rows({}, cap=100)


# ---------------------------------------------------------------------------
# register_materializer — functional
# ---------------------------------------------------------------------------

def test_register_and_invoke_materializer() -> None:
    calls: list = []

    def fake_materializer(ref, *, cap, allow_truncate=False):
        calls.append({"ref": ref, "cap": cap, "allow_truncate": allow_truncate})
        return [{"col": "val"}]

    saved = datasets._materializer
    register_materializer(fake_materializer)
    try:
        ref = make_dataset_ref(_artifact_ref())
        result = materialize_dataset_rows(ref, cap=50, allow_truncate=True)
        assert result == [{"col": "val"}]
        assert calls[0]["cap"] == 50
        assert calls[0]["allow_truncate"] is True
    finally:
        datasets._materializer = saved


# ---------------------------------------------------------------------------
# dataset_from_records — error paths
# ---------------------------------------------------------------------------

def test_dataset_from_records_raises_when_no_writer() -> None:
    saved = datasets._dataset_writer
    datasets._dataset_writer = None
    try:
        with pytest.raises(RuntimeError, match="no dataset writer registered"):
            dataset_from_records([{"x": 1}])
    finally:
        datasets._dataset_writer = saved


# ---------------------------------------------------------------------------
# register_dataset_writer — functional
# ---------------------------------------------------------------------------

def test_register_and_invoke_dataset_writer() -> None:
    calls: list = []

    def fake_writer(records, *, name="loop_output.parquet"):
        calls.append({"records": records, "name": name})
        return make_dataset_ref(_artifact_ref())

    saved = datasets._dataset_writer
    register_dataset_writer(fake_writer)
    try:
        dataset_from_records([{"a": 1}, {"a": 2}], name="output.parquet")
        assert calls[0]["records"] == [{"a": 1}, {"a": 2}]
        assert calls[0]["name"] == "output.parquet"
    finally:
        datasets._dataset_writer = saved


def test_register_dataset_writer_replaces_previous() -> None:
    calls_a: list = []
    calls_b: list = []

    def writer_a(records, *, name="x"):
        calls_a.append(records)
        return make_dataset_ref(_artifact_ref())

    def writer_b(records, *, name="x"):
        calls_b.append(records)
        return make_dataset_ref(_artifact_ref())

    saved = datasets._dataset_writer
    register_dataset_writer(writer_a)
    register_dataset_writer(writer_b)
    try:
        dataset_from_records([])
        assert calls_a == []
        assert len(calls_b) == 1
    finally:
        datasets._dataset_writer = saved
