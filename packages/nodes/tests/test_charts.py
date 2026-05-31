"""Tests for the chart + report visualization nodes."""

from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id
from noodle_nodes.charts import (
    build_report,
    chart,
    is_chart_ref,
    is_report_ref,
    metrics_chart,
)
from noodle_nodes.datasets import records_to_dataset


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="charts-test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("charts-test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)


def _sales():
    return records_to_dataset(
        [
            {"month": "Jan", "revenue": 100, "cost": 60},
            {"month": "Feb", "revenue": 140, "cost": 70},
            {"month": "Mar", "revenue": 120, "cost": 65},
        ]
    )


def test_chart_bar_from_dataset(store_ctx) -> None:
    spec = chart(input=_sales(), chart_type="bar", x="month", y="revenue", title="Revenue")
    assert is_chart_ref(spec)
    assert spec["chart_type"] == "bar"
    assert spec["title"] == "Revenue"
    assert spec["x_label"] == "month"
    assert len(spec["series"]) == 1
    pts = spec["series"][0]["points"]
    assert len(pts) == 3
    assert pts[0]["y"] == 100.0
    assert pts[0]["label"] == "Jan"


def test_chart_multi_series_auto_numeric(store_ctx) -> None:
    spec = chart(input=_sales(), chart_type="line", x="month")
    names = {s["name"] for s in spec["series"]}
    assert names == {"revenue", "cost"}


def test_chart_pie_single_series(store_ctx) -> None:
    spec = chart(input=_sales(), chart_type="pie", x="month", y="revenue")
    assert spec["chart_type"] == "pie"
    assert len(spec["series"]) == 1
    pt = spec["series"][0]["points"][0]
    assert pt["label"] == "Jan"
    assert pt["y"] == 100.0


def test_chart_requires_numeric_column(store_ctx) -> None:
    ds = records_to_dataset([{"a": "x", "b": "y"}, {"a": "p", "b": "q"}])
    with pytest.raises(ValueError):
        chart(input=ds, x="a")


def test_metrics_chart_feature_importances(store_ctx) -> None:
    metrics = {
        "feature_importances": [
            {"feature": "x1", "importance": 0.7},
            {"feature": "x2", "importance": 0.3},
        ]
    }
    spec = metrics_chart(input=metrics, source="feature_importances")
    assert is_chart_ref(spec)
    assert spec["chart_type"] == "bar"
    assert [p["y"] for p in spec["series"][0]["points"]] == [0.7, 0.3]


def test_metrics_chart_history_line(store_ctx) -> None:
    metrics = {
        "history": [
            {"timestamp": "t1", "accuracy": 0.9},
            {"timestamp": "t2", "accuracy": 0.85},
        ]
    }
    spec = metrics_chart(input=metrics, source="history")
    assert spec["chart_type"] == "line"
    assert spec["series"][0]["name"] == "accuracy"
    assert len(spec["series"][0]["points"]) == 2


def test_metrics_chart_no_data_errors(store_ctx) -> None:
    with pytest.raises(ValueError):
        metrics_chart(input={"task": "classification"}, source="auto")


def test_build_report_mixes_tiles(store_ctx) -> None:
    spec = chart(input=_sales(), chart_type="bar", x="month", y="revenue")
    report = build_report(
        title="Sales report",
        tile1=spec,
        tile2=_sales(),
        tile3={"accuracy": 0.91, "n_rows": 3},
        tile4="A short note.",
    )
    assert is_report_ref(report)
    assert report["title"] == "Sales report"
    types = [t["type"] for t in report["tiles"]]
    assert types == ["chart", "table", "metric", "text"]
    # Every tile carries a grid layout.
    for tile in report["tiles"]:
        assert {"x", "y", "w", "h"} <= set(tile["layout"])
    table_tile = report["tiles"][1]
    assert "month" in table_tile["data"]["columns"]
    assert len(table_tile["data"]["rows"]) == 3


def test_build_report_skips_empty_inputs(store_ctx) -> None:
    report = build_report(title="One tile", tile1="hello")
    assert len(report["tiles"]) == 1
    assert report["columns"] == 12
