"""Python Native Package Nodes — Tasks 30-39 (Workstream 6).

Covers: Pandas Transform, NumPy Array Ops, Matplotlib Chart,
Pydantic Validate, OpenCV Process, SciPy Stats, spaCy NLP,
NetworkX Graph Ops, BeautifulSoup Scraper, SymPy Math.

All heavy imports are lazy; base node registration has no import overhead.
"""

from __future__ import annotations

import ast
import io
import json
import re
from typing import Any

from noodle.artifacts import is_artifact_ref
from noodle.artifacts import read_bytes as read_artifact_bytes
from noodle.artifacts import write_bytes
from noodle.sdk import node
from noodle_nodes.http_security import assert_public_http_url

DATA_CATEGORY = "Data"
AI_CATEGORY = "AI"
IMAGE_CATEGORY = "Image"
CHARTS_CATEGORY = "Charts"
API_CATEGORY = "API"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _to_list_of_dicts(data: Any) -> list[dict[str, Any]]:
    """Normalize data to a list of dicts."""
    if isinstance(data, list):
        return [d if isinstance(d, dict) else {"value": d} for d in data]
    if isinstance(data, dict):
        if all(isinstance(v, list) for v in data.values()):
            # columnar → row format
            keys = list(data.keys())
            length = max(len(v) for v in data.values())
            return [{k: data[k][i] if i < len(data[k]) else None for k in keys} for i in range(length)]
        return [data]
    raise ValueError(f"Expected list or dict data, got {type(data).__name__}")


def _coerce_value(val_str: str) -> Any:
    """Auto-detect type for a string filter value from UI."""
    if val_str.lower() == "true":
        return True
    if val_str.lower() == "false":
        return False
    if val_str.lower() in ("null", "none", ""):
        return None
    try:
        return int(val_str)
    except ValueError:
        pass
    try:
        return float(val_str)
    except ValueError:
        pass
    return val_str


def _resolve_image_bytes(image: Any) -> bytes:
    import base64 as _b64  # noqa: PLC0415
    if isinstance(image, bytes):
        return image
    if is_artifact_ref(image):
        return read_artifact_bytes(image)
    if isinstance(image, str):
        try:
            return _b64.b64decode(image)
        except Exception as exc:
            raise ValueError(f"opencv_process: invalid image input") from exc
    raise ValueError(f"opencv_process: image must be bytes, base64 string, or artifact ref")


# ---------------------------------------------------------------------------
# Task 30 — Pandas Transform
# ---------------------------------------------------------------------------

PANDAS_OPS = [
    "filter", "select_columns", "group_by", "sort", "merge",
    "pivot", "melt", "fill_na", "drop_duplicates", "apply",
]
FILTER_OPS = ["==", "!=", ">", "<", ">=", "<=", "contains", "is_null", "not_null", "between", "in"]


@node(
    name="Pandas Transform",
    id="pandas_transform",
    category=DATA_CATEGORY,
    role="executable",
    icon="table",
    requirements=["pandas>=2.0"],
    inputs=["main"],
    outputs=["main"],
    param_groups={
        "Filter": ["filter_column", "filter_operator", "filter_value"],
        "Group/Sort": ["columns", "aggregations", "sort_ascending"],
        "Pivot": ["pivot_index", "pivot_columns", "pivot_values"],
        "Output": ["fill_value", "output_as"],
    },
    params={
        "operation": {"choices": PANDAS_OPS, "description": "DataFrame operation to perform."},
        "filter_column": {"description": "[filter] Column to filter on.", "group": "Filter"},
        "filter_operator": {"choices": FILTER_OPS, "description": "[filter] Comparison operator.", "group": "Filter"},
        "filter_value": {"description": "[filter] Value to compare against.", "group": "Filter"},
        "columns": {"placeholder": "col1, col2", "description": "[select/group_by/sort] Column(s).", "group": "Group/Sort"},
        "aggregations": {"type": "key_value", "description": "[group_by] Agg specs: {col: 'sum'}.", "group": "Group/Sort"},
        "sort_ascending": {"description": "[sort] Sort ascending if true.", "group": "Group/Sort"},
        "pivot_index": {"description": "[pivot] Index column.", "group": "Pivot"},
        "pivot_columns": {"description": "[pivot] Columns field.", "group": "Pivot"},
        "pivot_values": {"description": "[pivot] Values field.", "group": "Pivot"},
        "fill_value": {"description": "[fill_na] Fill NA with this value.", "group": "Output"},
        "output_as": {"choices": ["json", "dataset"], "description": "Output format.", "group": "Output"},
    },
)
def pandas_transform(
    input: Any = None,
    operation: str = "filter",
    filter_column: str = "",
    filter_operator: str = "==",
    filter_value: str = "",
    columns: str = "",
    aggregations: dict | None = None,
    sort_ascending: bool = True,
    pivot_index: str = "",
    pivot_columns: str = "",
    pivot_values: str = "",
    fill_value: str = "",
    output_as: str = "json",
) -> dict[str, Any]:
    """Perform Pandas DataFrame operations with visual configuration."""
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError:
        raise ImportError("pandas_transform requires pandas>=2.0. Install with: pip install pandas")

    rows = _to_list_of_dicts(input)
    df = pd.DataFrame(rows)

    col_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else []

    if operation == "filter":
        if not filter_column or filter_column not in df.columns:
            raise ValueError(f"pandas_transform filter: column '{filter_column}' not found")
        col = df[filter_column]
        op = filter_operator or "=="
        val = _coerce_value(filter_value)
        if op == "==":
            df = df[col == val]
        elif op == "!=":
            df = df[col != val]
        elif op == ">":
            df = df[col > val]
        elif op == "<":
            df = df[col < val]
        elif op == ">=":
            df = df[col >= val]
        elif op == "<=":
            df = df[col <= val]
        elif op == "contains":
            df = df[col.astype(str).str.contains(str(val), na=False)]
        elif op == "is_null":
            df = df[col.isna()]
        elif op == "not_null":
            df = df[col.notna()]
        elif op == "between":
            parts = str(filter_value).split(",")
            lo, hi = _coerce_value(parts[0].strip()), _coerce_value(parts[1].strip() if len(parts) > 1 else parts[0].strip())
            df = df[col.between(lo, hi)]
        elif op == "in":
            vals = [_coerce_value(v.strip()) for v in str(filter_value).split(",")]
            df = df[col.isin(vals)]

    elif operation == "select_columns":
        valid = [c for c in col_list if c in df.columns]
        df = df[valid]

    elif operation == "group_by":
        if not col_list:
            raise ValueError("pandas_transform group_by: columns is required")
        agg = aggregations or {c: "sum" for c in df.columns if c not in col_list}
        df = df.groupby(col_list).agg(agg).reset_index()

    elif operation == "sort":
        by = col_list if col_list else list(df.columns[:1])
        df = df.sort_values(by=by, ascending=bool(sort_ascending))

    elif operation == "fill_na":
        val = _coerce_value(fill_value) if fill_value else 0
        df = df.fillna(val)

    elif operation == "drop_duplicates":
        df = df.drop_duplicates(subset=col_list if col_list else None)

    elif operation == "pivot":
        try:
            df = df.pivot(index=pivot_index or None, columns=pivot_columns or None, values=pivot_values or None)
            df = df.reset_index()
            df.columns = [str(c) for c in df.columns]
        except ValueError as exc:
            raise ValueError(f"pandas_transform pivot failed: {exc}. Try using group_by with aggregation first.") from exc

    elif operation == "melt":
        id_cols = col_list
        value_cols = [c for c in df.columns if c not in id_cols]
        df = df.melt(id_vars=id_cols, value_vars=value_cols)

    result_rows = df.to_dict(orient="records")
    return {
        "data": result_rows,
        "rows": len(result_rows),
        "columns": list(df.columns),
        "operation": operation,
    }


