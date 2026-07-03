"""Chart + report nodes — turn data into visual objects the UI renders.

These nodes produce small JSON "spec" envelopes (no image bytes):

* a **ChartRef** (``__nodyra_chart__``) describes a bar/line/area/scatter/pie
  chart. The web editor renders it as an interactive SVG with hover tooltips.
* a **ReportRef** (``__nodyra_report__``) bundles charts/tables/metrics/text
  into tiles laid out on a grid. The editor renders it as a drag-and-drop,
  resizable report canvas.

Both are plain dicts, so they flow through node outputs, pins, and caches like
any other value and can be reused across workflows.
"""

from __future__ import annotations

from typing import Any

from nodyra.datasets import is_dataset_ref
from nodyra.sdk import node
from nodyra_nodes.datasets import materialize_dataset

CHART_MARKER = "__nodyra_chart__"
CHART_VERSION = 1
REPORT_MARKER = "__nodyra_report__"
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
        return [r for r in value if isinstance(r, dict)][:_CHART_CAP]
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
        "title": {"group": "Options", "placeholder": "Monthly revenue"},
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
                    {
                        "x": idx,
                        "y": _to_number(h.get(watch)) or 0.0,
                        "label": str(h.get("timestamp", idx)),
                    }
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
        return {
            "id": tile_id,
            "type": "chart",
            "title": value.get("title") or "Chart",
            "data": value,
        }
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


# ---------------------------------------------------------------------------
# Chart -> image (SVG / PNG)
# ---------------------------------------------------------------------------

_CHART_PALETTE = [
    "#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#64748b",
]


def _esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _series_values(chart: dict[str, Any]) -> list[float]:
    out: list[float] = []
    for s in chart.get("series", []):
        for p in s.get("points", []):
            num = _to_number(p.get("y"))
            if num is not None:
                out.append(num)
    return out


def render_chart_svg(chart: dict[str, Any], width: int = 720, height: int = 420) -> str:
    """Render a ChartRef into a standalone SVG document string.

    Supports bar/line/area/scatter/pie. Dependency-free so it runs anywhere a
    node runs; the SVG is itself a valid image artifact.
    """
    ctype = chart.get("chart_type", "bar")
    title = chart.get("title", "")
    series = [s for s in chart.get("series", []) if isinstance(s, dict)]
    categories = chart.get("categories", []) or []
    pad_l, pad_r, pad_t, pad_b = 56, 24, 48 if title else 24, 56
    plot_w = max(10, width - pad_l - pad_r)
    plot_h = max(10, height - pad_t - pad_b)

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Inter, system-ui, sans-serif">'
    )
    parts.append(f'<rect width="{width}" height="{height}" fill="#ffffff"/>')
    if title:
        parts.append(
            f'<text x="{width / 2}" y="26" text-anchor="middle" '
            f'font-size="16" font-weight="600" fill="#0f172a">{_esc(title)}</text>'
        )

    if ctype == "pie":
        parts.append(_render_pie(series, width, height, pad_t))
        parts.append("</svg>")
        return "".join(parts)

    values = _series_values(chart)
    vmax = max(values) if values else 1.0
    vmin = min(values + [0.0]) if values else 0.0
    if vmax == vmin:
        vmax = vmin + 1.0
    span = vmax - vmin

    def y_of(v: float) -> float:
        return pad_t + plot_h - ((v - vmin) / span) * plot_h

    # Axes + zero baseline.
    parts.append(
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" '
        f'stroke="#cbd5e1" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" '
        f'y2="{pad_t + plot_h}" stroke="#cbd5e1" stroke-width="1"/>'
    )
    # Y gridlines + labels.
    for i in range(5):
        gv = vmin + span * (i / 4)
        gy = y_of(gv)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + plot_w}" y2="{gy:.1f}" '
            f'stroke="#eef2f7" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#64748b">{_fmt_num(gv)}</text>'
        )

    n = max(1, len(categories))
    step = plot_w / n

    if ctype == "bar":
        group = len(series) or 1
        bar_w = max(2.0, (step * 0.7) / group)
        for si, s in enumerate(series):
            color = _CHART_PALETTE[si % len(_CHART_PALETTE)]
            for ci in range(n):
                pts = s.get("points", [])
                val = _to_number(pts[ci].get("y")) if ci < len(pts) else None
                if val is None:
                    continue
                x = pad_l + ci * step + (step * 0.15) + si * bar_w
                y = y_of(val)
                bh = (pad_t + plot_h) - y
                parts.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                    f'height="{max(0, bh):.1f}" fill="{color}" rx="2"/>'
                )
    else:  # line / area / scatter
        for si, s in enumerate(series):
            color = _CHART_PALETTE[si % len(_CHART_PALETTE)]
            coords: list[tuple[float, float]] = []
            for ci, p in enumerate(s.get("points", [])):
                val = _to_number(p.get("y"))
                if val is None:
                    continue
                x = pad_l + (ci + 0.5) * step
                coords.append((x, y_of(val)))
            if not coords:
                continue
            if ctype == "area":
                base = pad_t + plot_h
                d = f'M {coords[0][0]:.1f} {base:.1f} '
                d += " ".join(f'L {x:.1f} {y:.1f}' for x, y in coords)
                d += f' L {coords[-1][0]:.1f} {base:.1f} Z'
                parts.append(f'<path d="{d}" fill="{color}" fill-opacity="0.18"/>')
            if ctype in ("line", "area"):
                d = "M " + " L ".join(f'{x:.1f} {y:.1f}' for x, y in coords)
                parts.append(
                    f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>'
                )
            radius = 4 if ctype == "scatter" else 2.5
            for x, y in coords:
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{color}"/>'
                )

    # X labels (thinned to avoid overlap).
    label_every = max(1, n // 12)
    for ci in range(n):
        if ci % label_every:
            continue
        cx = pad_l + (ci + 0.5) * step
        parts.append(
            f'<text x="{cx:.1f}" y="{pad_t + plot_h + 18}" text-anchor="middle" '
            f'font-size="11" fill="#64748b">{_esc(categories[ci])[:14]}</text>'
        )

    # Legend.
    if len(series) > 1:
        lx = pad_l
        ly = pad_t + plot_h + 38
        for si, s in enumerate(series):
            color = _CHART_PALETTE[si % len(_CHART_PALETTE)]
            label = _esc(s.get("name", f"series {si + 1}"))
            parts.append(
                f'<rect x="{lx}" y="{ly - 9}" width="10" height="10" fill="{color}" rx="2"/>'
            )
            parts.append(
                f'<text x="{lx + 15}" y="{ly}" font-size="11" fill="#334155">{label}</text>'
            )
            lx += 18 + len(s.get("name", "")) * 7 + 16

    parts.append("</svg>")
    return "".join(parts)


def _render_pie(series: list[dict[str, Any]], width: int, height: int, pad_t: int) -> str:
    import math

    points = series[0].get("points", []) if series else []
    total = sum(max(0.0, _to_number(p.get("y")) or 0.0) for p in points)
    cx, cy = width / 2, pad_t + (height - pad_t) / 2
    r = min(width, height - pad_t) / 2 - 24
    if total <= 0:
        return f'<text x="{cx}" y="{cy}" text-anchor="middle" fill="#94a3b8">No data</text>'
    parts: list[str] = []
    angle = -math.pi / 2
    for i, p in enumerate(points):
        val = max(0.0, _to_number(p.get("y")) or 0.0)
        if val <= 0:
            continue
        frac = val / total
        end = angle + frac * 2 * math.pi
        x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
        large = 1 if frac > 0.5 else 0
        color = _CHART_PALETTE[i % len(_CHART_PALETTE)]
        parts.append(
            f'<path d="M {cx:.1f} {cy:.1f} L {x1:.1f} {y1:.1f} '
            f'A {r:.1f} {r:.1f} 0 {large} 1 {x2:.1f} {y2:.1f} Z" '
            f'fill="{color}"/>'
        )
        mid = angle + frac * math.pi
        lx, ly = cx + (r + 14) * math.cos(mid), cy + (r + 14) * math.sin(mid)
        anchor = "start" if math.cos(mid) >= 0 else "end"
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="11" fill="#334155">{_esc(p.get("label", ""))[:16]} '
            f'({frac * 100:.0f}%)</text>'
        )
        angle = end
    return "".join(parts)


