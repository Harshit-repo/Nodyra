"""Regression tests for the MCP node-sweep bug fixes.

Covers:

- ``_parse_data`` (data_* nodes) no longer leaks ``JSONDecodeError`` for a
  blank ``data`` parameter and reports invalid JSON clearly.
- ``expectation_suite_run`` names the offending expectation when ``type`` is
  missing or unsupported instead of raising "Unknown expectation type: ".
- ``model_registry_query`` rejects empty/columnless input with a descriptive
  error instead of leaking a duckdb ``InvalidInputException``.
- The statistical nodes accept trigger-style ``{"rows": [...]}`` payloads,
  reject non-record input with a clear error, and never emit NaN results.
- The ``mcp_tool`` manifest exposes the node's real parameters
  (``connection_id``/``tool_name``/``arguments``) and hides the engine's
  reserved ``ctx``.
"""

from __future__ import annotations

import json

import pytest

from nodyra.artifacts import LocalArtifactStore
from nodyra.context import artifact_store, current_node_id
from nodyra.sdk import registry


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    artifact_token = artifact_store.set(store)
    node_token = current_node_id.set("test-node")
    yield store
    current_node_id.reset(node_token)
    artifact_store.reset(artifact_token)


# ---------------------------------------------------------------------------
# data_* nodes: _parse_data
# ---------------------------------------------------------------------------


def test_data_unique_values_blank_data_returns_empty() -> None:
    from nodyra_nodes.data_transform_nodes import data_unique_values

    result = data_unique_values(data="", column="a")
    assert result == {"values": [], "count": 0, "total_rows": 0}


def test_data_filter_rows_blank_data_returns_empty() -> None:
    pytest.importorskip("pandas")
    from nodyra_nodes.data_transform_nodes import data_filter_rows

    result = data_filter_rows(data="", column="a", operator="gt", value="1")
    assert result == {"rows": [], "count": 0, "total_before": 0}


def test_data_unique_values_invalid_json_raises_clearly() -> None:
    from nodyra_nodes.data_transform_nodes import data_unique_values

    with pytest.raises(ValueError, match="valid JSON"):
        data_unique_values(data="[{oops", column="a")


def test_data_unique_values_non_array_json_raises_clearly() -> None:
    from nodyra_nodes.data_transform_nodes import data_unique_values

    with pytest.raises(ValueError, match="JSON array"):
        data_unique_values(data='{"a": 1}', column="a")


# ---------------------------------------------------------------------------
# expectation_suite_run
# ---------------------------------------------------------------------------


def test_expectation_suite_run_names_missing_type() -> None:
    from nodyra_nodes.data_quality import expectation_suite_run

    with pytest.raises(ValueError) as excinfo:
        expectation_suite_run(
            input=[{"a": 1}],
            suite_json=json.dumps([{"column": "a"}]),
        )
    message = str(excinfo.value)
    assert "type" in message
    assert "not_null" in message


def test_expectation_suite_run_names_unknown_type(store_ctx) -> None:
    from nodyra_nodes.data_quality import expectation_suite_run

    with pytest.raises(ValueError) as excinfo:
        expectation_suite_run(
            input=[{"a": 1}],
            suite_json=json.dumps([{"type": "telepathy", "column": "a"}]),
        )
    message = str(excinfo.value)
    assert "telepathy" in message
    assert "regex" in message


# ---------------------------------------------------------------------------
# model_registry_query
# ---------------------------------------------------------------------------


def test_model_registry_query_rejects_empty_object_input() -> None:
    from nodyra_nodes.model_monitoring import model_registry_query

    with pytest.raises(ValueError, match="non-empty"):
        model_registry_query(input={})


