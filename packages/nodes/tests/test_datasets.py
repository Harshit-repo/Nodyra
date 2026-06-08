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
    map_dataset,
    polars_transform,
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


def test_duckdb_sql_blocks_server_file_read(store_ctx, tmp_path) -> None:
    """DSQ-2: the external-access latch must block reading arbitrary server
    files via DuckDB table functions, even though it's a valid SELECT."""
    ref = csv_parse(text="x\n1\n", has_header=True)
    secret = tmp_path / "secret.csv"
    secret.write_text("token\nhunter2\n")
    escaped = str(secret).replace("'", "''")
    with pytest.raises(Exception):  # noqa: B017 - DuckDB raises on disabled FS access
        duckdb_sql(input=ref, sql=f"SELECT * FROM read_csv_auto('{escaped}')")


def test_dataset_filter_input_kind_validation_via_engine(store_ctx) -> None:
    # filter expects DatasetRef on its input
    from noodle.engine import _validate_input_kinds
    from noodle.sdk import registry

    node_def = registry.get("dataset_filter")
    with pytest.raises(ValueError, match="DatasetRef"):
        _validate_input_kinds(node_def, {"input": [{"a": 1}]}, "n")


def test_materialize_dataset_expands_rows(store_ctx) -> None:
    from noodle_nodes.datasets import materialize_dataset

    ref = csv_parse(text="x\n1\n2\n3\n", has_header=True)
    rows = materialize_dataset(ref, cap=10)
    assert rows == [{"x": 1}, {"x": 2}, {"x": 3}]


def test_materialize_dataset_caps(store_ctx) -> None:
    from noodle_nodes.datasets import materialize_dataset

    ref = csv_parse(text="x\n1\n2\n3\n", has_header=True)
    with pytest.raises(ValueError):
        materialize_dataset(ref, cap=2)
    assert materialize_dataset(ref, cap=2, allow_truncate=True) == [{"x": 1}, {"x": 2}]


async def test_engine_expands_dataset_ref_into_loop_items(store_ctx) -> None:
    """A DatasetRef wired into a per-item node is expanded into its rows."""
    from noodle.engine import execute
    from noodle.models import Edge, GraphNode, WorkflowGraph
    from noodle.sdk import registry

    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": [{"id": 1}, {"id": 2}, {"id": 3}]},
            ),
            GraphNode(id="ds", type="records_to_dataset"),
            GraphNode(id="loop", type="loop_over_items"),
            GraphNode(id="each", type="no_op"),
        ],
        edges=[
            Edge(source="t", target="ds"),
            Edge(source="ds", target="loop"),
            Edge(source="loop", source_output="item", target="each"),
        ],
    )
    result = await execute(graph, registry)
    # The loop ran over the dataset's rows, not the single envelope dict.
    assert result.nodes["each"].outputs["main"] == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]


# ---------------------------------------------------------------------------
# polars_transform
# ---------------------------------------------------------------------------


@pytest.fixture
def polars_ref(store_ctx):
    pl = pytest.importorskip("polars")  # noqa: F841
    return csv_parse(text="amount,region\n100,east\n200,west\n50,east\n", has_header=True)


def test_polars_transform_filter(store_ctx, polars_ref) -> None:
    pytest.importorskip("polars")
    ref = polars_transform(
        input=polars_ref,
        code="output = input.filter(pl.col('amount') > 60)",
    )
    assert is_dataset_ref(ref)
    rows = dataset_to_records(input=ref, max_rows=100)
    assert len(rows) == 2


def test_polars_transform_aggregate(store_ctx, polars_ref) -> None:
    pytest.importorskip("polars")
    ref = polars_transform(
        input=polars_ref,
        code=(
            "output = input.group_by('region').agg(pl.col('amount').sum().alias('total'))"
            ".sort('region')"
        ),
    )
    assert is_dataset_ref(ref)
    rows = dataset_to_records(input=ref, max_rows=100)
    assert len(rows) == 2


def test_polars_transform_accepts_dataframe(store_ctx, polars_ref) -> None:
    pytest.importorskip("polars")
    ref = polars_transform(
        input=polars_ref,
        code="output = input.collect()",
    )
    assert is_dataset_ref(ref)


def test_polars_transform_missing_output_raises(store_ctx, polars_ref) -> None:
    pytest.importorskip("polars")
    with pytest.raises(ValueError, match="output"):
        polars_transform(input=polars_ref, code="x = 1")


