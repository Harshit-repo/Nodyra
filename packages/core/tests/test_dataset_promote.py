"""Tests for nodyra.dataset_promote — auto-promotion of large values."""

from nodyra.artifacts import ARTIFACT_MARKER, LocalArtifactStore
from nodyra.context import artifact_store, current_node_id
from nodyra.dataset_promote import (
    _estimate_bytes,
    _is_dataframe,
    _row_shaped,
    promote_value,
)

# ---------------------------------------------------------------------------
# _is_dataframe
# ---------------------------------------------------------------------------

def test_is_dataframe_false_for_scalars() -> None:
    assert _is_dataframe(42) is False
    assert _is_dataframe("string") is False
    assert _is_dataframe(None) is False
    assert _is_dataframe(3.14) is False


def test_is_dataframe_false_for_list() -> None:
    assert _is_dataframe([{"a": 1}]) is False


def test_is_dataframe_false_for_plain_dict() -> None:
    assert _is_dataframe({"columns": "x"}) is False


def test_is_dataframe_true_for_duck_typed_object() -> None:
    # _is_dataframe checks type().__name__ == "DataFrame" — must be named exactly
    class DataFrame:
        columns = ["a", "b"]
        shape = (10, 2)

        def head(self, n=5):
            return self

        def to_dict(self, orient=None):
            return []

    assert _is_dataframe(DataFrame()) is True


def test_is_dataframe_false_when_name_is_not_dataframe() -> None:
    class NotDataFrame:
        columns = ["a", "b"]
        shape = (10, 2)

        def head(self, n=5):
            return self

        def to_dict(self, orient=None):
            return []

    assert _is_dataframe(NotDataFrame()) is False


def test_is_dataframe_false_when_missing_shape() -> None:
    class DataFrame:
        columns = ["a"]

        def head(self, n=5):
            return self

        def to_dict(self, orient=None):
            return []

    assert _is_dataframe(DataFrame()) is False


def test_is_dataframe_false_when_missing_to_dict() -> None:
    class DataFrame:
        columns = ["a"]
        shape = (1, 1)

        def head(self, n=5):
            return self

    assert _is_dataframe(DataFrame()) is False


# ---------------------------------------------------------------------------
# _row_shaped
# ---------------------------------------------------------------------------

def test_row_shaped_true_for_list_of_dicts() -> None:
    data = [{"a": 1}, {"a": 2}, {"a": 3}]
    assert _row_shaped(data, min_rows=3) is True


def test_row_shaped_false_when_below_min_rows() -> None:
    data = [{"a": 1}]
    assert _row_shaped(data, min_rows=2) is False


def test_row_shaped_false_for_non_list() -> None:
    assert _row_shaped({"a": 1}, min_rows=1) is False
    assert _row_shaped("string", min_rows=1) is False


def test_row_shaped_false_when_not_all_dicts() -> None:
    assert _row_shaped([{"a": 1}, "oops", {"a": 3}], min_rows=1) is False
    assert _row_shaped([1, 2, 3], min_rows=1) is False


def test_row_shaped_samples_only_first_10() -> None:
    # Elements past index 10 are not inspected
    data = [{"a": i} for i in range(15)] + ["not_a_dict"]
    assert _row_shaped(data, min_rows=1) is True


def test_row_shaped_false_for_empty_list() -> None:
    assert _row_shaped([], min_rows=1) is False


# ---------------------------------------------------------------------------
# _estimate_bytes
# ---------------------------------------------------------------------------

def test_estimate_bytes_returns_positive_for_dict() -> None:
    size = _estimate_bytes({"key": "value"}, cap=1000)
    assert size > 0
    assert size < 1000


def test_estimate_bytes_exceeds_cap_for_large_value() -> None:
    large = list(range(10000))
    size = _estimate_bytes(large, cap=10)
    assert size > 10


def test_estimate_bytes_uses_str_fallback_for_non_serializable() -> None:
    class Custom:
        def __repr__(self):
            return "custom"

    # json.dumps with default=str should serialize it; result has some positive length
    size = _estimate_bytes(Custom(), cap=100000)
    assert isinstance(size, int)
    assert size > 0


