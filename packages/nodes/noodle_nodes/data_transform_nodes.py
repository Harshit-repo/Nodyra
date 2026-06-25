"""Pandas-based data transformation nodes."""

from __future__ import annotations

from typing import Any

from noodle.sdk import node


def _pd():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "Data transform requires pandas. Install: uv pip install pandas"
        ) from exc
    return pd


def _parse_data(data: Any) -> list[dict]:
    if isinstance(data, str):
        import json

        parsed = json.loads(data)
        if isinstance(parsed, list):
            return parsed
        raise ValueError("data must be a JSON array of objects")
    if isinstance(data, list):
        return data
    raise ValueError("data must be a list or JSON string")


_OPERATORS = [
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "startswith",
    "endswith",
    "in",
    "not_in",
    "is_null",
    "not_null",
]

_AGG_FUNCTIONS = [
    "sum",
    "mean",
    "count",
    "min",
    "max",
    "median",
    "std",
    "first",
    "last",
    "nunique",
]


@node(
    name="Data Filter Rows",
    id="data_filter_rows",
    category="Transform",
    icon="filter",
    requirements=["pandas>=2.0"],
    params={
        "data": {
            "description": "JSON array of objects to filter.",
            "multiline": True,
            "placeholder": '[{"col": "val"}, ...]',
        },
        "column": {
            "description": "Column name to filter on.",
        },
        "operator": {
            "choices": _OPERATORS,
            "description": "Comparison operator.",
        },
        "value": {
            "description": "Value to compare against (comma-separated for in/not_in).",
        },
    },
)
def data_filter_rows(
    input: Any = None,
    data: str = "",
    column: str = "",
    operator: str = "eq",
    value: str = "",
) -> dict[str, Any]:
    """Filter rows in a dataset by column comparison."""
    pd = _pd()
    rows = _parse_data(data)
    total_before = len(rows)

    if not rows:
        return {"rows": [], "count": 0, "total_before": 0}

    if not column:
        raise ValueError("data_filter_rows: column is required")

    df = pd.DataFrame(rows)

    if column not in df.columns:
        raise ValueError(f"data_filter_rows: column '{column}' not found in data")

    col = df[column]

    if operator in ("gt", "gte", "lt", "lte"):
        if value == "":
            raise ValueError(f"data_filter_rows: operator '{operator}' requires a numeric value")
        try:
            num_val = float(value)
        except (ValueError, TypeError):
            raise ValueError(
                f"data_filter_rows: value '{value}' is not numeric for operator '{operator}'"
            )
        if operator == "gt":
            mask = col.astype(float) > num_val
        elif operator == "gte":
            mask = col.astype(float) >= num_val
        elif operator == "lt":
            mask = col.astype(float) < num_val
        else:
            mask = col.astype(float) <= num_val
    elif operator == "eq":
        mask = col == value
    elif operator == "neq":
        mask = col != value
    elif operator == "contains":
        mask = col.astype(str).str.contains(value, na=False)
    elif operator == "startswith":
        mask = col.astype(str).str.startswith(value)
    elif operator == "endswith":
        mask = col.astype(str).str.endswith(value)
    elif operator == "in":
        if value == "":
            raise ValueError(
                "data_filter_rows: operator 'in' requires a comma-separated value list"
            )
        vals = [v.strip() for v in value.split(",")]
        mask = col.isin(vals)
    elif operator == "not_in":
        if value == "":
            raise ValueError(
                "data_filter_rows: operator 'not_in' requires a comma-separated value list"
            )
        vals = [v.strip() for v in value.split(",")]
        mask = ~col.isin(vals)
    elif operator == "is_null":
        mask = col.isna()
    elif operator == "not_null":
        mask = col.notna()
    else:
        raise ValueError(f"data_filter_rows: unknown operator '{operator}'")

    filtered = df[mask].to_dict(orient="records")
    return {"rows": filtered, "count": len(filtered), "total_before": total_before}