def test_model_registry_query_rejects_columnless_rows(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_registry_query

    with pytest.raises(ValueError, match="non-empty"):
        model_registry_query(input=[{}, {}])


# ---------------------------------------------------------------------------
# statistical nodes: input handling and NaN guards
# ---------------------------------------------------------------------------


def test_statistical_test_accepts_rows_wrapped_dict() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    result = statistical_test(
        input={"rows": [{"v": 1}, {"v": 2}, {"v": 3}, {"v": 4}]},
        test="t_test_1samp",
        column="v",
    )
    assert result["n"] == 4
    assert result["statistic"] is not None
    assert result["p_value"] is not None


def test_statistical_test_raises_when_column_has_no_numeric_values() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    with pytest.raises(ValueError, match="no numeric values"):
        statistical_test(
            input={"rows": [{"other": 1}]},
            test="t_test_1samp",
            column="v",
        )


def test_statistical_test_raises_on_non_record_input() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    with pytest.raises(ValueError, match="Records To Dataset"):
        statistical_test(input="not records", test="t_test_1samp", column="v")


def test_statistical_test_rejects_non_finite_result() -> None:
    """All-identical samples yield NaN from scipy; the node must raise instead
    of emitting a NaN that poisons every JSON consumer."""
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import statistical_test

    with pytest.raises(ValueError, match="non-finite"):
        statistical_test(
            input=[
                {"g": "a", "v": 1.0},
                {"g": "a", "v": 1.0},
                {"g": "a", "v": 1.0},
                {"g": "b", "v": 1.0},
                {"g": "b", "v": 1.0},
                {"g": "b", "v": 1.0},
            ],
            test="t_test_ind",
            column="v",
            group_column="g",
        )


def test_regression_analysis_accepts_rows_wrapped_dict() -> None:
    pytest.importorskip("statsmodels")
    from nodyra_nodes.statistical_analysis import regression_analysis

    result = regression_analysis(
        input={"rows": [{"x": 1, "y": 2}, {"x": 2, "y": 4}, {"x": 3, "y": 6}, {"x": 4, "y": 8}]},
        target_column="y",
        feature_columns="x",
    )
    assert result["n_obs"] == 4


def test_correlation_analysis_rejects_non_record_input() -> None:
    pytest.importorskip("scipy")
    from nodyra_nodes.statistical_analysis import correlation_analysis

    with pytest.raises(ValueError, match="Records To Dataset"):
        correlation_analysis(input="garbage", columns="x,y")


# ---------------------------------------------------------------------------
# mcp_tool manifest
# ---------------------------------------------------------------------------


def test_mcp_tool_manifest_exposes_real_params_not_ctx() -> None:
    import nodyra_nodes.mcp_tool  # noqa: F401 - registers the node

    manifest = registry.get("mcp_tool").manifest
    names = [p.name for p in manifest.params]
    assert "ctx" not in names, "engine RuntimeContext leaked into the manifest"
    assert names == ["connection_id", "tool_name", "arguments"]

    specs = {p.name: p for p in manifest.params}
    assert specs["connection_id"].required is True
    assert specs["tool_name"].required is True
    assert specs["arguments"].required is False


def test_mcp_tool_manifest_keeps_input_port() -> None:
    import nodyra_nodes.mcp_tool  # noqa: F401

    manifest = registry.get("mcp_tool").manifest
    assert [p.name for p in manifest.inputs] == ["input"]
    assert [p.name for p in manifest.outputs] == ["main"]


# ---------------------------------------------------------------------------
# html_extract_records: columnless records must not crash the dataset builder
# ---------------------------------------------------------------------------


def test_html_extract_records_no_selectors_does_not_crash() -> None:
    """With no selectors configured the node produced one empty record and fed
    it to records_to_dataset, which raised a raw duckdb InvalidInputException."""
    pytest.importorskip("bs4")
    from nodyra_nodes.browser_automation import html_extract_records

    result = html_extract_records(input="<html><body>hi</body></html>")
    assert result["records_found"] == 1
    assert result["dataset"] is None


def test_html_extract_records_extracts_with_selectors(store_ctx) -> None:
    pytest.importorskip("bs4")
    from nodyra_nodes.browser_automation import html_extract_records

    result = html_extract_records(
        input='<div class="r"><span>1</span></div><div class="r"><span>2</span></div>',
        container_selector="div.r",
        selectors_json='{"value": "span"}',
    )
    assert result["records_found"] == 2
    assert result["records"][0]["value"] == "1"
    assert result["dataset"] is not None


# ---------------------------------------------------------------------------
# rows-wrapped trigger payloads on ML/LLM record inputs
# ---------------------------------------------------------------------------


def test_weak_label_accepts_rows_wrapped_dict(store_ctx) -> None:
    from nodyra_nodes.synthetic_data import weak_label

    result = weak_label(
        input={"rows": [{"text": "buy now"}, {"text": "hello"}]},
        mode="rules",
        text_column="text",
        labels="spam,ham",
        output_column="label",
        rule_patterns='{"spam": "buy"}',
    )
    assert result is not None


def test_llm_fine_tune_dataset_accepts_rows_wrapped_dict(store_ctx) -> None:
    from nodyra_nodes.llm_training import llm_fine_tune_dataset

    result = llm_fine_tune_dataset(
        input={"rows": [{"p": "q1", "c": "a1"}, {"p": "q2", "c": "a2"}]},
        format="openai_chat",
        prompt_column="p",
        completion_column="c",
        min_examples=2,
    )
    assert result is not None


def test_model_registry_query_accepts_rows_wrapped_dict(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_registry_query

    result = model_registry_query(
        input={
            "rows": [
                {
                    "model_id": "m1",
                    "status": "production",
                    "provider": "openai",
                    "base_model": "gpt-4",
                },
            ]
        }
    )
    assert result["main"]["total_registered"] == 1
    assert result["main"]["total_matched"] == 1