def test_polars_transform_non_polars_output_raises(store_ctx, polars_ref) -> None:
    pytest.importorskip("polars")
    with pytest.raises(ValueError, match="DataFrame or LazyFrame"):
        polars_transform(input=polars_ref, code="output = [1, 2, 3]")


def test_polars_transform_requires_dataset_ref(store_ctx) -> None:
    pytest.importorskip("polars")
    with pytest.raises(ValueError, match="DatasetRef"):
        polars_transform(input={"not": "a ref"}, code="output = input")


# ---------------------------------------------------------------------------
# map_dataset
# ---------------------------------------------------------------------------


async def test_map_dataset_calls_child_per_row(store_ctx) -> None:
    from noodle.context import workflow_caller

    calls: list[dict] = []

    async def caller(wf_id: str, payload: dict) -> dict:
        calls.append(payload)
        return {"processed": payload["row"]["x"] * 2}

    ref = csv_parse(text="x\n1\n2\n3\n", has_header=True)
    token = workflow_caller.set(caller)
    try:
        result = await map_dataset(
            input=ref,
            workflow_id="wf-1",
            on_error="fail",
            output_mode="records",
        )
    finally:
        workflow_caller.reset(token)

    assert len(calls) == 3
    assert sorted(r["processed"] for r in result["main"]) == [2, 4, 6]


async def test_map_dataset_dataset_output_is_ref(store_ctx) -> None:
    from noodle.context import workflow_caller

    async def caller(wf_id: str, payload: dict) -> dict:
        return {"val": payload["row"]["x"]}

    ref = csv_parse(text="x\n10\n20\n", has_header=True)
    token = workflow_caller.set(caller)
    try:
        result = await map_dataset(
            input=ref, workflow_id="wf-1", on_error="fail", output_mode="dataset"
        )
    finally:
        workflow_caller.reset(token)

    assert is_dataset_ref(result["main"])
    rows = dataset_to_records(input=result["main"], max_rows=10)
    assert sorted(r["val"] for r in rows) == [10, 20]


async def test_map_dataset_max_rows_raises_before_mapping(store_ctx) -> None:
    from noodle.context import workflow_caller

    calls: list = []

    async def caller(wf_id: str, payload: dict) -> dict:
        calls.append(payload)
        return {}

    ref = csv_parse(text="x\n1\n2\n3\n", has_header=True)
    token = workflow_caller.set(caller)
    try:
        with pytest.raises(ValueError, match="max_rows"):
            await map_dataset(input=ref, workflow_id="wf-1", max_rows=2)
    finally:
        workflow_caller.reset(token)

    assert calls == []  # no child calls should have happened


async def test_map_dataset_continue_on_error(store_ctx) -> None:
    from noodle.context import workflow_caller

    async def caller(wf_id: str, payload: dict) -> dict:
        if payload["row"]["x"] == 2:
            raise RuntimeError("fail on 2")
        return {"val": payload["row"]["x"]}

    ref = csv_parse(text="x\n1\n2\n3\n", has_header=True)
    token = workflow_caller.set(caller)
    try:
        result = await map_dataset(
            input=ref, workflow_id="wf-1", on_error="continue", output_mode="records"
        )
    finally:
        workflow_caller.reset(token)

    assert len(result["main"]) == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 1


def test_auto_expand_skips_code_and_dataset_ports(store_ctx) -> None:
    """Code nodes and dataset ports keep the raw DatasetRef; others expand."""
    from noodle.engine import _auto_expand_dataset_inputs
    from noodle.sdk import registry

    ref = csv_parse(text="x\n1\n2\n", has_header=True)

    # Generic per-item node: input is expanded into rows.
    loop_def = registry.get("loop_over_items")
    loop_kwargs = {"input": ref}
    _auto_expand_dataset_inputs(loop_def, loop_kwargs, "loop_over_items")
    assert loop_kwargs["input"] == [{"x": 1}, {"x": 2}]

    # Code node (dataset-passthrough): keeps the raw ref.
    code_def = registry.get("code")
    code_kwargs = {"input": ref}
    _auto_expand_dataset_inputs(code_def, code_kwargs, "code")
    assert is_dataset_ref(code_kwargs["input"])

    # Dataset-native node port: keeps the raw ref (kind != "any").
    filter_def = registry.get("dataset_filter")
    filter_kwargs = {"input": ref}
    _auto_expand_dataset_inputs(filter_def, filter_kwargs, "dataset_filter")
    assert is_dataset_ref(filter_kwargs["input"])

