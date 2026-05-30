"""Tests for DatasetRef envelope helpers + dataset nodes."""

from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref
from noodle_nodes.datasets import (
    csv_parse,
    csv_write,
    dataset_filter,
    dataset_limit,
    dataset_preview,
    dataset_select,
    dataset_to_records,
    duckdb_sql,
    records_to_dataset_node,
)


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)


def test_csv_parse_returns_dataset_ref(store_ctx) -> None:
    ref = csv_parse(text="a,b\n1,2\n3,4\n", has_header=True)
    assert is_dataset_ref(ref)
    assert ref["row_count"] == 2
    assert {c["name"] for c in ref["schema"]} == {"a", "b"}


def test_csv_parse_empty_text_produces_zero_rows(store_ctx) -> None:
    ref = csv_parse(text="a,b\n", has_header=True)
    assert is_dataset_ref(ref)
    assert ref["row_count"] == 0


def test_records_to_dataset_node_handles_list(store_ctx) -> None:
    ref = records_to_dataset_node(input=[{"id": 1}, {"id": 2}])
    assert is_dataset_ref(ref)
    assert ref["row_count"] == 2


def test_records_to_dataset_node_handles_wrapped_records(store_ctx) -> None:
    ref = records_to_dataset_node(input={"records": [{"id": 1}]})
    assert is_dataset_ref(ref)
    assert ref["row_count"] == 1


def test_dataset_preview_returns_inline(store_ctx) -> None:
    ref = csv_parse(text="a,b\n1,2\n3,4\n", has_header=True)
    out = dataset_preview(input=ref, limit=10)
    assert out["row_count"] == 2
    assert out["truncated"] is False
    assert out["rows"] == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


def test_dataset_to_records_caps(store_ctx) -> None:
    ref = csv_parse(text="a\n1\n2\n3\n", has_header=True)
    with pytest.raises(ValueError):
        dataset_to_records(input=ref, max_rows=1, allow_truncate=False)
    rows = dataset_to_records(input=ref, max_rows=1, allow_truncate=True)
    assert len(rows) == 1


def test_dataset_select(store_ctx) -> None:
    ref = csv_parse(text="a,b,c\n1,2,3\n", has_header=True)
    out = dataset_select(input=ref, columns="a,c")
    assert [s["name"] for s in out["schema"]] == ["a", "c"]


def test_dataset_filter(store_ctx) -> None:
    ref = csv_parse(text="x\n1\n2\n3\n", has_header=True)
    out = dataset_filter(input=ref, where="x > 1")
    assert out["row_count"] == 2


def test_dataset_limit(store_ctx) -> None:
    ref = csv_parse(text="x\n1\n2\n3\n", has_header=True)
    out = dataset_limit(input=ref, limit=2)
    assert out["row_count"] == 2


def test_csv_write_returns_artifact(store_ctx) -> None:
    ref = csv_parse(text="a,b\n1,2\n", has_header=True)
    out = csv_write(input=ref, filename="out.csv")
    assert is_artifact_ref(out)
    assert out["content_type"].startswith("text/csv")


def test_duckdb_sql(store_ctx) -> None:
    ref = csv_parse(text="x\n1\n2\n3\n", has_header=True)
    out = duckdb_sql(input=ref, sql="SELECT COUNT(*) AS n FROM input")
    rows = dataset_to_records(input=out, max_rows=10)
    assert rows == [{"n": 3}]


def test_duckdb_sql_rejects_multi_statement(store_ctx) -> None:
    ref = csv_parse(text="x\n1\n", has_header=True)
    with pytest.raises(ValueError):
        duckdb_sql(input=ref, sql="SELECT 1; SELECT 2")


def test_duckdb_sql_rejects_non_select(store_ctx) -> None:
    ref = csv_parse(text="x\n1\n", has_header=True)
    with pytest.raises(ValueError):
        duckdb_sql(input=ref, sql="DELETE FROM input")


def test_dataset_filter_input_kind_validation_via_engine(store_ctx) -> None:
    # filter expects DatasetRef on its input
    from noodle.engine import _validate_input_kinds
    from noodle.sdk import registry

    node_def = registry.get("dataset_filter")
    with pytest.raises(ValueError, match="DatasetRef"):
        _validate_input_kinds(node_def, {"input": [{"a": 1}]}, "n")