# ---------------------------------------------------------------------------
# Task 31 — NumPy Array Ops
# ---------------------------------------------------------------------------

NUMPY_OPS = ["stats", "math", "reshape", "transpose", "concat", "dot_product", "clip", "normalize"]
NUMPY_MATH_OPS = ["add", "sub", "mul", "div"]


@node(
    name="NumPy Array Operations",
    id="numpy_array_ops",
    category=DATA_CATEGORY,
    role="executable",
    icon="hash",
    requirements=["numpy>=1.26"],
    inputs=["main"],
    outputs=["main"],
    params={
        "operation": {"choices": NUMPY_OPS, "description": "Array operation."},
        "math_op": {"choices": NUMPY_MATH_OPS, "description": "[math] Operation: add/sub/mul/div scalar."},
        "scalar": {"description": "[math/clip] Scalar value."},
        "axis": {"description": "Axis for operation. Leave empty for flattened."},
        "new_shape": {"placeholder": "3, 4", "description": "[reshape] New shape dimensions."},
        "stats_list": {"placeholder": "mean, std, min, max", "description": "[stats] Statistics to compute."},
        "percentile": {"description": "[stats] Percentile value (0-100)."},
        "clip_min": {"description": "[clip] Minimum clamp value."},
        "clip_max": {"description": "[clip] Maximum clamp value."},
    },
)
def numpy_array_ops(
    input: Any = None,
    operation: str = "stats",
    math_op: str = "add",
    scalar: float = 0.0,
    axis: int | None = None,
    new_shape: str = "",
    stats_list: str = "mean, std, min, max",
    percentile: float = 50.0,
    clip_min: float | None = None,
    clip_max: float | None = None,
) -> dict[str, Any]:
    """Perform NumPy array operations."""
    try:
        import numpy as np  # noqa: PLC0415
    except ImportError:
        raise ImportError("numpy_array_ops requires numpy>=1.26. Install with: pip install numpy")

    arr = np.array(input)
    ax = int(axis) if axis is not None else None

    if operation == "stats":
        requested = [s.strip() for s in stats_list.split(",") if s.strip()]
        result: dict[str, Any] = {}
        for stat in requested:
            if stat == "mean":
                result["mean"] = float(np.mean(arr, axis=ax)) if ax is not None else float(np.mean(arr))
            elif stat == "median":
                result["median"] = float(np.median(arr, axis=ax) if ax is not None else np.median(arr))
            elif stat == "std":
                result["std"] = float(np.std(arr, axis=ax) if ax is not None else np.std(arr))
            elif stat == "min":
                result["min"] = float(np.min(arr, axis=ax) if ax is not None else np.min(arr))
            elif stat == "max":
                result["max"] = float(np.max(arr, axis=ax) if ax is not None else np.max(arr))
            elif stat == "sum":
                result["sum"] = float(np.sum(arr, axis=ax) if ax is not None else np.sum(arr))
            elif stat == "percentile":
                result[f"p{int(percentile)}"] = float(np.percentile(arr, float(percentile)))
            elif stat == "shape":
                result["shape"] = list(arr.shape)
            elif stat == "count":
                result["count"] = int(arr.size)
        return {"stats": result, "shape": list(arr.shape), "dtype": str(arr.dtype)}

    elif operation == "math":
        s = float(scalar or 0)
        if math_op == "add":
            out = arr + s
        elif math_op == "sub":
            out = arr - s
        elif math_op == "mul":
            out = arr * s
        elif math_op == "div":
            if s == 0:
                raise ValueError("numpy_array_ops math div: scalar cannot be zero")
            out = arr / s
        else:
            out = arr
        return {"result": out.tolist(), "shape": list(out.shape), "operation": f"{math_op}({s})"}

    elif operation == "reshape":
        parts = [int(x.strip()) for x in new_shape.split(",") if x.strip()]
        out = arr.reshape(parts)
        return {"result": out.tolist(), "shape": list(out.shape)}

    elif operation == "transpose":
        out = arr.T
        return {"result": out.tolist(), "shape": list(out.shape)}

    elif operation == "concat":
        if not isinstance(input, list) or len(input) < 2:
            raise ValueError("numpy_array_ops concat: input must be a list of at least 2 arrays")
        arrays = [np.array(a) for a in input]
        out = np.concatenate(arrays, axis=ax or 0)
        return {"result": out.tolist(), "shape": list(out.shape)}

    elif operation == "dot_product":
        if not isinstance(input, list) or len(input) != 2:
            raise ValueError("numpy_array_ops dot_product: input must be [array_a, array_b]")
        a, b = np.array(input[0]), np.array(input[1])
        out = np.dot(a, b)
        return {"result": out.tolist() if hasattr(out, "tolist") else float(out), "shape": list(np.shape(out))}

    elif operation == "clip":
        lo = float(clip_min) if clip_min is not None else None
        hi = float(clip_max) if clip_max is not None else None
        out = np.clip(arr, lo, hi)
        return {"result": out.tolist(), "shape": list(out.shape)}

    elif operation == "normalize":
        mn, mx = arr.min(), arr.max()
        if mx == mn:
            out = np.zeros_like(arr, dtype=float)
        else:
            out = (arr - mn) / (mx - mn)
        return {"result": out.tolist(), "min": float(mn), "max": float(mx)}

    raise ValueError(f"numpy_array_ops: unknown operation '{operation}'")