@node(
    name="Data Sort",
    id="data_sort",
    category="Transform",
    icon="filter",
    requirements=["pandas>=2.0"],
    params={
        "data": {
            "description": "JSON array of objects to sort.",
            "multiline": True,
        },
        "sort_by": {
            "description": "Column(s) to sort by. Comma-separated for multiple.",
            "placeholder": "column1,column2",
        },
        "ascending": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Sort ascending. For multi-column, use 'true,false' string.",
        },
    },
)
def data_sort(
    input: Any = None,
    data: str = "",
    sort_by: str = "",
    ascending: bool | str = True,
) -> dict[str, Any]:
    """Sort rows by one or more columns."""
    pd = _pd()
    rows = _parse_data(data)

    if not rows:
        return {"rows": [], "count": 0}

    if not sort_by:
        raise ValueError("data_sort: sort_by is required")

    columns = [c.strip() for c in sort_by.split(",") if c.strip()]
    df = pd.DataFrame(rows)

    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"data_sort: columns not found: {', '.join(missing)}")

    if isinstance(ascending, bool):
        ascending_list = ascending
    elif isinstance(ascending, str):
        ascending_list = [part.strip().lower() == "true" for part in ascending.split(",")]
        if len(ascending_list) == 1:
            ascending_list = ascending_list[0]
    else:
        ascending_list = True

    sorted_df = df.sort_values(by=columns, ascending=ascending_list)
    result = sorted_df.to_dict(orient="records")
    return {"rows": result, "count": len(result)}


@node(
    name="Data Group By",
    id="data_group_by",
    category="Transform",
    icon="filter",
    requirements=["pandas>=2.0"],
    params={
        "data": {
            "description": "JSON array of objects.",
            "multiline": True,
        },
        "group_columns": {
            "description": "Column(s) to group by, comma-separated.",
        },
        "aggregate_column": {
            "description": "Column to aggregate.",
        },
        "agg_function": {
            "choices": _AGG_FUNCTIONS,
            "description": "Aggregation function.",
        },
    },
)
def data_group_by(
    input: Any = None,
    data: str = "",
    group_columns: str = "",
    aggregate_column: str = "",
    agg_function: str = "sum",
) -> dict[str, Any]:
    """Group rows and compute an aggregation."""
    pd = _pd()
    rows = _parse_data(data)

    if not rows:
        return {"rows": [], "count": 0}

    if not group_columns:
        raise ValueError("data_group_by: group_columns is required")
    if not aggregate_column:
        raise ValueError("data_group_by: aggregate_column is required")

    group_cols = [c.strip() for c in group_columns.split(",") if c.strip()]
    df = pd.DataFrame(rows)

    missing = [c for c in group_cols + [aggregate_column] if c not in df.columns]
    if missing:
        raise ValueError(f"data_group_by: columns not found: {', '.join(missing)}")

    if agg_function in ("sum", "mean", "median", "std"):
        if not pd.api.types.is_numeric_dtype(df[aggregate_column]):
            raise ValueError(
                f"data_group_by: '{agg_function}' requires a numeric column, "
                f"but '{aggregate_column}' is not numeric"
            )

    grouped = df.groupby(group_cols, as_index=False)
    agg_map = {aggregate_column: agg_function}
    result_df = grouped.agg(agg_map).reset_index(drop=True)
    result = result_df.to_dict(orient="records")
    return {"rows": result, "count": len(result)}