def _fmt_num(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}"


def _svg_to_png(svg: str) -> bytes:
    """Rasterise an SVG to PNG bytes if an optional renderer is installed."""
    try:
        import cairosvg  # type: ignore
    except Exception:
        pass
    else:
        return cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    try:

        import matplotlib  # type: ignore

        matplotlib.use("Agg")
    except Exception as exc:  # pragma: no cover - depends on env
        raise ValueError(
            "PNG export needs an optional renderer. Install 'cairosvg' "
            "(pip install cairosvg) for best results, or use format='svg' "
            "which works everywhere."
        ) from exc
    raise ValueError(
        "PNG export needs 'cairosvg' (pip install cairosvg). The 'svg' format "
        "works without extra dependencies and renders as an image everywhere."
    )


@node(
    name="Chart To Image",
    requirements=["cairosvg"],
    id="chart_to_image",
    category="Visualize",
    icon="image",
    input_kinds={"input": "any"},
    params={
        "format": {
            "description": "Image format. SVG works everywhere with no extra "
            "dependencies; PNG needs the optional 'cairosvg' package.",
            "choices": ["svg", "png"],
        },
        "width": {"group": "Options", "description": "Image width in pixels."},
        "height": {"group": "Options", "description": "Image height in pixels."},
        "name": {"group": "Options", "placeholder": "chart.svg"},
    },
)
def chart_to_image(
    input: Any = None,
    format: str = "svg",
    width: int = 720,
    height: int = 420,
    name: str = "",
) -> dict[str, Any]:
    """Render a chart (from the Chart / Metrics Chart node) into an image file.

    Produces a downloadable image **artifact** you can preview inline, attach
    to emails, embed in PDFs, or upload elsewhere. SVG is the default (crisp,
    dependency-free); choose PNG when a raster image is required.
    """
    from nodyra import artifacts

    if not is_chart_ref(input):
        raise ValueError(
            "Chart To Image expects a chart. Connect the output of a Chart or "
            "Metrics Chart node."
        )
    fmt = (format or "svg").lower()
    try:
        w = max(120, min(int(width or 720), 4000))
        h = max(120, min(int(height or 420), 4000))
    except (TypeError, ValueError):
        w, h = 720, 420
    svg = render_chart_svg(input, width=w, height=h)
    title = input.get("title") or "chart"
    if fmt == "png":
        png = _svg_to_png(svg)
        fname = _ensure_ext(name or title, "png")
        return artifacts.write_bytes(
            png, name=fname, content_type="image/png", kind="image"
        )
    fname = _ensure_ext(name or title, "svg")
    return artifacts.write_bytes(
        svg.encode("utf-8"),
        name=fname,
        content_type="image/svg+xml",
        kind="image",
    )


def _ensure_ext(name: str, ext: str) -> str:
    base = (name or "chart").strip() or "chart"
    if base.lower().endswith(f".{ext}"):
        return base
    base = base.rsplit(".", 1)[0] if "." in base else base
    return f"{base}.{ext}"