# ---------------------------------------------------------------------------
# Task 32 — Matplotlib Chart Generator
# ---------------------------------------------------------------------------

CHART_TYPES = ["line", "bar", "scatter", "histogram", "boxplot", "heatmap", "pie"]
COLOR_SCHEMES = ["default", "viridis", "plasma", "dark", "pastel"]


@node(
    name="Matplotlib Chart",
    id="matplotlib_chart",
    category=CHARTS_CATEGORY,
    role="executable",
    icon="bar-chart-2",
    requirements=["matplotlib>=3.8"],
    inputs=["main"],
    outputs=["main"],
    param_groups={"Axes": ["title", "x_label", "y_label"], "Style": ["color_scheme", "figsize", "dpi", "output_format"]},
    params={
        "chart_type": {"choices": CHART_TYPES, "description": "Chart type to generate."},
        "x_column": {"description": "Column for X axis data."},
        "y_columns": {"placeholder": "col1, col2", "description": "Comma-separated Y axis columns."},
        "title": {"description": "Chart title.", "group": "Axes"},
        "x_label": {"description": "X axis label.", "group": "Axes"},
        "y_label": {"description": "Y axis label.", "group": "Axes"},
        "figsize": {"placeholder": "10,6", "description": "Figure size as width,height (inches).", "group": "Style"},
        "color_scheme": {"choices": COLOR_SCHEMES, "description": "Color palette.", "group": "Style"},
        "output_format": {"choices": ["png", "svg", "pdf"], "description": "Output image format.", "group": "Style"},
        "dpi": {"description": "Output resolution (DPI).", "group": "Style"},
    },
)
def matplotlib_chart(
    input: Any = None,
    chart_type: str = "line",
    x_column: str = "",
    y_columns: str = "",
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    figsize: str = "10,6",
    color_scheme: str = "default",
    output_format: str = "png",
    dpi: int = 150,
) -> dict[str, Any]:
    """Generate a chart using Matplotlib and return it as an artifact."""
    try:
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt  # noqa: PLC0415
        import matplotlib.cm as cm  # noqa: PLC0415
    except ImportError:
        raise ImportError("matplotlib_chart requires matplotlib>=3.8. Install with: pip install matplotlib")

    rows = _to_list_of_dicts(input) if input is not None else []

    # Parse figsize
    try:
        fw, fh = [float(x.strip()) for x in (figsize or "10,6").split(",")][:2]
    except (ValueError, TypeError):
        fw, fh = 10.0, 6.0

    fig, ax = plt.subplots(figsize=(fw, fh))

    # Color map
    cmap = None
    if color_scheme not in ("default", "pastel", "dark"):
        cmap = color_scheme

    y_cols = [c.strip() for c in y_columns.split(",") if c.strip()] if y_columns else []

    if not rows:
        ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center", fontsize=14, color="gray")
    elif chart_type == "line":
        x_vals = [r.get(x_column, i) for i, r in enumerate(rows)] if x_column else list(range(len(rows)))
        for y_col in y_cols or (list(rows[0].keys())[1:] if len(rows[0]) > 1 else []):
            y_vals = [r.get(y_col) for r in rows]
            ax.plot(x_vals, y_vals, label=y_col)
        ax.legend() if y_cols else None
    elif chart_type == "bar":
        x_vals = [r.get(x_column, i) for i, r in enumerate(rows)] if x_column else list(range(len(rows)))
        for y_col in y_cols or (list(rows[0].keys())[1:] if len(rows[0]) > 1 else []):
            y_vals = [r.get(y_col) for r in rows]
            ax.bar([str(x) for x in x_vals], y_vals, label=y_col)
        ax.legend() if y_cols else None
    elif chart_type == "scatter":
        if len(rows) > 10000:
            rows = rows[:10000]
        x_vals = [r.get(x_column, i) for i, r in enumerate(rows)] if x_column else list(range(len(rows)))
        for y_col in y_cols or (list(rows[0].keys())[1:] if len(rows[0]) > 1 else []):
            y_vals = [r.get(y_col) for r in rows]
            ax.scatter(x_vals, y_vals, label=y_col, rasterized=len(rows) > 1000, s=5)
        ax.legend() if y_cols else None
    elif chart_type == "histogram":
        for y_col in y_cols or list(rows[0].keys()):
            vals = [r.get(y_col) for r in rows if isinstance(r.get(y_col), (int, float))]
            if vals:
                ax.hist(vals, label=y_col, alpha=0.7)
        ax.legend() if y_cols else None
    elif chart_type == "boxplot":
        data_arrays = []
        labels = []
        for y_col in y_cols or list(rows[0].keys()):
            vals = [r.get(y_col) for r in rows if isinstance(r.get(y_col), (int, float))]
            if vals:
                data_arrays.append(vals)
                labels.append(y_col)
        if data_arrays:
            ax.boxplot(data_arrays, labels=labels)
    elif chart_type == "pie":
        col = y_cols[0] if y_cols else (x_column or list(rows[0].keys())[0])
        vals = [r.get(col) for r in rows]
        labs = [str(r.get(x_column, i)) for i, r in enumerate(rows)] if x_column else [str(i) for i in range(len(rows))]
        ax.pie(vals, labels=labs, autopct="%1.1f%%")
    elif chart_type == "heatmap":
        try:
            import numpy as np  # noqa: PLC0415
            numeric_cols = [k for k, v in rows[0].items() if isinstance(v, (int, float))]
            mat = [[r.get(c, 0) for c in numeric_cols] for r in rows]
            im = ax.imshow(np.array(mat, dtype=float), aspect="auto", cmap=cmap or "viridis")
            fig.colorbar(im, ax=ax)
            ax.set_xticks(range(len(numeric_cols)))
            ax.set_xticklabels(numeric_cols, rotation=45, ha="right")
        except Exception:
            ax.text(0.5, 0.5, "Heatmap requires numeric data", transform=ax.transAxes, ha="center")

    if title:
        ax.set_title(title)
    if x_label:
        ax.set_xlabel(x_label)
    if y_label:
        ax.set_ylabel(y_label)

    buf = io.BytesIO()
    fmt = output_format or "png"
    fig.savefig(buf, format=fmt, dpi=int(dpi or 150), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img_bytes = buf.read()

    content_type = {"png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf"}.get(fmt, "image/png")
    artifact = write_bytes(img_bytes, name=f"chart.{fmt}", content_type=content_type)
    return {"chart": artifact, "format": fmt, "chart_type": chart_type, "rows": len(rows)}


# ---------------------------------------------------------------------------
# Task 33 — Pydantic Schema Validation
# ---------------------------------------------------------------------------

_BLOCKED_IMPORTS = {"os", "subprocess", "socket", "sys", "shutil", "pathlib", "importlib",
                    "builtins", "ctypes", "eval", "exec", "open", "pty", "signal", "resource"}


def _validate_schema_ast(code: str) -> None:
    """Raise ValueError if the schema code uses any dangerous imports or builtins."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"pydantic_validate: schema syntax error: {exc}") from exc

    for node_ in ast.walk(tree):
        if isinstance(node_, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name.split(".")[0] for alias in node_.names]
                if isinstance(node_, ast.Import)
                else ([node_.module.split(".")[0]] if node_.module else [])
            )
            for name in names:
                if name in _BLOCKED_IMPORTS:
                    raise ValueError(
                        f"pydantic_validate: schema imports '{name}' which is not allowed. "
                        "Only pydantic and standard typing imports are permitted."
                    )
        if isinstance(node_, ast.Call):
            if isinstance(node_.func, ast.Name) and node_.func.id in ("eval", "exec", "__import__"):
                raise ValueError(f"pydantic_validate: schema uses '{node_.func.id}()' which is not allowed.")


def _build_model_from_schema(schema_str: str, BaseModel: type) -> type:
    """Build a Pydantic model from a schema string via AST — no exec."""
    import typing  # noqa: PLC0415
    from pydantic import create_model  # noqa: PLC0415

    _SAFE: dict[str, Any] = {
        "str": str, "int": int, "float": float, "bool": bool,
        "list": list, "dict": dict, "tuple": tuple, "set": set,
        "Any": typing.Any,
        "Optional": typing.Optional, "List": typing.List,
        "Dict": typing.Dict, "Union": typing.Union,
    }

    def _resolve(node: ast.expr) -> Any:
        if isinstance(node, ast.Name):
            if node.id not in _SAFE:
                raise ValueError(f"pydantic_validate: unsupported type '{node.id}'")
            return _SAFE[node.id]
        if isinstance(node, ast.Subscript):
            outer = node.value.id if isinstance(node.value, ast.Name) else ""
            sl = node.slice
            if outer == "Optional":
                return typing.Optional[_resolve(sl)]
            if outer == "List":
                return typing.List[_resolve(sl)]
            if outer == "Dict":
                if isinstance(sl, ast.Tuple) and len(sl.elts) == 2:
                    return typing.Dict[_resolve(sl.elts[0]), _resolve(sl.elts[1])]
                return dict
            if outer == "Union":
                if isinstance(sl, ast.Tuple):
                    return typing.Union[tuple(_resolve(e) for e in sl.elts)]  # type: ignore[return-value]
            raise ValueError(f"pydantic_validate: unsupported generic '{outer}'")
        raise ValueError(f"pydantic_validate: unsupported annotation {ast.dump(node)}")

    tree = ast.parse(schema_str)
    cls_node: ast.ClassDef | None = next(
        (s for s in tree.body if isinstance(s, ast.ClassDef)
         and any(isinstance(b, ast.Name) and b.id == "BaseModel" for b in s.bases)),
        None,
    )
    if cls_node is None:
        raise ValueError("pydantic_validate: no BaseModel subclass found in schema")

    fields: dict[str, Any] = {}
    for stmt in cls_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            ftype = _resolve(stmt.annotation)
            try:
                default = ast.literal_eval(stmt.value) if stmt.value is not None else ...
            except (ValueError, TypeError):
                default = ...
            fields[stmt.target.id] = (ftype, default)

    if not fields:
        raise ValueError("pydantic_validate: no fields found in schema")
    return create_model(cls_node.name, __base__=BaseModel, **fields)


@node(
    name="Pydantic Validate",
    id="pydantic_validate",
    category=DATA_CATEGORY,
    role="executable",
    icon="check-square",
    requirements=["pydantic>=2.0"],
    inputs=["main"],
    outputs=["valid", "invalid"],
    params={
        "schema": {
            "multiline": True,
            "placeholder": "class User(BaseModel):\n    name: str\n    age: int",
            "description": "Pydantic model class definition.",
        },
        "strict": {"description": "Strict mode (no type coercion)."},
        "on_error": {
            "choices": ["raise", "filter", "flag"],
            "description": "Error handling: raise / filter (remove invalid) / flag (add _valid bool).",
        },
    },
)
def pydantic_validate(
    input: Any = None,
    schema: str = "",
    strict: bool = False,
    on_error: str = "raise",
) -> dict[str, Any]:
    """Validate data against a Pydantic model schema."""
    try:
        import pydantic  # noqa: PLC0415
        from pydantic import BaseModel  # noqa: PLC0415
    except ImportError:
        raise ImportError("pydantic_validate requires pydantic>=2.0. Install with: pip install pydantic")

    if not schema:
        raise ValueError("pydantic_validate: schema is required")

    _validate_schema_ast(schema)

    try:
        model_cls = _build_model_from_schema(schema, BaseModel)
    except Exception as exc:
        raise ValueError(f"pydantic_validate: schema compilation failed: {exc}") from exc

    items = input if isinstance(input, list) else [input] if input is not None else []

    valid_items: list[Any] = []
    invalid_items: list[Any] = []

    for item in items:
        try:
            validated = model_cls.model_validate(item, strict=strict)
            valid_items.append(validated.model_dump())
        except pydantic.ValidationError as exc:
            if on_error == "raise":
                raise ValueError(f"pydantic_validate: validation failed: {exc}") from exc
            errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
            if on_error == "filter":
                invalid_items.append({"item": item, "errors": errors})
            else:  # flag
                item_dict = dict(item) if isinstance(item, dict) else {"value": item}
                item_dict["_valid"] = False
                item_dict["_errors"] = errors
                invalid_items.append(item_dict)

    return {
        "valid": valid_items,
        "invalid": invalid_items,
        "valid_count": len(valid_items),
        "invalid_count": len(invalid_items),
    }


# ---------------------------------------------------------------------------
# Task 34 — OpenCV Image Processing
# ---------------------------------------------------------------------------

OPENCV_OPS = [
    "detect_faces", "detect_edges", "blur", "sharpen", "threshold",
    "contours", "color_balance", "denoise", "grayscale",
]


@node(
    name="OpenCV Image Processing",
    id="opencv_process",
    category=IMAGE_CATEGORY,
    role="executable",
    icon="aperture",
    requirements=["opencv-python>=4.8"],
    inputs=["main"],
    outputs=["main"],
    param_groups={"Options": ["threshold_value", "blur_kernel", "cascade_file", "output_format"]},
    params={
        "operation": {"choices": OPENCV_OPS, "description": "Image processing operation."},
        "threshold_value": {"description": "[threshold] Threshold 0-255.", "group": "Options"},
        "blur_kernel": {"description": "[blur/denoise] Kernel size (odd number).", "group": "Options"},
        "cascade_file": {
            "placeholder": "haarcascade_frontalface_default",
            "description": "[detect_faces] OpenCV cascade classifier name.",
            "group": "Options",
        },
        "output_format": {"choices": ["png", "jpg"], "description": "Output image format.", "group": "Options"},
    },
)
def opencv_process(
    input: Any = None,
    operation: str = "detect_faces",
    threshold_value: int = 127,
    blur_kernel: int = 5,
    cascade_file: str = "haarcascade_frontalface_default",
    output_format: str = "png",
) -> dict[str, Any]:
    """Apply OpenCV image processing operations."""
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except ImportError:
        raise ImportError(
            "opencv_process requires opencv-python>=4.8. Install with: pip install opencv-python"
        )

    raw = _resolve_image_bytes(input)
    nparr = np.frombuffer(raw, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("opencv_process: could not decode image")

    metadata: dict[str, Any] = {}
    out_img = img

    if operation == "detect_faces":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        try:
            cascade_path = cv2.data.haarcascades + f"{cascade_file}.xml"
            cascade = cv2.CascadeClassifier(cascade_path)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
            metadata["face_count"] = len(faces)
            metadata["faces"] = [{"x": int(x), "y": int(y), "w": int(w), "h": int(h)} for x, y, w, h in faces]
            for x, y, w, h in faces:
                cv2.rectangle(img, (x, y), (x + w, y + h), (255, 0, 0), 2)
            out_img = img
        except Exception as exc:
            metadata["face_detection_error"] = str(exc)

    elif operation == "detect_edges":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        out_img = cv2.Canny(gray, 50, 150)
        out_img = cv2.cvtColor(out_img, cv2.COLOR_GRAY2BGR)

    elif operation == "blur":
        k = max(1, int(blur_kernel or 5))
        if k % 2 == 0:
            k += 1
        out_img = cv2.GaussianBlur(img, (k, k), 0)

    elif operation == "sharpen":
        import numpy as np  # noqa: PLC0415
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        out_img = cv2.filter2D(img, -1, kernel)

    elif operation == "threshold":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, int(threshold_value or 127), 255, cv2.THRESH_BINARY)
        out_img = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

    elif operation == "contours":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, int(threshold_value or 127), 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(img, contours, -1, (0, 255, 0), 2)
        metadata["contour_count"] = len(contours)
        out_img = img

    elif operation == "denoise":
        out_img = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)

    elif operation == "grayscale":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        out_img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    elif operation == "color_balance":
        out_img = cv2.convertScaleAbs(img, alpha=1.2, beta=10)

    fmt = output_format or "png"
    ext = ".jpg" if fmt == "jpg" else ".png"
    _, buf = cv2.imencode(ext, out_img)
    img_bytes = bytes(buf)
    ct = "image/jpeg" if fmt == "jpg" else "image/png"
    artifact = write_bytes(img_bytes, name=f"processed{ext}", content_type=ct)

    h, w = out_img.shape[:2]
    return {
        "image": artifact,
        "operation": operation,
        "width": w,
        "height": h,
        **metadata,
    }


# ---------------------------------------------------------------------------
# Task 35 — SciPy Statistical Functions
# ---------------------------------------------------------------------------

SCIPY_TESTS = [
    "ttest", "chi2", "anova", "mannwhitney", "ks",
    "pearsonr", "spearmanr", "zscore", "describe", "normaltest",
]


@node(
    name="SciPy Statistical Functions",
    id="scipy_stats",
    category=DATA_CATEGORY,
    role="executable",
    icon="activity",
    requirements=["scipy>=1.12"],
    inputs=["main"],
    outputs=["main"],
    params={
        "test": {"choices": SCIPY_TESTS, "description": "Statistical test or function."},
        "alpha": {"description": "Significance level (default 0.05)."},
        "samples_column": {"description": "[ttest/mannwhitney] Column name to split samples by."},
        "group_column": {"description": "[anova/chi2] Column with group labels."},
        "value_column": {"description": "[anova] Column with values."},
        "y_column": {"description": "[pearsonr/spearmanr] Second column for correlation."},
    },
)
def scipy_stats(
    input: Any = None,
    test: str = "ttest",
    alpha: float = 0.05,
    samples_column: str = "",
    group_column: str = "",
    value_column: str = "",
    y_column: str = "",
) -> dict[str, Any]:
    """Run SciPy statistical tests."""
    try:
        from scipy import stats as sp_stats  # noqa: PLC0415
    except ImportError:
        raise ImportError("scipy_stats requires scipy>=1.12. Install with: pip install scipy")

    alpha_val = float(alpha or 0.05)

    # Resolve input to numpy array or dataframe-like
    if isinstance(input, list):
        if all(isinstance(x, (int, float)) for x in input):
            sample = input
            rows = None
        else:
            rows = _to_list_of_dicts(input)
            sample = None
    elif isinstance(input, dict):
        rows = _to_list_of_dicts(input)
        sample = None
    else:
        sample = [float(input)] if input is not None else []
        rows = None

    def _col(colname: str) -> list:
        if not rows or not colname:
            return sample or []
        return [r.get(colname) for r in rows if r.get(colname) is not None]

    if test == "ttest":
        if samples_column and rows:
            groups = {}
            for r in rows:
                k = str(r.get(samples_column))
                groups.setdefault(k, []).append(r.get(value_column or list(r.keys())[-1]))
            keys = list(groups.keys())
            if len(keys) < 2:
                raise ValueError("scipy_stats ttest: need at least 2 groups")
            stat, p = sp_stats.ttest_ind(groups[keys[0]], groups[keys[1]])
        else:
            stat, p = sp_stats.ttest_1samp(sample or [], 0)
        return {"statistic": float(stat), "p_value": float(p), "significant": float(p) < alpha_val, "alpha": alpha_val}

    elif test == "chi2":
        if rows and group_column:
            from collections import Counter  # noqa: PLC0415
            observed = list(Counter(_col(group_column)).values())
        else:
            observed = sample or []
        stat, p = sp_stats.chisquare(observed)
        return {"statistic": float(stat), "p_value": float(p), "significant": float(p) < alpha_val}

    elif test == "anova":
        if rows and group_column:
            groups = {}
            vcol = value_column or [k for k in rows[0].keys() if k != group_column][0] if rows else ""
            for r in rows:
                k = str(r.get(group_column))
                groups.setdefault(k, []).append(r.get(vcol))
            stat, p = sp_stats.f_oneway(*[g for g in groups.values() if len(g) > 1])
        else:
            stat, p = sp_stats.f_oneway(sample or [])
        return {"statistic": float(stat), "p_value": float(p), "significant": float(p) < alpha_val}

    elif test == "mannwhitney":
        if rows and samples_column:
            groups = {}
            vcol = value_column or [k for k in rows[0].keys() if k != samples_column][0] if rows else ""
            for r in rows:
                k = str(r.get(samples_column))
                groups.setdefault(k, []).append(r.get(vcol))
            keys = list(groups.keys())
            stat, p = sp_stats.mannwhitneyu(groups[keys[0]], groups[keys[1]])
        else:
            raise ValueError("scipy_stats mannwhitney: samples_column is required")
        return {"statistic": float(stat), "p_value": float(p), "significant": float(p) < alpha_val}

    elif test == "ks":
        stat, p = sp_stats.kstest(sample or [], "norm")
        return {"statistic": float(stat), "p_value": float(p), "significant": float(p) < alpha_val}

    elif test == "pearsonr":
        x_vals = _col(samples_column) if samples_column else (sample or [])
        y_vals = _col(y_column) if y_column else []
        if not y_vals:
            raise ValueError("scipy_stats pearsonr: y_column is required")
        r, p = sp_stats.pearsonr(x_vals, y_vals)
        return {"r": float(r), "p_value": float(p), "significant": float(p) < alpha_val}

    elif test == "spearmanr":
        x_vals = _col(samples_column) if samples_column else (sample or [])
        y_vals = _col(y_column) if y_column else []
        if not y_vals:
            raise ValueError("scipy_stats spearmanr: y_column is required")
        r, p = sp_stats.spearmanr(x_vals, y_vals)
        return {"rho": float(r), "p_value": float(p), "significant": float(p) < alpha_val}

    elif test == "zscore":
        import numpy as np  # noqa: PLC0415
        zscores = sp_stats.zscore(sample or []).tolist()
        return {"zscores": zscores, "count": len(zscores)}

    elif test == "describe":
        desc = sp_stats.describe(sample or [])
        return {
            "count": int(desc.nobs),
            "min": float(desc.minmax[0]),
            "max": float(desc.minmax[1]),
            "mean": float(desc.mean),
            "variance": float(desc.variance),
            "skewness": float(desc.skewness),
            "kurtosis": float(desc.kurtosis),
        }

    elif test == "normaltest":
        stat, p = sp_stats.normaltest(sample or [])
        return {"statistic": float(stat), "p_value": float(p), "is_normal": float(p) >= alpha_val}

    raise ValueError(f"scipy_stats: unknown test '{test}'")


# ---------------------------------------------------------------------------
# Task 36 — spaCy NLP Pipeline
# ---------------------------------------------------------------------------

SPACY_COMPONENTS = ["ner", "pos", "dep", "lemma", "sentences", "noun_phrases"]


@node(
    name="spaCy NLP Pipeline",
    id="spacy_nlp",
    category=AI_CATEGORY,
    role="executable",
    icon="type",
    requirements=["spacy>=3.7"],
    inputs=["main"],
    outputs=["main"],
    params={
        "text": {"multiline": True, "description": "Text to process. Also accepts wired input."},
        "model": {
            "placeholder": "en_core_web_sm",
            "description": "spaCy model name. Must be installed: python -m spacy download en_core_web_sm.",
        },
        "components": {
            "placeholder": "ner, pos, dep, lemma",
            "description": "Comma-separated pipeline components to include in output.",
        },
    },
)
def spacy_nlp(
    input: Any = None,
    text: str = "",
    model: str = "en_core_web_sm",
    components: str = "ner, pos, dep, lemma",
) -> dict[str, Any]:
    """Run a full spaCy NLP pipeline on text."""
    try:
        import spacy  # noqa: PLC0415
    except ImportError:
        raise ImportError(
            "spacy_nlp requires spacy>=3.7. Install with: pip install spacy && "
            "python -m spacy download en_core_web_sm"
        )

    effective_text = text if text.strip() else (str(input) if input is not None else "")
    if not effective_text:
        raise ValueError("spacy_nlp: text input is required")

    try:
        nlp = spacy.load(model or "en_core_web_sm")
    except OSError as exc:
        raise ImportError(
            f"spacy_nlp: model '{model}' not found. "
            f"Install it with: python -m spacy download {model}"
        ) from exc

    doc = nlp(effective_text)
    requested = {c.strip() for c in components.split(",") if c.strip()}

    result: dict[str, Any] = {"text": effective_text, "model": model}

    if "ner" in requested:
        result["entities"] = [
            {"text": ent.text, "label": ent.label_, "start": ent.start_char, "end": ent.end_char}
            for ent in doc.ents
        ]

    if "pos" in requested:
        result["tokens"] = [
            {"text": tok.text, "pos": tok.pos_, "tag": tok.tag_, "lemma": tok.lemma_}
            for tok in doc
        ]

    if "dep" in requested:
        result["dependency_tree"] = [
            {"text": tok.text, "dep": tok.dep_, "head": tok.head.text, "children": [c.text for c in tok.children]}
            for tok in doc
        ]

    if "lemma" in requested and "tokens" not in result:
        result["tokens"] = [{"text": tok.text, "lemma": tok.lemma_} for tok in doc]

    if "sentences" in requested:
        result["sentences"] = [sent.text.strip() for sent in doc.sents]

    if "noun_phrases" in requested:
        result["noun_phrases"] = [chunk.text for chunk in doc.noun_chunks]

    return result


# ---------------------------------------------------------------------------
# Task 37 — NetworkX Graph Operations
# ---------------------------------------------------------------------------

NETWORKX_OPS = [
    "shortest_path", "all_shortest_paths", "betweenness_centrality",
    "degree_centrality", "pagerank", "clustering", "connected_components",
    "minimum_spanning_tree", "neighbors",
]


@node(
    name="NetworkX Graph Operations",
    id="networkx_graph_ops",
    category=DATA_CATEGORY,
    role="executable",
    icon="git-branch",
    requirements=["networkx>=3.2"],
    inputs=["main"],
    outputs=["main"],
    params={
        "operation": {"choices": NETWORKX_OPS, "description": "Graph operation."},
        "source": {"description": "[shortest_path/neighbors] Source node ID."},
        "target": {"description": "[shortest_path] Target node ID."},
        "weight_column": {"description": "Edge weight column name."},
        "directed": {"description": "Treat graph as directed."},
    },
)
def networkx_graph_ops(
    input: Any = None,
    operation: str = "shortest_path",
    source: str = "",
    target: str = "",
    weight_column: str = "",
    directed: bool = False,
) -> dict[str, Any]:
    """Perform graph analysis operations using NetworkX."""
    try:
        import networkx as nx  # noqa: PLC0415
    except ImportError:
        raise ImportError("networkx_graph_ops requires networkx>=3.2. Install with: pip install networkx")

    # Build graph from edge list: [{source, target[, weight, ...]}]
    G: Any = nx.DiGraph() if directed else nx.Graph()

    if isinstance(input, list):
        for edge in input:
            if isinstance(edge, dict):
                s = str(edge.get("source") or edge.get("from") or edge.get("src") or "")
                t = str(edge.get("target") or edge.get("to") or edge.get("dst") or "")
                if s and t:
                    w = edge.get(weight_column) if weight_column else None
                    if w is not None:
                        G.add_edge(s, t, weight=float(w))
                    else:
                        G.add_edge(s, t)
    elif isinstance(input, dict):
        # Adjacency dict: {node: [neighbor, ...]}
        for node_, neighbors in input.items():
            for nbr in (neighbors if isinstance(neighbors, list) else []):
                G.add_edge(str(node_), str(nbr))

    if G.number_of_nodes() == 0:
        raise ValueError("networkx_graph_ops: could not build graph from input — expected list of {source, target} dicts")

    weight_attr = weight_column or None

    if operation == "shortest_path":
        if not source:
            raise ValueError("networkx_graph_ops shortest_path: source is required")
        if target:
            try:
                path = nx.shortest_path(G, source=source, target=target, weight=weight_attr)
                length = nx.shortest_path_length(G, source=source, target=target, weight=weight_attr)
                return {"path": path, "length": length, "hops": len(path) - 1}
            except nx.NetworkXNoPath:
                return {"path": None, "length": None, "hops": None, "error": "No path found"}
            except nx.NodeNotFound as exc:
                raise ValueError(f"networkx_graph_ops: node not found: {exc}") from exc
        else:
            paths = dict(nx.single_source_shortest_path(G, source))
            return {"paths": {t: p for t, p in paths.items()}, "node_count": G.number_of_nodes()}

    elif operation == "all_shortest_paths":
        if not source or not target:
            raise ValueError("networkx_graph_ops all_shortest_paths: source and target are required")
        try:
            paths = list(nx.all_shortest_paths(G, source, target, weight=weight_attr))
        except nx.NetworkXNoPath:
            paths = []
        return {"paths": paths, "count": len(paths)}

    elif operation == "betweenness_centrality":
        centrality = nx.betweenness_centrality(G, weight=weight_attr)
        return {"centrality": centrality, "most_central": max(centrality, key=centrality.get)}

    elif operation == "degree_centrality":
        centrality = nx.degree_centrality(G)
        return {"centrality": centrality, "most_central": max(centrality, key=centrality.get)}

    elif operation == "pagerank":
        pr = nx.pagerank(G, weight=weight_attr)
        return {"pagerank": pr, "top_node": max(pr, key=pr.get)}

    elif operation == "clustering":
        if directed:
            return {"clustering": {}}
        coeff = nx.clustering(G, weight=weight_attr)
        avg = nx.average_clustering(G, weight=weight_attr)
        return {"clustering": coeff, "average": avg}

    elif operation == "connected_components":
        if directed:
            comps = list(nx.weakly_connected_components(G))
        else:
            comps = list(nx.connected_components(G))
        return {"components": [list(c) for c in comps], "count": len(comps)}

    elif operation == "minimum_spanning_tree":
        if directed:
            raise ValueError("networkx_graph_ops: minimum_spanning_tree requires an undirected graph")
        mst = nx.minimum_spanning_tree(G, weight=weight_attr or "weight")
        edges = [{"source": u, "target": v, **(d if d else {})} for u, v, d in mst.edges(data=True)]
        return {"edges": edges, "node_count": mst.number_of_nodes(), "edge_count": mst.number_of_edges()}

    elif operation == "neighbors":
        if not source:
            raise ValueError("networkx_graph_ops neighbors: source is required")
        if source not in G:
            raise ValueError(f"networkx_graph_ops: node '{source}' not in graph")
        nbrs = list(G.neighbors(source))
        return {"neighbors": nbrs, "degree": G.degree(source)}

    raise ValueError(f"networkx_graph_ops: unknown operation '{operation}'")


# ---------------------------------------------------------------------------
# Task 38 — BeautifulSoup Web Scraper
# ---------------------------------------------------------------------------


@node(
    name="BeautifulSoup Web Scraper",
    id="beautifulsoup_scrape",
    category=API_CATEGORY,
    role="executable",
    icon="code",
    inputs=["main"],
    outputs=["main"],
    params={
        "url": {
            "placeholder": "https://example.com",
            "description": "URL to fetch HTML from. Leave empty if wiring HTML directly.",
        },
        "selectors": {
            "type": "key_value",
            "placeholder": '{"title": "h1", "price": ".price span"}',
            "description": "Map of field names to CSS selectors.",
        },
        "extract": {
            "choices": ["text", "html", "attribute"],
            "description": "What to extract: text content, inner HTML, or an attribute.",
        },
        "attribute_name": {
            "placeholder": "href",
            "description": "[extract=attribute] Attribute name to extract.",
        },
        "multiple": {"description": "Return all matches instead of first match."},
        "strip": {"description": "Strip whitespace from extracted text."},
    },
)
def beautifulsoup_scrape(
    input: Any = None,
    url: str = "",
    selectors: dict | None = None,
    extract: str = "text",
    attribute_name: str = "",
    multiple: bool = False,
    strip: bool = True,
) -> dict[str, Any]:
    """Scrape structured data from HTML using CSS selectors."""
    from bs4 import BeautifulSoup  # noqa: PLC0415 - already a core dep

    # Resolve HTML source
    if url:
        assert_public_http_url(url)
        import urllib.parse as _up  # noqa: PLC0415
        import requests as _req  # noqa: PLC0415
        _headers = {"User-Agent": "noodle-scraper/1.0"}
        resp = _req.get(url, timeout=30, allow_redirects=False, headers=_headers)
        # Follow redirects manually so each hop is validated against SSRF rules.
        for _ in range(10):
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            location = resp.headers.get("Location", "")
            if not location:
                break
            next_url = _up.urljoin(resp.url, location)
            assert_public_http_url(next_url)
            resp = _req.get(next_url, timeout=30, allow_redirects=False, headers=_headers)
        resp.raise_for_status()
        html_content = resp.text
    elif input is not None:
        html_content = str(input)
    else:
        raise ValueError("beautifulsoup_scrape: html input or url is required")

    try:
        soup = BeautifulSoup(html_content, "lxml")
    except Exception:
        soup = BeautifulSoup(html_content, "html.parser")

    if not selectors:
        # No selectors: return page text and title
        title = soup.title.string if soup.title else ""
        return {
            "title": title.strip() if strip and title else title,
            "text": soup.get_text(separator=" ", strip=strip),
            "url": url,
        }

    def _extract_element(el: Any) -> str:
        if extract == "html":
            return str(el)
        if extract == "attribute" and attribute_name:
            return el.get(attribute_name, "") or ""
        text = el.get_text(separator=" ")
        return text.strip() if strip else text

    results: dict[str, Any] = {}
    for field, selector in selectors.items():
        if multiple:
            elements = soup.select(selector)
            results[field] = [_extract_element(el) for el in elements]
        else:
            el = soup.select_one(selector)
            results[field] = _extract_element(el) if el else None

    return {"data": results, "url": url}


# ---------------------------------------------------------------------------
# Task 39 — SymPy Symbolic Math
# ---------------------------------------------------------------------------

SYMPY_OPS = ["solve", "simplify", "expand", "factor", "diff", "integrate", "limit", "matrix_ops", "latex"]


@node(
    name="SymPy Symbolic Math",
    id="sympy_math",
    category=DATA_CATEGORY,
    role="executable",
    icon="function-square",
    requirements=["sympy>=1.12"],
    inputs=["main"],
    outputs=["main"],
    params={
        "operation": {"choices": SYMPY_OPS, "description": "Symbolic math operation."},
        "expression": {
            "placeholder": "x**2 + 2*x + 1",
            "description": "Mathematical expression (SymPy syntax).",
        },
        "variable": {"placeholder": "x", "description": "Primary variable name."},
        "at_value": {"description": "[limit] Point to evaluate limit at."},
        "order": {"description": "[diff] Differentiation order."},
        "solve_for": {"description": "[solve] Variable to solve for (default: variable param)."},
    },
)
def sympy_math(
    input: Any = None,
    operation: str = "solve",
    expression: str = "",
    variable: str = "x",
    at_value: str = "0",
    order: int = 1,
    solve_for: str = "",
) -> dict[str, Any]:
    """Perform symbolic mathematics using SymPy."""
    try:
        import sympy as sp  # noqa: PLC0415
    except ImportError:
        raise ImportError("sympy_math requires sympy>=1.12. Install with: pip install sympy")

    expr_str = expression or (str(input) if input is not None else "")
    if not expr_str:
        raise ValueError("sympy_math: expression is required")

    var_name = variable or "x"
    var = sp.Symbol(var_name)

    local_dict = {var_name: var}

    # sympify uses eval internally; use parse_expr with an explicit identifier allowlist instead.
    _ALLOWED_IDENTIFIERS = frozenset({
        var_name,
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
        "sinh", "cosh", "tanh", "exp", "log", "sqrt",
        "Abs", "sign", "floor", "ceiling", "factorial",
        "pi", "E", "I", "oo", "zoo", "nan", "re", "im",
    })
    unknown_ids = set(re.findall(r"\b[A-Za-z_]\w*\b", expr_str)) - _ALLOWED_IDENTIFIERS
    if unknown_ids:
        raise ValueError(
            f"sympy_math: expression contains unknown identifiers: {sorted(unknown_ids)}. "
            "Only standard math functions and the declared variable are allowed."
        )
    try:
        from sympy.parsing.sympy_parser import (  # noqa: PLC0415
            parse_expr, standard_transformations, implicit_multiplication_application,
        )
        _transforms = standard_transformations + (implicit_multiplication_application,)
        expr = parse_expr(expr_str, local_dict=local_dict, transformations=_transforms, evaluate=False)
    except Exception as exc:
        raise ValueError(f"sympy_math: could not parse expression '{expr_str}': {exc}") from exc

    def _to_str(val: Any) -> str:
        return str(val)

    if operation == "solve":
        solve_var = sp.Symbol(solve_for or var_name)
        solutions = sp.solve(expr, solve_var)
        return {
            "solutions": [_to_str(s) for s in solutions],
            "expression": expr_str,
            "variable": str(solve_var),
        }

    elif operation == "simplify":
        result = sp.simplify(expr)
        return {"result": _to_str(result), "expression": expr_str}

    elif operation == "expand":
        result = sp.expand(expr)
        return {"result": _to_str(result), "expression": expr_str}

    elif operation == "factor":
        result = sp.factor(expr)
        return {"result": _to_str(result), "expression": expr_str}

    elif operation == "diff":
        n = max(1, int(order or 1))
        result = sp.diff(expr, var, n)
        return {"result": _to_str(result), "expression": expr_str, "order": n, "variable": var_name}

    elif operation == "integrate":
        result = sp.integrate(expr, var)
        return {"result": _to_str(result), "expression": expr_str, "variable": var_name}

    elif operation == "limit":
        try:
            at = sp.sympify(at_value or "0")
        except Exception:
            at = sp.sympify("0")
        result = sp.limit(expr, var, at)
        return {"result": _to_str(result), "expression": expr_str, "at": str(at)}

    elif operation == "matrix_ops":
        # For matrix ops, expression is a 2D list
        if isinstance(input, list):
            mat = sp.Matrix(input)
        else:
            mat = sp.Matrix([[expr]])
        return {
            "det": _to_str(mat.det()) if mat.is_square else None,
            "rank": int(mat.rank()),
            "shape": list(mat.shape),
            "eigenvalues": {_to_str(k): int(v) for k, v in mat.eigenvals().items()} if mat.is_square else {},
        }

    elif operation == "latex":
        return {"latex": sp.latex(expr), "expression": expr_str}

    raise ValueError(f"sympy_math: unknown operation '{operation}'")
