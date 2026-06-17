"""Data quality, validation, reconciliation, and normalization nodes.

The nodes in this module are deliberately light at import time. Optional data
quality packages are imported inside node functions only when needed.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from noodle.artifacts import write_text
from noodle.datasets import is_dataset_ref
from noodle.sdk import node
from noodle_nodes.datasets import materialize_dataset, records_to_dataset

DATA_QUALITY_CATEGORY = "Data Quality"


def _to_records(value: Any, *, cap: int = 100_000) -> list[dict[str, Any]]:
    if is_dataset_ref(value):
        return materialize_dataset(value, cap=cap, allow_truncate=True)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("records"), list):
            return [row for row in value["records"] if isinstance(row, dict)]
        return [value]
    return []


def _ts() -> str:
    return datetime.now(tz=UTC).isoformat()


def _json_loads(value: str, default: Any) -> Any:
    if not value or not value.strip():
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc


@node(
    name="Data Profile Report",
    id="data_profile_report",
    category=DATA_QUALITY_CATEGORY,
    icon="file-search",
    requirements=["pandas>=2.0", "ydata-profiling>=4.0"],
    input_kinds={"input": "dataset"},
    params={
        "title": {"description": "Report title."},
        "minimal": {"description": "Use ydata-profiling minimal mode for faster reports."},
    },
)
def data_profile_report(
    input: Any = None,
    title: str = "Data Profile Report",
    minimal: bool = True,
) -> dict[str, Any]:
    """Generate a ydata-profiling HTML report artifact for a DatasetRef."""
    try:
        import pandas as pd  # type: ignore[import-not-found]
        from ydata_profiling import ProfileReport  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Data Profile Report requires pandas and ydata-profiling. Add "
            "pandas>=2.0 and ydata-profiling>=4.0 to the workflow environment."
        ) from exc

    rows = _to_records(input)
    if not rows:
        raise ValueError("input must contain at least one record.")
    df = pd.DataFrame(rows)
    profile = ProfileReport(df, title=title or "Data Profile Report", minimal=minimal)
    html = profile.to_html()
    artifact = write_text(
        html,
        "data-profile-report.html",
        "text/html; charset=utf-8",
        metadata={"kind": "data_profile_report", "title": title},
    )
    return {
        "report": artifact,
        "n_rows": int(len(df)),
        "n_columns": int(len(df.columns)),
        "columns": list(df.columns),
        "created_at": _ts(),
    }


@node(
    name="Schema Validate",
    id="schema_validate",
    category=DATA_QUALITY_CATEGORY,
    icon="file-json",
    requirements=["jsonschema>=4.21"],
    outputs=["main", "invalid"],
    params={
        "schema_json": {
            "description": "JSON Schema object used to validate each record.",
            "multiline": True,
        },
        "on_error": {
            "choices": ["summarize", "fail"],
            "description": "Summarize invalid rows or fail immediately.",
        },
        "include_valid_rows": {"description": "Return a DatasetRef containing valid rows."},
    },
)
def schema_validate(
    input: Any = None,
    schema_json: str = "{}",
    on_error: str = "summarize",
    include_valid_rows: bool = True,
) -> dict[str, Any]:
    """Validate records against JSON Schema."""
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Schema Validate requires jsonschema>=4.21 in the workflow environment."
        ) from exc

    schema = _json_loads(schema_json, {})
    if not isinstance(schema, dict) or not schema:
        raise ValueError("schema_json must be a non-empty JSON Schema object.")
    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records.")

    validator = jsonschema.Draft202012Validator(schema)
    valid_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        errors = sorted(validator.iter_errors(row), key=lambda err: list(err.path))
        if not errors:
            valid_rows.append(row)
            continue
        invalid_rows.append(
            {
                **row,
                "_row_index": index,
                "_validation_errors": "; ".join(err.message for err in errors[:5]),
            }
        )

    if invalid_rows and on_error == "fail":
        first = invalid_rows[0]["_validation_errors"]
        raise ValueError(f"{len(invalid_rows)} rows failed schema validation: {first}")

    summary = {
        "n_rows": len(rows),
        "n_valid": len(valid_rows),
        "n_invalid": len(invalid_rows),
        "valid_rate": len(valid_rows) / max(len(rows), 1),
    }
    return {
        "main": {
            **summary,
            "valid_rows": records_to_dataset(valid_rows, name="valid-rows.parquet")
            if include_valid_rows
            else None,
        },
        "invalid": records_to_dataset(invalid_rows, name="invalid-rows.parquet")
        if invalid_rows
        else None,
    }


@node(
    name="Expectation Suite Run",
    id="expectation_suite_run",
    category=DATA_QUALITY_CATEGORY,
    icon="check-square",
    outputs=["main", "failures"],
    params={
        "suite_json": {
            "description": (
                "JSON array of expectations. Supported types: not_null, unique, "
                "in_set, min, max, regex."
            ),
            "multiline": True,
            "placeholder": (
                '[{"type":"not_null","column":"email"},'
                '{"type":"regex","column":"email","pattern":"@"}]'
            ),
        },
        "fail_on_error": {"description": "Raise if any expectation fails."},
    },
)
def expectation_suite_run(
    input: Any = None,
    suite_json: str = "[]",
    fail_on_error: bool = False,
) -> dict[str, Any]:
    """Run a lightweight expectation suite against records."""
    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records.")
    expectations = _json_loads(suite_json, [])
    if not isinstance(expectations, list) or not expectations:
        raise ValueError("suite_json must be a non-empty JSON array.")

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for exp_idx, exp in enumerate(expectations):
        if not isinstance(exp, dict):
            continue
        exp_type = str(exp.get("type") or "").strip()
        column = str(exp.get("column") or "").strip()
        if not column:
            raise ValueError(f"Expectation {exp_idx} is missing column.")
        values = [row.get(column) for row in rows]
        failed_indexes: list[int] = []

        if exp_type == "not_null":
            failed_indexes = [i for i, value in enumerate(values) if value in (None, "")]
        elif exp_type == "unique":
            counts = Counter(str(value) for value in values)
            failed_indexes = [
                i for i, value in enumerate(values) if counts[str(value)] > 1
            ]
        elif exp_type == "in_set":
            allowed = set(str(v) for v in exp.get("values", []))
            failed_indexes = [
                i for i, value in enumerate(values) if str(value) not in allowed
            ]
        elif exp_type == "min":
            minimum = float(exp.get("value"))
            failed_indexes = [
                i for i, value in enumerate(values) if value is None or float(value) < minimum
            ]
        elif exp_type == "max":
            maximum = float(exp.get("value"))
            failed_indexes = [
                i for i, value in enumerate(values) if value is None or float(value) > maximum
            ]
        elif exp_type == "regex":
            pattern = re.compile(str(exp.get("pattern") or ""))
            failed_indexes = [
                i for i, value in enumerate(values) if not pattern.search(str(value or ""))
            ]
        else:
            raise ValueError(f"Unknown expectation type: {exp_type}")

        passed = not failed_indexes
        result = {
            "expectation": exp_type,
            "column": column,
            "passed": passed,
            "failed_count": len(failed_indexes),
            "failed_indexes": failed_indexes[:20],
        }
        results.append(result)
        for idx in failed_indexes:
            failures.append(
                {
                    "_row_index": idx,
                    "_expectation": exp_type,
                    "_column": column,
                    **rows[idx],
                }
            )

    if failures and fail_on_error:
        raise ValueError(f"{len(failures)} expectation failures found.")

    summary = {
        "passed": not failures,
        "n_expectations": len(results),
        "n_failures": len(failures),
        "results": results,
        "created_at": _ts(),
    }
    return {
        "main": summary,
        "failures": records_to_dataset(failures, name="expectation-failures.parquet")
        if failures
        else None,
    }


@node(
    name="Record Linkage",
    id="record_linkage",
    category=DATA_QUALITY_CATEGORY,
    icon="link",
    outputs=["main", "matches"],
    params={
        "left_key": {"description": "Column on the left/input dataset."},
        "right_key": {"description": "Column on the right dataset."},
        "right_records_json": {
            "description": "Right-side records as JSON array when no right input is wired.",
            "multiline": True,
        },
        "threshold": {"description": "Similarity threshold from 0.0 to 1.0."},
        "max_matches_per_row": {"description": "Maximum matches emitted per left row."},
    },
)
def record_linkage(
    input: Any = None,
    right: Any = None,
    left_key: str = "",
    right_key: str = "",
    right_records_json: str = "[]",
    threshold: float = 0.85,
    max_matches_per_row: int = 1,
) -> dict[str, Any]:
    """Fuzzy-match records between two datasets."""
    from difflib import SequenceMatcher

    left_rows = _to_records(input)
    right_rows = _to_records(right)
    if not right_rows:
        right_rows = _json_loads(right_records_json, [])
    if not isinstance(right_rows, list):
        raise ValueError("right_records_json must be a JSON array.")
    right_rows = [row for row in right_rows if isinstance(row, dict)]

    if not left_rows or not right_rows:
        raise ValueError("Record Linkage requires non-empty left and right records.")
    if not left_key or not right_key:
        raise ValueError("left_key and right_key are required.")

    try:
        from rapidfuzz import fuzz  # type: ignore[import-not-found]

        def _score(a: str, b: str) -> float:
            return float(fuzz.token_sort_ratio(a, b)) / 100.0

    except ImportError:

        def _score(a: str, b: str) -> float:
            return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    matches: list[dict[str, Any]] = []
    for left_index, left_row in enumerate(left_rows):
        scored: list[tuple[float, int, dict[str, Any]]] = []
        left_value = str(left_row.get(left_key) or "")
        for right_index, right_row in enumerate(right_rows):
            right_value = str(right_row.get(right_key) or "")
            score = _score(left_value, right_value)
            if score >= threshold:
                scored.append((score, right_index, right_row))
        scored.sort(key=lambda item: item[0], reverse=True)
        for score, right_index, right_row in scored[: max(1, int(max_matches_per_row or 1))]:
            matches.append(
                {
                    "left_index": left_index,
                    "right_index": right_index,
                    "score": round(score, 4),
                    "left": left_row,
                    "right": right_row,
                }
            )

    return {
        "main": {
            "left_rows": len(left_rows),
            "right_rows": len(right_rows),
            "matches": len(matches),
            "threshold": float(threshold),
        },
        "matches": records_to_dataset(matches, name="record-linkage.parquet")
        if matches
        else None,
    }


@node(
    name="Data Reconcile",
    id="data_reconcile",
    category=DATA_QUALITY_CATEGORY,
    icon="git-compare",
    outputs=["main", "left_only", "right_only", "changed"],
    params={
        "key_columns": {"description": "Comma-separated key columns used to match rows."},
        "right_records_json": {
            "description": "Right-side records as JSON array when no right input is wired.",
            "multiline": True,
        },
        "compare_columns": {
            "description": "Comma-separated columns to compare. Blank = all non-key columns.",
        },
    },
)
def data_reconcile(
    input: Any = None,
    right: Any = None,
    key_columns: str = "",
    right_records_json: str = "[]",
    compare_columns: str = "",
) -> dict[str, Any]:
    """Compare two datasets by keys and emit left-only/right-only/changed rows."""
    left_rows = _to_records(input)
    right_rows = _to_records(right)
    if not right_rows:
        right_rows = _json_loads(right_records_json, [])
    right_rows = [row for row in right_rows if isinstance(row, dict)]
    keys = [col.strip() for col in key_columns.split(",") if col.strip()]
    if not keys:
        raise ValueError("key_columns is required.")

    def _key(row: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(row.get(col) for col in keys)

    left_by_key = {_key(row): row for row in left_rows}
    right_by_key = {_key(row): row for row in right_rows}
    compare = [col.strip() for col in compare_columns.split(",") if col.strip()]
    if not compare:
        compare = sorted(
            {
                key
                for row in [*left_rows, *right_rows]
                for key in row
                if key not in keys
            }
        )

    left_only = [row for key, row in left_by_key.items() if key not in right_by_key]
    right_only = [row for key, row in right_by_key.items() if key not in left_by_key]
    changed: list[dict[str, Any]] = []
    for key in sorted(left_by_key.keys() & right_by_key.keys()):
        left_row = left_by_key[key]
        right_row = right_by_key[key]
        diffs = {
            col: {"left": left_row.get(col), "right": right_row.get(col)}
            for col in compare
            if left_row.get(col) != right_row.get(col)
        }
        if diffs:
            changed.append({"key": list(key), "diffs": diffs, "left": left_row, "right": right_row})

    summary = {
        "left_rows": len(left_rows),
        "right_rows": len(right_rows),
        "left_only": len(left_only),
        "right_only": len(right_only),
        "changed": len(changed),
        "matched": len(left_by_key.keys() & right_by_key.keys()),
    }
    return {
        "main": summary,
        "left_only": records_to_dataset(left_only, name="left-only.parquet")
        if left_only
        else None,
        "right_only": records_to_dataset(right_only, name="right-only.parquet")
        if right_only
        else None,
        "changed": records_to_dataset(changed, name="changed.parquet")
        if changed
        else None,
    }


@node(
    name="Outlier Detect Statistical",
    id="outlier_detect_statistical",
    category=DATA_QUALITY_CATEGORY,
    icon="scan-search",
    outputs=["main", "outliers"],
    params={
        "column": {"description": "Numeric column to inspect."},
        "method": {"choices": ["zscore", "iqr", "modified_zscore"]},
        "threshold": {"description": "Threshold. zscore default 3.0, IQR default 1.5."},
    },
)
def outlier_detect_statistical(
    input: Any = None,
    column: str = "",
    method: str = "zscore",
    threshold: float = 3.0,
) -> dict[str, Any]:
    """Detect statistical outliers in a numeric column."""
    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records.")
    if not column:
        raise ValueError("column is required.")

    values: list[tuple[int, float]] = []
    for idx, row in enumerate(rows):
        try:
            values.append((idx, float(row.get(column))))
        except (TypeError, ValueError):
            continue
    if len(values) < 3:
        raise ValueError("At least three numeric values are required.")

    nums = [v for _, v in values]
    outlier_indexes: set[int] = set()
    if method == "iqr":
        sorted_nums = sorted(nums)
        q1 = sorted_nums[len(sorted_nums) // 4]
        q3 = sorted_nums[(len(sorted_nums) * 3) // 4]
        iqr = q3 - q1
        limit = float(threshold or 1.5)
        low, high = q1 - limit * iqr, q3 + limit * iqr
        outlier_indexes = {idx for idx, value in values if value < low or value > high}
    elif method == "modified_zscore":
        median = sorted(nums)[len(nums) // 2]
        deviations = [abs(v - median) for v in nums]
        mad = sorted(deviations)[len(deviations) // 2] or 1e-9
        limit = float(threshold or 3.5)
        outlier_indexes = {
            idx for idx, value in values if abs(0.6745 * (value - median) / mad) > limit
        }
    else:
        mean = sum(nums) / len(nums)
        variance = sum((v - mean) ** 2 for v in nums) / max(len(nums) - 1, 1)
        std = math.sqrt(variance) or 1e-9
        limit = float(threshold or 3.0)
        outlier_indexes = {idx for idx, value in values if abs((value - mean) / std) > limit}

    outliers = [
        {"_row_index": idx, "_outlier_column": column, **rows[idx]}
        for idx in sorted(outlier_indexes)
    ]
    summary = {
        "n_rows": len(rows),
        "n_numeric": len(values),
        "n_outliers": len(outliers),
        "method": method,
        "column": column,
    }
    return {
        "main": summary,
        "outliers": records_to_dataset(outliers, name="outliers.parquet")
        if outliers
        else None,
    }


@node(
    name="String Normalize",
    id="string_normalize",
    category=DATA_QUALITY_CATEGORY,
    icon="text-case",
    output_kinds={"main": "dataset"},
    params={
        "columns": {"description": "Comma-separated text columns to normalize."},
        "case": {"choices": ["none", "lower", "upper", "title"]},
        "strip_accents": {"description": "Remove accents/diacritics."},
        "collapse_whitespace": {"description": "Collapse repeated whitespace to one space."},
        "trim": {"description": "Trim leading/trailing whitespace."},
        "output_suffix": {"description": "Suffix for normalized columns. Blank = overwrite."},
    },
)
def string_normalize(
    input: Any = None,
    columns: str = "",
    case: str = "lower",
    strip_accents: bool = True,
    collapse_whitespace: bool = True,
    trim: bool = True,
    output_suffix: str = "",
) -> dict[str, Any]:
    """Normalize text columns and return a DatasetRef."""
    rows = _to_records(input)
    cols = [col.strip() for col in columns.split(",") if col.strip()]
    if not rows or not cols:
        raise ValueError("input and columns are required.")

    def _normalize(value: Any) -> str:
        text = str(value or "")
        if trim:
            text = text.strip()
        if strip_accents:
            text = "".join(
                ch
                for ch in unicodedata.normalize("NFKD", text)
                if not unicodedata.combining(ch)
            )
        if collapse_whitespace:
            text = re.sub(r"\s+", " ", text)
        if case == "lower":
            text = text.lower()
        elif case == "upper":
            text = text.upper()
        elif case == "title":
            text = text.title()
        return text

    result_rows: list[dict[str, Any]] = []
    suffix = output_suffix or ""
    for row in rows:
        out = dict(row)
        for col in cols:
            out[f"{col}{suffix}"] = _normalize(row.get(col))
        result_rows.append(out)
    return records_to_dataset(result_rows, name="string-normalized.parquet")


@node(
    name="Date Parse Normalize",
    id="date_parse_normalize",
    category=DATA_QUALITY_CATEGORY,
    icon="calendar",
    output_kinds={"main": "dataset"},
    params={
        "columns": {"description": "Comma-separated date/time columns."},
        "output_format": {"description": "strftime format or 'iso'."},
        "timezone": {"description": "Timezone name passed to dateparser when installed."},
        "output_suffix": {"description": "Suffix for parsed columns. Blank = overwrite."},
    },
)
def date_parse_normalize(
    input: Any = None,
    columns: str = "",
    output_format: str = "iso",
    timezone: str = "UTC",
    output_suffix: str = "",
) -> dict[str, Any]:
    """Parse date strings into a consistent output format."""
    rows = _to_records(input)
    cols = [col.strip() for col in columns.split(",") if col.strip()]
    if not rows or not cols:
        raise ValueError("input and columns are required.")

    try:
        import dateparser  # type: ignore[import-not-found]
    except ImportError:
        dateparser = None

    def _parse(value: Any) -> str | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            dt = value
        elif dateparser is not None:
            dt = dateparser.parse(
                str(value),
                settings={"TIMEZONE": timezone or "UTC", "RETURN_AS_TIMEZONE_AWARE": True},
            )
        else:
            text = str(value)
            dt = None
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    dt = datetime.strptime(text, fmt).replace(tzinfo=UTC)
                    break
                except ValueError:
                    pass
            if dt is None:
                try:
                    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError:
                    return None
        if dt is None:
            return None
        if output_format == "iso":
            return dt.isoformat()
        return dt.strftime(output_format or "%Y-%m-%d")

    result_rows = []
    suffix = output_suffix or ""
    for row in rows:
        out = dict(row)
        for col in cols:
            out[f"{col}{suffix}"] = _parse(row.get(col))
        result_rows.append(out)
    return records_to_dataset(result_rows, name="date-normalized.parquet")


@node(
    name="Currency Normalize",
    id="currency_normalize",
    category=DATA_QUALITY_CATEGORY,
    icon="dollar-sign",
    output_kinds={"main": "dataset"},
    params={
        "amount_column": {"description": "Column containing amount strings or numbers."},
        "currency_column": {
            "description": "Optional existing currency-code column to preserve.",
        },
        "default_currency": {"description": "Currency code used when none is detected."},
        "output_amount_column": {"description": "Normalized decimal amount column."},
        "output_currency_column": {"description": "Normalized currency code column."},
    },
)
def currency_normalize(
    input: Any = None,
    amount_column: str = "amount",
    currency_column: str = "",
    default_currency: str = "USD",
    output_amount_column: str = "amount_normalized",
    output_currency_column: str = "currency",
) -> dict[str, Any]:
    """Normalize currency-like strings into decimal amount and ISO-ish code."""
    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records.")
    if not amount_column:
        raise ValueError("amount_column is required.")

    symbols = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}

    def _parse(value: Any) -> tuple[str | None, str]:
        text = str(value or "").strip()
        currency = default_currency.upper()
        for symbol, code in symbols.items():
            if symbol in text:
                currency = code
                break
        match = re.search(r"\b([A-Z]{3})\b", text.upper())
        if match:
            currency = match.group(1)
        cleaned = re.sub(r"[^0-9,.\-()]", "", text)
        negative = "(" in cleaned and ")" in cleaned
        cleaned = cleaned.replace("(", "").replace(")", "")
        comma_count = cleaned.count(",")
        dot_count = cleaned.count(".")
        if comma_count and dot_count:
            decimal_sep = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
            thousands_sep = "." if decimal_sep == "," else ","
            cleaned = cleaned.replace(thousands_sep, "").replace(decimal_sep, ".")
        elif comma_count:
            parts = cleaned.split(",")
            comma_is_thousands = (
                len(parts) > 1
                and all(len(part) == 3 and part.isdigit() for part in parts[1:])
                and parts[0].lstrip("-").isdigit()
            )
            cleaned = cleaned.replace(",", "" if comma_is_thousands else ".")
        elif dot_count > 1:
            parts = cleaned.split(".")
            dot_is_thousands = (
                len(parts) > 1
                and all(len(part) == 3 and part.isdigit() for part in parts[1:])
                and parts[0].lstrip("-").isdigit()
            )
            if dot_is_thousands:
                cleaned = cleaned.replace(".", "")
        try:
            amount = Decimal(cleaned)
            if negative:
                amount = -amount
            return str(amount), currency
        except (InvalidOperation, ValueError):
            return None, currency

    result_rows: list[dict[str, Any]] = []
    for row in rows:
        amount, detected = _parse(row.get(amount_column))
        out = dict(row)
        out[output_amount_column or "amount_normalized"] = amount
        out[output_currency_column or "currency"] = (
            str(row.get(currency_column) or detected or default_currency).upper()
            if currency_column
            else detected
        )
        result_rows.append(out)
    return records_to_dataset(result_rows, name="currency-normalized.parquet")
