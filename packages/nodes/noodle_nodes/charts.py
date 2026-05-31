"""Chart + report nodes — turn data into visual objects the UI renders.

These nodes produce small JSON "spec" envelopes (no image bytes):

* a **ChartRef** (``__noodle_chart__``) describes a bar/line/area/scatter/pie
  chart. The web editor renders it as an interactive SVG with hover tooltips.
* a **ReportRef** (``__noodle_report__``) bundles charts/tables/metrics/text
  into tiles laid out on a grid. The editor renders it as a drag-and-drop,
  resizable report canvas.

Both are plain dicts, so they flow through node outputs, pins, and caches like
any other value and can be reused across workflows.
"""

from __future__ import annotations

from typing import Any

from noodle.datasets import is_dataset_ref
from noodle.sdk import node
from noodle_nodes.datasets import materialize_dataset

CHART_MARKER = "__noodle_chart__"
CHART_VERSION = 1
REPORT_MARKER = "__noodle_report__"
REPORT_VERSION = 1

_CHART_TYPES = ["bar", "line", "area", "scatter", "pie"]
_CHART_CAP = 2000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_chart_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(CHART_MARKER) is True
        and value.get("version") == CHART_VERSION
    )


def is_report_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(REPORT_MARKER) is True
        and value.get("version") == REPORT_VERSION
    )


def _columns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [c.strip() for c in value.split(",") if c.strip()]
    if isinstance(value, (list, tuple)):
        return [str(c).strip() for c in value if str(c).strip()]
    return []


