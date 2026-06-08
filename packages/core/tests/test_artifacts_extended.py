"""Extended tests for noodle.artifacts — limits, path safety, and read helpers."""
import json
import pytest

from noodle import artifacts
from noodle.artifacts import (
    ARTIFACT_MARKER,
    LocalArtifactStore,
    is_artifact_ref,
    sanitize_name,
    write_bytes,
    write_json,
    write_text,
)
from noodle.context import artifact_store, current_node_id


def _with_store(store, fn):
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        return fn()
    finally:
        current_node_id.reset(nt)
        artifact_store.reset(st)


# ---------------------------------------------------------------------------
# is_artifact_ref
# ---------------------------------------------------------------------------

def test_is_artifact_ref_valid(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    ref = _with_store(store, lambda: write_bytes(b"data", name="f.bin"))
    assert is_artifact_ref(ref) is True


def test_is_artifact_ref_rejects_non_dict() -> None:
    assert is_artifact_ref("string") is False
    assert is_artifact_ref(None) is False
    assert is_artifact_ref(42) is False


def test_is_artifact_ref_rejects_missing_marker() -> None:
    assert is_artifact_ref({"version": 1, "artifact_id": "abc"}) is False


def test_is_artifact_ref_rejects_wrong_marker_type() -> None:
    assert is_artifact_ref({ARTIFACT_MARKER: False, "version": 1, "artifact_id": "abc"}) is False


# ---------------------------------------------------------------------------
# sanitize_name
# ---------------------------------------------------------------------------

def test_sanitize_name_strips_path_traversal() -> None:
    assert sanitize_name("../etc/passwd") == "passwd"
    assert sanitize_name("../../secret.txt") == "secret.txt"


def test_sanitize_name_removes_special_chars() -> None:
    result = sanitize_name("my file (1).csv")
    assert "/" not in result
    assert "\\" not in result


def test_sanitize_name_handles_none_and_empty() -> None:
    assert sanitize_name(None) == "artifact"
    assert sanitize_name("") == "artifact"


def test_sanitize_name_truncates_long_names() -> None:
    name = "a" * 300 + ".txt"
    result = sanitize_name(name)
    assert len(result) <= 180


def test_sanitize_name_preserves_extension() -> None:
    result = sanitize_name("report.csv")
    assert result.endswith(".csv")


# ---------------------------------------------------------------------------
# LocalArtifactStore — count limit
# ---------------------------------------------------------------------------

def test_artifact_store_enforces_count_limit(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1", max_count=2)
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        write_bytes(b"first", name="a.bin")
        write_bytes(b"second", name="b.bin")
        with pytest.raises(ValueError, match="artifact limit reached"):
            write_bytes(b"third", name="c.bin")
    finally:
        current_node_id.reset(nt)
        artifact_store.reset(st)


def test_artifact_store_no_limit_allows_many(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")  # max_count=0 → unlimited
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        for i in range(20):
            write_bytes(b"data", name=f"file{i}.bin")
    finally:
        current_node_id.reset(nt)
        artifact_store.reset(st)


# ---------------------------------------------------------------------------
# LocalArtifactStore — path traversal protection
# ---------------------------------------------------------------------------

def test_store_rejects_storage_key_that_escapes_base_dir(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    evil_ref = {
        ARTIFACT_MARKER: True,
        "version": 1,
        "artifact_id": "evil",
        "storage_key": "../../etc/passwd",
    }
    with pytest.raises(ValueError, match="escapes the artifact directory"):
        store.path_for_ref(evil_ref)


def test_store_path_for_ref_requires_artifact_id(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    ref_no_id = {ARTIFACT_MARKER: True, "version": 1, "artifact_id": ""}
    with pytest.raises(ValueError, match="artifact_id"):
        store.path_for_ref(ref_no_id)


# ---------------------------------------------------------------------------
# LocalArtifactStore — open() write mode rejection
# ---------------------------------------------------------------------------

def test_store_open_rejects_write_mode(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    ref = _with_store(store, lambda: write_bytes(b"hello", name="f.bin"))
    with pytest.raises(ValueError, match="read-only"):
        store.open(ref, mode="wb")


def test_store_open_rejects_append_mode(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    ref = _with_store(store, lambda: write_bytes(b"hello", name="f.bin"))
    with pytest.raises(ValueError, match="read-only"):
        store.open(ref, mode="ab")


def test_store_open_rejects_readwrite_mode(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    ref = _with_store(store, lambda: write_bytes(b"hello", name="f.bin"))
    with pytest.raises(ValueError, match="read-only"):
        store.open(ref, mode="r+b")


def test_store_open_read_mode_works(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    ref = _with_store(store, lambda: write_bytes(b"hello", name="f.bin"))
    with store.open(ref, mode="rb") as fh:
        assert fh.read() == b"hello"


def test_store_open_rejects_invalid_ref(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    with pytest.raises(ValueError, match="artifact ref"):
        store.open({"not": "a ref"}, mode="rb")


# ---------------------------------------------------------------------------
# read_bytes / read_text / read_json
# ---------------------------------------------------------------------------

def test_read_bytes_roundtrip(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    ref = _with_store(store, lambda: write_bytes(b"\x00\xff\xfe", name="raw.bin"))
    assert store.read_bytes(ref) == b"\x00\xff\xfe"


def test_read_bytes_rejects_invalid_ref(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    with pytest.raises(ValueError, match="artifact ref"):
        store.read_bytes({"not": "a ref"})


def test_write_text_roundtrip(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    _with_store(store, lambda: write_text("hello ñoño", name="text.txt"))
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        ref = write_text("hello ñoño", name="text2.txt")
        from noodle.artifacts import read_text
        assert read_text(ref) == "hello ñoño"
    finally:
        current_node_id.reset(nt)
        artifact_store.reset(st)


def test_write_json_roundtrip(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        payload = {"items": [1, 2, 3], "nested": {"key": "val"}}
        ref = write_json(payload, name="data.json")
        from noodle.artifacts import read_json
        assert read_json(ref) == payload
    finally:
        current_node_id.reset(nt)
        artifact_store.reset(st)


# ---------------------------------------------------------------------------
# Missing store context raises RuntimeError
# ---------------------------------------------------------------------------

def test_write_bytes_without_store_raises() -> None:
    with pytest.raises(RuntimeError, match="artifacts are not available"):
        # No artifact_store in context
        from noodle.artifacts import _store
        _store()


def test_write_text_content_type_default(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        ref = write_text("data", name="f.txt")
        assert "text/plain" in ref["content_type"]
    finally:
        current_node_id.reset(nt)
        artifact_store.reset(st)


# ---------------------------------------------------------------------------
# write_dataframe error paths
# ---------------------------------------------------------------------------

def test_write_dataframe_rejects_unsupported_format(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        with pytest.raises(ValueError, match="csv or json"):
            from noodle.artifacts import write_dataframe
            write_dataframe(object(), name="df.parquet", format="parquet")
    finally:
        current_node_id.reset(nt)
        artifact_store.reset(st)


def test_write_dataframe_rejects_non_dataframe_csv(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        with pytest.raises((ValueError, AttributeError)):
            from noodle.artifacts import write_dataframe
            write_dataframe({"not": "a dataframe"}, name="df.csv", format="csv")
    finally:
        current_node_id.reset(nt)
        artifact_store.reset(st)