@node(
    name="Data Select Columns",
    id="data_select_columns",
    category="Transform",
    icon="filter",
    requirements=["pandas>=2.0"],
    params={
        "data": {
            "description": "JSON array of objects.",
            "multiline": True,
        },
        "columns": {
            "description": "Columns to keep, comma-separated.",
            "placeholder": "col1,col2,col3",
        },
        "rename": {
            "group": "Options",
            "description": "Rename mapping as JSON object.",
            "placeholder": '{"old_name": "new_name"}',
            "multiline": True,
        },
    },
)
def data_select_columns(
    input: Any = None,
    data: str = "",
    columns: str = "",
    rename: str = "",
) -> dict[str, Any]:
    """Select and optionally rename columns."""
    pd = _pd()
    rows = _parse_data(data)

    if not rows:
        return {"rows": [], "count": 0}

    if not columns:
        raise ValueError("data_select_columns: columns is required")

    col_list = [c.strip() for c in columns.split(",") if c.strip()]
    df = pd.DataFrame(rows)

    available = [c for c in col_list if c in df.columns]
    skipped = [c for c in col_list if c not in df.columns]

    result_df = df[available].copy()

    if rename:
        import json

        try:
            rename_map = json.loads(rename)
        except (json.JSONDecodeError, TypeError):
            raise ValueError("data_select_columns: rename must be a valid JSON object")
        if not isinstance(rename_map, dict):
            raise ValueError("data_select_columns: rename must be a JSON object")
        result_df = result_df.rename(columns=rename_map)

    result = result_df.to_dict(orient="records")

    output: dict[str, Any] = {"rows": result, "count": len(result)}
    if skipped:
        output["skipped"] = skipped
    return output


@node(
    name="Data Fill NA",
    id="data_fill_na",
    category="Transform",
    icon="filter",
    requirements=["pandas>=2.0"],
    params={
        "data": {
            "description": "JSON array of objects.",
            "multiline": True,
        },
        "fill_value": {
            "description": "Value to fill. Use literal 'mean' or 'median' for column stats.",
            "default": "",
        },
        "columns": {
            "group": "Options",
            "description": "Columns to fill (comma-separated, blank=all).",
        },
    },
)
def data_fill_na(
    input: Any = None,
    data: str = "",
    fill_value: str = "",
    columns: str = "",
) -> dict[str, Any]:
    """Fill missing values in a dataset."""
    pd = _pd()
    rows = _parse_data(data)

    if not rows:
        return {"rows": [], "count": 0, "filled_cells": 0}

    df = pd.DataFrame(rows)
    total_before = int(df.isna().sum().sum())

    if total_before == 0:
        return {"rows": rows, "count": len(rows), "filled_cells": 0}

    if columns:
        col_list = [c.strip() for c in columns.split(",") if c.strip()]
        missing = [c for c in col_list if c not in df.columns]
        if missing:
            raise ValueError(f"data_fill_na: columns not found: {', '.join(missing)}")
        target_cols = col_list
    else:
        target_cols = list(df.columns)

    if fill_value in ("mean", "median"):
        for col in target_cols:
            if pd.api.types.is_numeric_dtype(df[col]):
                if fill_value == "mean":
                    replacement = df[col].mean()
                else:
                    replacement = df[col].median()
                df[col] = df[col].fillna(replacement)
    else:
        df[target_cols] = df[target_cols].fillna(fill_value)

    filled = total_before - int(df.isna().sum().sum())
    result = df.to_dict(orient="records")
    return {"rows": result, "count": len(result), "filled_cells": filled}


@node(
    name="Data Unique Values",
    id="data_unique_values",
    category="Transform",
    icon="filter",
    tool_side_effecting=False,
    params={
        "data": {
            "description": "JSON array of objects.",
            "multiline": True,
        },
        "column": {
            "description": "Column to get unique values from.",
        },
        "sort": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Sort unique values.",
        },
    },
)
def data_unique_values(
    input: Any = None,
    data: str = "",
    column: str = "",
    sort: bool = True,
) -> dict[str, Any]:
    """Get unique values from a column."""
    rows = _parse_data(data)

    if not rows:
        return {"values": [], "count": 0, "total_rows": 0}

    if not column:
        raise ValueError("data_unique_values: column is required")

    if column not in rows[0]:
        raise ValueError(f"data_unique_values: column '{column}' not found in data")

    seen: set[Any] = set()
    for row in rows:
        if column in row:
            seen.add(row[column])

    values = list(seen)
    if sort:
        try:
            values = sorted(values)
        except TypeError:
            values = sorted(values, key=str)
    return {"values": values, "count": len(values), "total_rows": len(rows)}