def _as_records(value: Any) -> list[dict[str, Any]]:
    """Coerce a dataset ref or list-like into a bounded list of dict rows."""
    if is_dataset_ref(value):
        return materialize_dataset(value, cap=_CHART_CAP, allow_truncate=True)
    if isinstance(value, dict):
        for key in ("records", "rows", "items", "data", "results"):
            inner = value.get(key)
            if isinstance(inner, list):
                value = inner
                break
        else:
            return [value]
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    return []


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _numeric_columns(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    cols = list(rows[0].keys())
    numeric = []
    for col in cols:
        values = [r.get(col) for r in rows[:50]]
        if any(_to_number(v) is not None for v in values):
            numeric.append(col)
    return numeric


def make_chart(
    *,
    chart_type: str,
    title: str,
    categories: list[Any],
    series: list[dict[str, Any]],
    x_label: str = "",
    y_label: str = "",
) -> dict[str, Any]:
    return {
        CHART_MARKER: True,
        "version": CHART_VERSION,
        "chart_type": chart_type if chart_type in _CHART_TYPES else "bar",
        "title": title or "",
        "x_label": x_label or "",
        "y_label": y_label or "",
        "categories": categories,
        "series": series,
    }


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@node(
    name="Chart",
    id="chart",
    category="Visualize",
    icon="bar-chart",
    input_kinds={"input": "dataset"},
    params={
        "chart_type": {"choices": _CHART_TYPES},
        "x": {
            "description": "Column for the x-axis / category labels.",
            "placeholder": "month",
        },
        "y": {
            "description": "Comma-separated numeric column(s) to plot. Blank = "
            "all numeric columns except x.",
            "placeholder": "revenue, cost",
        },
        "title": {"placeholder": "Monthly revenue"},
    },
)
def chart(
    input: Any = None,
    chart_type: str = "bar",
    x: str = "",
    y: str = "",
    title: str = "",
) -> dict[str, Any]:
    """Build an interactive chart spec from a dataset (or inline rows).

    The result is a ChartRef the editor renders as an SVG chart with tooltips,
    and which can be fed into the Build Report node.
    """
    rows = _as_records(input)
    if not rows:
        return make_chart(chart_type=chart_type, title=title, categories=[], series=[])

    all_cols = list(rows[0].keys())
    x_col = x.strip() if x and x.strip() in all_cols else (all_cols[0] if all_cols else "")
    y_cols = [c for c in _columns(y) if c in all_cols]
    if not y_cols:
        y_cols = [c for c in _numeric_columns(rows) if c != x_col]
    if not y_cols:
        raise ValueError(
            "Chart needs at least one numeric column to plot. Set 'y' to a "
            "numeric column."
        )

    categories = [row.get(x_col) for row in rows] if x_col else list(range(len(rows)))

    if chart_type == "pie":
        col = y_cols[0]
        points = [
            {"label": _label(cat), "y": _to_number(row.get(col)) or 0.0}
            for cat, row in zip(categories, rows, strict=False)
        ]
        series = [{"name": col, "points": points}]
        return make_chart(
            chart_type="pie", title=title or col, categories=categories,
            series=series, x_label=x_col, y_label=col,
        )

    series = []
    for col in y_cols:
        points = []
        for cat, row in zip(categories, rows, strict=False):
            num = _to_number(row.get(col))
            if num is None:
                continue
            points.append({"x": _xvalue(cat), "y": num, "label": _label(cat)})
        series.append({"name": col, "points": points})

    return make_chart(
        chart_type=chart_type if chart_type in _CHART_TYPES else "bar",
        title=title or ", ".join(y_cols),
        categories=categories,
        series=series,
        x_label=x_col,
        y_label=y_cols[0] if len(y_cols) == 1 else "",
    )


def _xvalue(cat: Any) -> Any:
    num = _to_number(cat)
    return num if num is not None else _label(cat)


def _label(cat: Any) -> str:
    if cat is None:
        return ""
    return str(cat)


@node(
    name="Metrics Chart",
    id="metrics_chart",
    category="Visualize",
    icon="bar-chart",
    params={
        "source": {
            "description": "Which part of an ML metrics object to chart.",
            "choices": [
                "auto",
                "feature_importances",
                "coefficients",
                "confusion_matrix",
                "history",
            ],
        },
        "title": {"placeholder": "Feature importances"},
    },
)
def metrics_chart(input: Any = None, source: str = "auto", title: str = "") -> dict[str, Any]:
    """Turn an ML metrics dict into a chart.

    Understands ``feature_importances``, ``coefficients``, ``confusion_matrix``
    and a monitor ``history`` list. With ``auto`` it picks the first available.
    """
    metrics = input if isinstance(input, dict) else {}
    order = (
        [source]
        if source != "auto"
        else ["feature_importances", "coefficients", "confusion_matrix", "history"]
    )
    for key in order:
        if key == "feature_importances" and isinstance(metrics.get(key), list):
            items = metrics[key]
            cats = [str(i.get("feature", "")) for i in items]
            points = [
                {"x": str(i.get("feature", "")), "y": _to_number(i.get("importance")) or 0.0,
                 "label": str(i.get("feature", ""))}
                for i in items
            ]
            return make_chart(chart_type="bar", title=title or "Feature importances",
                              categories=cats, series=[{"name": "importance", "points": points}],
                              x_label="feature", y_label="importance")
        if key == "coefficients" and isinstance(metrics.get(key), list):
            items = metrics[key]
            cats = [str(i.get("feature", "")) for i in items]
            points = [
                {"x": str(i.get("feature", "")), "y": _to_number(i.get("coefficient")) or 0.0,
                 "label": str(i.get("feature", ""))}
                for i in items
            ]
            return make_chart(chart_type="bar", title=title or "Coefficients",
                              categories=cats, series=[{"name": "coefficient", "points": points}],
                              x_label="feature", y_label="coefficient")
        if key == "history" and isinstance(metrics.get(key), list):
            items = [h for h in metrics[key] if isinstance(h, dict)]
            watch = None
            for candidate in ("accuracy", "f1", "r2", "rmse"):
                if items and candidate in items[0]:
                    watch = candidate
                    break
            if watch:
                points = [
                    {"x": idx, "y": _to_number(h.get(watch)) or 0.0, "label": str(h.get("timestamp", idx))}
                    for idx, h in enumerate(items)
                ]
                return make_chart(chart_type="line", title=title or f"{watch} over time",
                                  categories=[h.get("timestamp", i) for i, h in enumerate(items)],
                                  series=[{"name": watch, "points": points}],
                                  x_label="run", y_label=watch)
    raise ValueError(
        "Metrics Chart could not find chartable data. Connect the 'metrics' "
        "output of a training/monitor node, or pick a specific source."
    )


_TILE_PORTS = ["tile1", "tile2", "tile3", "tile4", "tile5", "tile6"]


@node(
    name="Build Report",
    id="build_report",
    category="Visualize",
    icon="layout",
    inputs=_TILE_PORTS,
    params={
        "title": {"placeholder": "Model report"},
        "columns": {"description": "Grid columns the canvas snaps tiles to."},
    },
)
def build_report(
    title: str = "",
    columns: int = 12,
    **tiles: Any,
) -> dict[str, Any]:
    """Assemble charts, tables, metrics, and text into a report canvas.

    Wire charts (from Chart), datasets/tables, metrics dicts, or plain text
    into the tile inputs. The editor renders the result as a drag-and-drop,
    resizable report you can rearrange — and reuse in other workflows.
    """
    grid_cols = max(1, min(int(columns or 12), 24))
    tile_w = max(1, grid_cols // 2)
    tile_h = 5
    out_tiles: list[dict[str, Any]] = []
    cursor_x = 0
    cursor_y = 0
    index = 0
    for port in _TILE_PORTS:
        value = tiles.get(port)
        if value is None:
            continue
        tile = _make_tile(value, index)
        if tile is None:
            continue
        if cursor_x + tile_w > grid_cols:
            cursor_x = 0
            cursor_y += tile_h
        tile["layout"] = {"x": cursor_x, "y": cursor_y, "w": tile_w, "h": tile_h}
        cursor_x += tile_w
        out_tiles.append(tile)
        index += 1

    return {
        REPORT_MARKER: True,
        "version": REPORT_VERSION,
        "title": title or "Report",
        "columns": grid_cols,
        "tiles": out_tiles,
    }


def _make_tile(value: Any, index: int) -> dict[str, Any] | None:
    tile_id = f"tile-{index + 1}"
    if is_chart_ref(value):
        return {"id": tile_id, "type": "chart", "title": value.get("title") or "Chart", "data": value}
    if is_report_ref(value):
        # Flatten a nested report's tiles is overkill; embed its title as text.
        return {"id": tile_id, "type": "text", "title": value.get("title") or "Report", "data": ""}
    if is_dataset_ref(value):
        rows = materialize_dataset(value, cap=200, allow_truncate=True)
        return _table_tile(tile_id, rows)
    if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
        return _table_tile(tile_id, value[:200])
    if isinstance(value, dict):
        # A metrics-style flat dict → key/value tile.
        return {
            "id": tile_id,
            "type": "metric",
            "title": "Metrics",
            "data": {k: v for k, v in value.items() if not isinstance(v, (list, dict))},
        }
    if isinstance(value, str):
        return {"id": tile_id, "type": "text", "title": "", "data": value}
    return {"id": tile_id, "type": "text", "title": "", "data": str(value)}


def _table_tile(tile_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return {
        "id": tile_id,
        "type": "table",
        "title": "Table",
        "data": {"columns": columns, "rows": rows},
    }