# ---------------------------------------------------------------------------
# promote_value — passthrough (small values stay inline)
# ---------------------------------------------------------------------------

def test_promote_value_passes_small_dict() -> None:
    val = {"key": "value"}
    result = promote_value(val, port_name="out", max_inline_rows=100, max_inline_bytes=10_000)
    assert result == val


def test_promote_value_passes_small_bytes() -> None:
    data = b"small"
    result = promote_value(data, port_name="out", max_inline_rows=100, max_inline_bytes=10_000)
    assert result == data


def test_promote_value_passes_small_string() -> None:
    s = "hello world"
    result = promote_value(s, port_name="out", max_inline_rows=100, max_inline_bytes=10_000)
    assert result == s


def test_promote_value_passes_small_list_of_dicts() -> None:
    data = [{"a": 1}, {"a": 2}]
    result = promote_value(data, port_name="out", max_inline_rows=10, max_inline_bytes=10_000)
    assert result == data


def test_promote_value_passes_non_row_shaped_list() -> None:
    data = [1, 2, 3, 4, 5]
    result = promote_value(data, port_name="out", max_inline_rows=3, max_inline_bytes=10_000)
    assert result == data


def test_promote_value_passes_none() -> None:
    result = promote_value(None, port_name="out", max_inline_rows=100, max_inline_bytes=10_000)
    assert result is None


# ---------------------------------------------------------------------------
# promote_value — large bytes → artifact
# ---------------------------------------------------------------------------

def test_promote_value_promotes_large_bytes_to_artifact(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        large_bytes = b"x" * 1001
        result = promote_value(
            large_bytes, port_name="out", max_inline_rows=100, max_inline_bytes=100
        )
        assert isinstance(result, dict)
        assert result.get(ARTIFACT_MARKER) is True
        assert result["kind"] == "binary"
    finally:
        artifact_store.reset(st)
        current_node_id.reset(nt)


def test_promote_value_large_bytes_falls_back_without_store() -> None:
    # No artifact store in context → catches RuntimeError → returns value as-is
    large_bytes = b"x" * 1001
    result = promote_value(
        large_bytes, port_name="out", max_inline_rows=100, max_inline_bytes=100
    )
    assert result == large_bytes


# ---------------------------------------------------------------------------
# promote_value — large string → artifact
# ---------------------------------------------------------------------------

def test_promote_value_promotes_large_string_to_artifact(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run1")
    st = artifact_store.set(store)
    nt = current_node_id.set("node1")
    try:
        large_str = "x" * 1001
        result = promote_value(
            large_str, port_name="out", max_inline_rows=100, max_inline_bytes=100
        )
        assert isinstance(result, dict)
        assert result.get(ARTIFACT_MARKER) is True
        assert result["kind"] == "text"
    finally:
        artifact_store.reset(st)
        current_node_id.reset(nt)


def test_promote_value_large_string_falls_back_without_store() -> None:
    large_str = "x" * 1001
    result = promote_value(
        large_str, port_name="out", max_inline_rows=100, max_inline_bytes=100
    )
    assert result == large_str


# ---------------------------------------------------------------------------
# promote_value — bytearray and memoryview
# ---------------------------------------------------------------------------

def test_promote_value_passes_small_bytearray() -> None:
    data = bytearray(b"tiny")
    result = promote_value(data, port_name="out", max_inline_rows=100, max_inline_bytes=10_000)
    assert result == data


def test_promote_value_promotes_large_bytearray(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, "run2")
    st = artifact_store.set(store)
    nt = current_node_id.set("n2")
    try:
        large = bytearray(b"y" * 1001)
        result = promote_value(
            large, port_name="data", max_inline_rows=100, max_inline_bytes=100
        )
        assert isinstance(result, dict)
        assert result.get(ARTIFACT_MARKER) is True
    finally:
        artifact_store.reset(st)
        current_node_id.reset(nt)