@node(
    name="Data Rename Column",
    id="data_rename_column",
    category="Transform",
    icon="filter",
    requirements=["pandas>=2.0"],
    params={
        "data": {
            "description": "JSON array of objects.",
            "multiline": True,
        },
        "column": {
            "description": "Existing column name to rename.",
        },
        "new_name": {
            "description": "New column name.",
        },
    },
)
def data_rename_column(
    input: Any = None,
    data: str = "",
    column: str = "",
    new_name: str = "",
) -> dict[str, Any]:
    """Rename a single column in a dataset."""
    pd = _pd()
    rows = _parse_data(data)

    if not rows:
        return {"rows": [], "count": 0}

    if not column:
        raise ValueError("data_rename_column: column is required")
    if not new_name:
        raise ValueError("data_rename_column: new_name is required")

    df = pd.DataFrame(rows)
    if column not in df.columns:
        raise ValueError(f"data_rename_column: column '{column}' not found in data")

    result_df = df.rename(columns={column: new_name})
    result = result_df.to_dict(orient="records")
    return {"rows": result, "count": len(result)}


@node(
    name="Data Drop Column",
    id="data_drop_column",
    category="Transform",
    icon="filter",
    requirements=["pandas>=2.0"],
    params={
        "data": {
            "description": "JSON array of objects.",
            "multiline": True,
        },
        "columns": {
            "description": "Column(s) to drop, comma-separated.",
        },
    },
)
def data_drop_column(
    input: Any = None,
    data: str = "",
    columns: str = "",
) -> dict[str, Any]:
    """Drop one or more columns from a dataset."""
    pd = _pd()
    rows = _parse_data(data)

    if not rows:
        return {"rows": [], "count": 0}

    if not columns:
        raise ValueError("data_drop_column: columns is required")

    col_list = [c.strip() for c in columns.split(",") if c.strip()]
    df = pd.DataFrame(rows)

    missing = [c for c in col_list if c not in df.columns]
    if missing:
        raise ValueError(f"data_drop_column: columns not found: {', '.join(missing)}")

    result_df = df.drop(columns=col_list)
    result = result_df.to_dict(orient="records")
    return {"rows": result, "count": len(result)}


@node(
    name="Data Sample",
    id="data_sample",
    category="Transform",
    icon="filter",
    tool_side_effecting=False,
    requirements=["pandas>=2.0"],
    params={
        "data": {
            "description": "JSON array of objects.",
            "multiline": True,
        },
        "n": {
            "type": "integer",
            "default": 10,
            "description": "Number of rows to sample.",
        },
        "seed": {
            "group": "Options",
            "type": "integer",
            "description": "Random seed for reproducibility.",
        },
    },
)
def data_sample(
    input: Any = None,
    data: str = "",
    n: int = 10,
    seed: int | None = None,
) -> dict[str, Any]:
    """Randomly sample rows from a dataset."""
    pd = _pd()
    rows = _parse_data(data)

    if not rows:
        return {"rows": [], "count": 0}

    n = max(1, min(len(rows), int(n or 10)))
    df = pd.DataFrame(rows)
    sampled = df.sample(n=n, random_state=seed).to_dict(orient="records")
    return {"rows": sampled, "count": len(sampled), "total_before": len(rows)}


__all__ = [
    "data_filter_rows",
    "data_sort",
    "data_group_by",
    "data_select_columns",
    "data_fill_na",
    "data_unique_values",
    "data_rename_column",
    "data_drop_column",
    "data_sample",
]
