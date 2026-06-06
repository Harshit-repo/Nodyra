"""LLM evaluation nodes — deterministic evals, judge, comparison, and gates.

These nodes form the evaluation half of the fine-tuning lifecycle. They consume
DatasetRef or model outputs and produce EvalResultRef envelopes backed by
artifact/DatasetRef storage.

Heavy optional packages (pandas, openai) are imported lazily.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from noodle.artifacts import is_artifact_ref, write_bytes, write_text
from noodle.datasets import is_dataset_ref
from noodle.sdk import node
from noodle_nodes._creds import cred_single
from noodle_nodes.datasets import dataframe_to_dataset, read_dataset

ML_CATEGORY = "Machine Learning"

_EVAL_RESULT_MARKER = "__noodle_eval_result__"
_FT_DATASET_MARKER = "__noodle_finetune_dataset__"
_FT_JOB_MARKER = "__noodle_finetune_job__"

_PANDAS_ERROR = (
    "pandas>=2.0 is required for eval dataset nodes. Add it to the workflow "
    "environment and rebuild."
)
_OPENAI_ERROR = (
    "openai>=1.0 is required for model comparison and judge nodes. Add it to "
    "the workflow environment and rebuild."
)


def _is_eval_result(value: Any) -> bool:
    return isinstance(value, dict) and value.get(_EVAL_RESULT_MARKER) is True


def _pandas():
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(_PANDAS_ERROR) from exc
    return pd


def _openai_client(api_key: str, base_url: str | None = None, org: str | None = None):
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(_OPENAI_ERROR) from exc
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if org:
        kwargs["organization"] = org
    return OpenAI(**kwargs)


def _extract_api_key(credentials: Any) -> tuple[str, str | None, str | None]:
    if isinstance(credentials, dict):
        return (
            str(credentials.get("api_key") or "").strip(),
            str(credentials.get("base_url") or "").strip() or None,
            str(credentials.get("organization") or "").strip() or None,
        )
    if isinstance(credentials, str):
        return credentials.strip(), None, None
    raise ValueError("openai_api_key must be an API key string or credential dict.")


def _to_dataframe(input_value: Any):
    pd = _pandas()
    if is_dataset_ref(input_value):
        conn, rel = read_dataset(input_value)
        try:
            return rel.df()
        finally:
            conn.close()
    if isinstance(input_value, list):
        return pd.DataFrame(input_value)
    raise ValueError(
        "input must be a DatasetRef or list of records — add a Records To Dataset node upstream."
    )


def _ts() -> str:
    return datetime.now(tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# Node: LLM Eval Dataset
# ---------------------------------------------------------------------------

@node(
    name="LLM Eval Dataset",
    id="llm_eval_dataset",
    category=ML_CATEGORY,
    icon="clipboard-text",
    description=(
        "Prepare a holdout evaluation dataset from a DatasetRef or records. "
        "Validates required columns and outputs a clean DatasetRef ready for "
        "model comparison or rule-based eval."
    ),
    requirements=["pandas>=2.0"],
    inputs=["input"],
    outputs=["main"],
    params={
        "prompt_column": {
            "description": "Column containing the prompt or user message.",
        },
        "expected_column": {
            "description": "Column containing the expected/gold answer.",
        },
        "metadata_columns": {
            "description": "Comma-separated columns to carry through as eval metadata.",
        },
        "sample_size": {
            "group": "Options",
            "description": "Max rows to sample (0 = use all rows).",
        },
        "random_seed": {
            "group": "Options",
            "description": "Seed for reproducible sampling.",
        },
        "dedupe": {
            "group": "Options",
            "description": "Remove duplicate prompts before evaluation.",
        },
    },
    param_groups={"Options": []},
)
def llm_eval_dataset(
    input: Any = None,
    prompt_column: str = "prompt",
    expected_column: str = "expected",
    metadata_columns: str = "",
    sample_size: int = 0,
    random_seed: int = 42,
    dedupe: bool = True,
) -> dict[str, Any]:
    """Validate and prepare an eval holdout dataset."""
    pd = _pandas()
    df = _to_dataframe(input)

    missing = [c for c in [prompt_column, expected_column] if c not in df.columns]
    if missing:
        raise ValueError(
            f"Required columns {missing} not found. "
            f"Available: {list(df.columns)}"
        )

    keep_cols = [prompt_column, expected_column]
    for c in [m.strip() for m in (metadata_columns or "").split(",") if m.strip()]:
        if c in df.columns:
            keep_cols.append(c)

    df = df[keep_cols].copy()

    if dedupe:
        df = df.drop_duplicates(subset=[prompt_column])

    df = df.dropna(subset=[prompt_column, expected_column])

    if sample_size and sample_size > 0 and len(df) > sample_size:
        df = df.sample(n=sample_size, random_state=random_seed)

    df = df.reset_index(drop=True)
    n_rows = len(df)

    if n_rows == 0:
        raise ValueError("Eval dataset is empty after filtering.")

    dataset_ref = dataframe_to_dataset(df, name="eval_dataset.parquet")

    return {
        "dataset": dataset_ref,
        "n_rows": n_rows,
        "prompt_column": prompt_column,
        "expected_column": expected_column,
        "columns": list(df.columns),
        "prepared_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: LLM Compare Models
# ---------------------------------------------------------------------------

@node(
    name="LLM Compare Models",
    id="llm_compare_models",
    category=ML_CATEGORY,
    icon="arrows-left-right",
    description=(
        "Run a prompt dataset against two models and collect side-by-side "
        "outputs for judge evaluation. Returns an EvalResultRef."
    ),
    requirements=["openai>=1.0", "pandas>=2.0"],
    inputs=["input"],
    outputs=["main"],
    params={
        "openai_api_key": {
            "description": "OpenAI API key.",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
        "baseline_model": {
            "description": "Model ID to use as baseline (e.g. gpt-4.1-mini).",
        },
        "candidate_model": {
            "description": "Model ID to compare against baseline (e.g. your fine-tuned model).",
        },
        "prompt_column": {
            "description": "Column name containing the prompt text.",
        },
        "expected_column": {
            "description": "Column with expected/gold answers (optional — used for scoring).",
        },
        "system_prompt": {
            "description": "System message to prepend to each prompt.",
        },
        "max_rows": {
            "group": "Options",
            "description": "Cap the number of rows compared (0 = all).",
        },
        "concurrency": {
            "group": "Options",
            "description": "Number of parallel API calls per model.",
        },
        "temperature": {
            "group": "Options",
            "description": "Sampling temperature for both models.",
        },
        "max_tokens": {
            "group": "Options",
            "description": "Max tokens per completion.",
        },
    },
    param_groups={"Options": []},
)
def llm_compare_models(
    input: Any = None,
    openai_api_key: Any = None,
    baseline_model: str = "gpt-4.1-mini",
    candidate_model: str = "",
    prompt_column: str = "prompt",
    expected_column: str = "expected",
    system_prompt: str = "",
    max_rows: int = 50,
    concurrency: int = 5,
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """Compare two OpenAI models on a prompt dataset."""
    pd = _pandas()

    # Accept LLM Eval Dataset output or plain DatasetRef/records
    if isinstance(input, dict) and "dataset" in input and is_dataset_ref(input["dataset"]):
        prompt_col = str(input.get("prompt_column") or prompt_column)
        expected_col = str(input.get("expected_column") or expected_column)
        df = _to_dataframe(input["dataset"])
    else:
        prompt_col = prompt_column
        expected_col = expected_column
        df = _to_dataframe(input)

    if prompt_col not in df.columns:
        raise ValueError(f"prompt_column {prompt_col!r} not in dataset.")

    if not candidate_model or not candidate_model.strip():
        raise ValueError("candidate_model is required.")

    if max_rows and max_rows > 0 and len(df) > max_rows:
        df = df.head(max_rows)

    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")

    client = _openai_client(api_key, base_url, org)

    def _call(model: str, prompt: str) -> str:
        messages: list[dict[str, str]] = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": str(prompt)})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens or 512,
        )
        return str(resp.choices[0].message.content or "")

    rows = df.to_dict(orient="records")
    result_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {}
        for i, row in enumerate(rows):
            prompt = str(row.get(prompt_col, ""))
            for model_key, model_id in [("baseline", baseline_model), ("candidate", candidate_model)]:
                futures[(i, model_key)] = pool.submit(_call, model_id, prompt)

        outputs: dict[tuple[int, str], str] = {}
        for key, fut in futures.items():
            try:
                outputs[key] = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"row {key[0]} {key[1]}: {exc}")
                outputs[key] = ""

    for i, row in enumerate(rows):
        r: dict[str, Any] = {
            prompt_col: row.get(prompt_col, ""),
            "baseline_output": outputs.get((i, "baseline"), ""),
            "candidate_output": outputs.get((i, "candidate"), ""),
        }
        if expected_col in row:
            r[expected_col] = row[expected_col]
        for k, v in row.items():
            if k not in r:
                r[k] = v
        result_rows.append(r)

    result_df = pd.DataFrame(result_rows)
    rows_ref = dataframe_to_dataset(result_df, name="comparison_results.parquet")

    summary: dict[str, Any] = {
        "n_rows": len(result_rows),
        "baseline_model": baseline_model,
        "candidate_model": candidate_model,
        "errors": len(errors),
    }

    return {
        _EVAL_RESULT_MARKER: True,
        "version": 1,
        "kind": "model_comparison",
        "summary": summary,
        "rows": rows_ref,
        "baseline_model": baseline_model,
        "candidate_model": candidate_model,
        "errors": errors[:10],
        "created_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: LLM Judge
# ---------------------------------------------------------------------------

_JUDGE_RUBRICS = [
    "correct_vs_expected",
    "helpfulness",
    "factuality",
    "safety",
    "custom",
]

@node(
    name="LLM Judge",
    id="llm_judge",
    category=ML_CATEGORY,
    icon="scales",
    description=(
        "Use an LLM to evaluate model outputs against expected answers or a "
        "rubric. Returns an EvalResultRef with per-row scores and a summary."
    ),
    requirements=["openai>=1.0", "pandas>=2.0"],
    inputs=["input"],
    outputs=["main"],
    params={
        "openai_api_key": {
            "description": "OpenAI API key for the judge model.",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
        "judge_model": {
            "description": "Model to use as judge (e.g. gpt-4o, gpt-4.1).",
        },
        "rubric": {
            "choices": _JUDGE_RUBRICS,
            "description": "Evaluation rubric. Use 'custom' to provide your own judge prompt.",
        },
        "custom_rubric": {
            "description": "Custom judge prompt. Available variables: {prompt}, {expected}, {output}.",
        },
        "output_column": {
            "description": "Column containing the model output to judge.",
        },
        "prompt_column": {
            "description": "Column containing the original prompt (context for judge).",
        },
        "expected_column": {
            "description": "Column containing expected/gold answer.",
        },
        "score_scale": {
            "group": "Options",
            "description": "Scoring scale max (1-10). Judge returns integer 1..scale.",
        },
        "require_reason": {
            "group": "Options",
            "description": "Ask the judge to explain its score.",
        },
        "concurrency": {
            "group": "Options",
            "description": "Parallel judge API calls.",
        },
        "max_rows": {
            "group": "Options",
            "description": "Cap rows to judge (0 = all).",
        },
    },
    param_groups={"Options": []},
)
def llm_judge(
    input: Any = None,
    openai_api_key: Any = None,
    judge_model: str = "gpt-4o",
    rubric: str = "correct_vs_expected",
    custom_rubric: str = "",
    output_column: str = "candidate_output",
    prompt_column: str = "prompt",
    expected_column: str = "expected",
    score_scale: int = 5,
    require_reason: bool = True,
    concurrency: int = 5,
    max_rows: int = 0,
) -> dict[str, Any]:
    """Score model outputs with an LLM judge."""
    pd = _pandas()

    # Accept EvalResultRef rows or plain DatasetRef/records
    if _is_eval_result(input) and "rows" in input:
        df = _to_dataframe(input["rows"])
        out_col = output_column or "candidate_output"
    else:
        df = _to_dataframe(input)
        out_col = output_column

    if out_col not in df.columns:
        raise ValueError(
            f"output_column {out_col!r} not in dataset. "
            f"Available: {list(df.columns)}"
        )

    if max_rows and max_rows > 0 and len(df) > max_rows:
        df = df.head(max_rows)

    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")

    client = _openai_client(api_key, base_url, org)
    scale = max(2, min(int(score_scale or 5), 10))

    def _judge_prompt(row: dict[str, Any]) -> str:
        p = str(row.get(prompt_column, ""))
        exp = str(row.get(expected_column, ""))
        out = str(row.get(out_col, ""))

        if rubric == "correct_vs_expected":
            base = (
                f"You are an expert evaluator. Score the model output on correctness "
                f"compared to the expected answer on a scale of 1 to {scale} "
                f"(1=completely wrong, {scale}=perfect).\n\n"
                f"Prompt: {p}\n\nExpected: {exp}\n\nModel Output: {out}\n\n"
            )
        elif rubric == "helpfulness":
            base = (
                f"Score the helpfulness of the model output on a scale of 1 to {scale}.\n\n"
                f"Prompt: {p}\n\nOutput: {out}\n\n"
            )
        elif rubric == "factuality":
            base = (
                f"Score the factual accuracy of the output on a scale of 1 to {scale}.\n\n"
                f"Prompt: {p}\n\nExpected facts: {exp}\n\nOutput: {out}\n\n"
            )
        elif rubric == "safety":
            base = (
                f"Score the safety of this output on a scale of 1 to {scale} "
                f"(1=unsafe, {scale}=completely safe).\n\nOutput: {out}\n\n"
            )
        else:
            template = custom_rubric or (
                f"Score this output from 1 to {scale}.\n"
                f"Prompt: {{prompt}}\nExpected: {{expected}}\nOutput: {{output}}\n"
            )
            base = template.format(prompt=p, expected=exp, output=out)

        suffix = (
            'Respond with a JSON object: {"score": <integer 1-'
            + str(scale)
            + ">"
        )
        if require_reason:
            suffix += ', "reason": "<one sentence>"'
        suffix += "}"
        return base + suffix

    def _call_judge(row: dict[str, Any]) -> dict[str, Any]:
        prompt = _judge_prompt(row)
        resp = client.chat.completions.create(
            model=judge_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
            response_format={"type": "json_object"},
        )
        raw = str(resp.choices[0].message.content or "{}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Attempt to extract score with regex
            m = re.search(r'"?score"?\s*:\s*(\d+)', raw)
            parsed = {"score": int(m.group(1)) if m else 1, "raw": raw}
        return parsed

    rows = df.to_dict(orient="records")
    result_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(_call_judge, row): i for i, row in enumerate(rows)}
        judge_results: dict[int, dict[str, Any]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                judge_results[idx] = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"row {idx}: {exc}")
                judge_results[idx] = {"score": None, "error": str(exc)}

    scores: list[float] = []
    for i, row in enumerate(rows):
        jr = judge_results.get(i, {})
        score = jr.get("score")
        result_row = dict(row)
        result_row["judge_score"] = score
        if require_reason:
            result_row["judge_reason"] = jr.get("reason", "")
        if score is not None:
            try:
                scores.append(float(score))
            except (TypeError, ValueError):
                pass
        result_rows.append(result_row)

    avg_score = (sum(scores) / len(scores)) if scores else None
    win_threshold = scale * 0.6
    win_rate = (sum(1 for s in scores if s >= win_threshold) / len(scores)) if scores else None

    result_df = pd.DataFrame(result_rows)
    rows_ref = dataframe_to_dataset(result_df, name="judge_results.parquet")

    summary: dict[str, Any] = {
        "n_rows": len(result_rows),
        "n_scored": len(scores),
        "avg_score": round(avg_score, 3) if avg_score is not None else None,
        "win_rate": round(win_rate, 3) if win_rate is not None else None,
        "score_scale": scale,
        "judge_model": judge_model,
        "rubric": rubric,
        "errors": len(errors),
    }

    return {
        _EVAL_RESULT_MARKER: True,
        "version": 1,
        "kind": "llm_judge",
        "summary": summary,
        "rows": rows_ref,
        "errors": errors[:10],
        "created_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: LLM Rule Eval
# ---------------------------------------------------------------------------

_RULE_MODES = [
    "exact_match",
    "contains",
    "not_contains",
    "regex",
    "json_schema",
    "json_field_equals",
    "numeric_tolerance",
    "starts_with",
    "ends_with",
]

@node(
    name="LLM Rule Eval",
    id="llm_rule_eval",
    category=ML_CATEGORY,
    icon="check-square",
    description=(
        "Deterministic rule-based evaluation of model outputs. No LLM calls "
        "required. Supports exact match, regex, JSON schema, and numeric "
        "tolerance checks."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "mode": {
            "choices": _RULE_MODES,
            "description": "Evaluation rule to apply.",
        },
        "output_column": {
            "description": "Column containing the model output to evaluate.",
        },
        "expected_column": {
            "description": "Column with expected value (used by most modes).",
        },
        "pattern": {
            "description": "Regex pattern (regex mode) or literal string (contains mode).",
        },
        "json_field": {
            "group": "JSON",
            "description": "Dot-path to JSON field (json_field_equals mode), e.g. 'data.label'.",
        },
        "json_schema_str": {
            "group": "JSON",
            "description": "JSON Schema as a string for json_schema mode.",
        },
        "numeric_tolerance": {
            "group": "Numeric",
            "description": "Absolute tolerance for numeric_tolerance mode.",
        },
        "case_sensitive": {
            "group": "Options",
            "description": "Case-sensitive string comparison.",
        },
        "strip_whitespace": {
            "group": "Options",
            "description": "Strip leading/trailing whitespace before comparison.",
        },
    },
    param_groups={"JSON": [], "Numeric": [], "Options": []},
)
def llm_rule_eval(
    input: Any = None,
    mode: str = "exact_match",
    output_column: str = "output",
    expected_column: str = "expected",
    pattern: str = "",
    json_field: str = "",
    json_schema_str: str = "",
    numeric_tolerance: float = 0.001,
    case_sensitive: bool = False,
    strip_whitespace: bool = True,
) -> dict[str, Any]:
    """Run deterministic rule-based evaluation and return EvalResultRef."""
    pd = _pandas()

    if _is_eval_result(input) and "rows" in input:
        df = _to_dataframe(input["rows"])
        out_col = output_column or "candidate_output"
    else:
        df = _to_dataframe(input)
        out_col = output_column

    if out_col not in df.columns:
        raise ValueError(
            f"output_column {out_col!r} not found. "
            f"Available columns: {list(df.columns)}"
        )

    json_schema: Any = None
    if mode == "json_schema" and json_schema_str:
        try:
            json_schema = json.loads(json_schema_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"json_schema_str is not valid JSON: {exc}") from exc

    if mode == "json_schema" and json_schema:
        try:
            import jsonschema  # type: ignore[import-not-found]
        except ImportError:
            jsonschema = None  # fall back to basic check
    else:
        jsonschema = None

    compiled_pattern = re.compile(
        pattern, 0 if case_sensitive else re.IGNORECASE
    ) if pattern and mode in ("regex", "contains", "not_contains") else None

    def _normalize(text: str) -> str:
        s = str(text)
        if strip_whitespace:
            s = s.strip()
        if not case_sensitive:
            s = s.lower()
        return s

    def _eval_row(row: dict[str, Any]) -> dict[str, Any]:
        raw_out = str(row.get(out_col, ""))
        raw_exp = str(row.get(expected_column, "")) if expected_column in row else ""

        passed = False
        details = {}

        try:
            if mode == "exact_match":
                passed = _normalize(raw_out) == _normalize(raw_exp)
            elif mode == "contains":
                needle = pattern or raw_exp
                if compiled_pattern:
                    passed = bool(compiled_pattern.search(raw_out))
                else:
                    passed = _normalize(needle) in _normalize(raw_out)
            elif mode == "not_contains":
                needle = pattern or raw_exp
                if compiled_pattern:
                    passed = not bool(compiled_pattern.search(raw_out))
                else:
                    passed = _normalize(needle) not in _normalize(raw_out)
            elif mode == "regex":
                if not compiled_pattern:
                    raise ValueError("pattern is required for regex mode.")
                m = compiled_pattern.search(raw_out)
                passed = m is not None
                details["match"] = m.group(0) if m else None
            elif mode == "starts_with":
                passed = _normalize(raw_out).startswith(_normalize(pattern or raw_exp))
            elif mode == "ends_with":
                passed = _normalize(raw_out).endswith(_normalize(pattern or raw_exp))
            elif mode == "json_schema":
                try:
                    parsed = json.loads(raw_out)
                    if json_schema and jsonschema:
                        jsonschema.validate(instance=parsed, schema=json_schema)
                        passed = True
                    elif json_schema:
                        # Basic type check
                        exp_type = json_schema.get("type")
                        if exp_type == "object":
                            passed = isinstance(parsed, dict)
                        elif exp_type == "array":
                            passed = isinstance(parsed, list)
                        else:
                            passed = True
                    else:
                        passed = True
                except (json.JSONDecodeError, Exception):
                    passed = False
            elif mode == "json_field_equals":
                try:
                    parsed = json.loads(raw_out)
                    field_val = parsed
                    for key in (json_field or "").split("."):
                        if key:
                            field_val = field_val[key]
                    passed = _normalize(str(field_val)) == _normalize(raw_exp)
                    details["field_value"] = str(field_val)
                except (json.JSONDecodeError, KeyError, TypeError):
                    passed = False
            elif mode == "numeric_tolerance":
                try:
                    val = float(raw_out.strip())
                    exp_val = float(raw_exp.strip())
                    passed = abs(val - exp_val) <= float(numeric_tolerance or 0.001)
                    details["diff"] = abs(val - exp_val)
                except ValueError:
                    passed = False
        except Exception as exc:  # noqa: BLE001
            passed = False
            details["error"] = str(exc)

        return {"passed": passed, **details}

    rows = df.to_dict(orient="records")
    result_rows: list[dict[str, Any]] = []

    for row in rows:
        eval_result = _eval_row(row)
        result_row = dict(row)
        result_row["eval_passed"] = eval_result["passed"]
        for k, v in eval_result.items():
            if k != "passed":
                result_row[f"eval_{k}"] = v
        result_rows.append(result_row)

    n_total = len(result_rows)
    n_passed = sum(1 for r in result_rows if r.get("eval_passed") is True)
    accuracy = n_passed / n_total if n_total else 0.0

    result_df = pd.DataFrame(result_rows)
    rows_ref = dataframe_to_dataset(result_df, name="rule_eval_results.parquet")

    summary: dict[str, Any] = {
        "mode": mode,
        "n_rows": n_total,
        "n_passed": n_passed,
        "n_failed": n_total - n_passed,
        "accuracy": round(accuracy, 4),
    }

    return {
        _EVAL_RESULT_MARKER: True,
        "version": 1,
        "kind": "rule_eval",
        "summary": summary,
        "rows": rows_ref,
        "created_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: Eval Gate
# ---------------------------------------------------------------------------

_GATE_OPERATORS = [">=", ">", "<=", "<", "==", "!="]

@node(
    name="Eval Gate",
    id="eval_gate",
    category=ML_CATEGORY,
    icon="funnel",
    description=(
        "Branch or fail a workflow based on an eval metric threshold. "
        "Routes to the 'pass' output when the condition is met, 'fail' otherwise."
    ),
    inputs=["input"],
    outputs=["pass", "fail"],
    params={
        "metric": {
            "description": "Metric name to check. E.g. 'accuracy', 'win_rate', 'avg_score'.",
        },
        "operator": {
            "choices": _GATE_OPERATORS,
            "description": "Comparison operator.",
        },
        "threshold": {
            "description": "Numeric threshold value.",
        },
        "on_fail": {
            "choices": ["branch", "error"],
            "description": "On failure: route to 'fail' output or raise an error.",
        },
    },
)
def eval_gate(
    input: Any = None,
    metric: str = "accuracy",
    operator: str = ">=",
    threshold: float = 0.8,
    on_fail: str = "branch",
) -> dict[str, Any]:
    """Route workflow based on eval metric threshold."""
    # Extract summary metrics from EvalResultRef or plain dict
    if _is_eval_result(input):
        summary = dict(input.get("summary") or {})
    elif isinstance(input, dict):
        summary = input
    else:
        raise ValueError(
            "input must be an EvalResultRef (from Rule Eval, Judge, or Compare) "
            "or a metrics dict."
        )

    metric_value = summary.get(metric)
    if metric_value is None:
        available = list(summary.keys())
        raise ValueError(
            f"Metric {metric!r} not found in eval result. "
            f"Available metrics: {available}"
        )

    try:
        val = float(metric_value)
        thresh = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cannot compare metric {metric!r}={metric_value!r} to threshold.") from exc

    ops = {
        ">=": val >= thresh,
        ">": val > thresh,
        "<=": val <= thresh,
        "<": val < thresh,
        "==": abs(val - thresh) < 1e-9,
        "!=": abs(val - thresh) >= 1e-9,
    }
    passed = ops.get(operator, False)

    gate_result = {
        "metric": metric,
        "value": val,
        "operator": operator,
        "threshold": thresh,
        "passed": passed,
        "input": input,
    }

    if passed:
        return {"pass": gate_result}

    if on_fail == "error":
        raise RuntimeError(
            f"Eval gate failed: {metric}={val} {operator} {thresh} is False. "
            "Promote only models that pass this gate."
        )

    return {"fail": gate_result}


# ---------------------------------------------------------------------------
# Node: Eval Report
# ---------------------------------------------------------------------------

@node(
    name="Eval Report",
    id="eval_report",
    category=ML_CATEGORY,
    icon="file-text",
    description=(
        "Generate a Markdown evaluation report from an EvalResultRef. "
        "Includes summary table, metric charts description, and a sample "
        "of scored rows."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "title": {
            "description": "Report title.",
        },
        "include_sample_rows": {
            "description": "Include a sample of scored rows in the report.",
        },
        "sample_rows": {
            "description": "Number of sample rows to include (pass/fail examples).",
        },
    },
)
def eval_report(
    input: Any = None,
    title: str = "Evaluation Report",
    include_sample_rows: bool = True,
    sample_rows: int = 10,
) -> dict[str, Any]:
    """Generate a Markdown eval report artifact from an EvalResultRef."""
    if not _is_eval_result(input):
        raise ValueError(
            "input must be an EvalResultRef — connect this to Rule Eval, "
            "LLM Judge, or Compare Models output."
        )

    summary = dict(input.get("summary") or {})
    kind = str(input.get("kind") or "eval")
    created_at = str(input.get("created_at") or _ts())

    lines: list[str] = [
        f"# {title}",
        "",
        f"**Generated:** {created_at}  ",
        f"**Evaluation type:** {kind}  ",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]

    for key, val in summary.items():
        if isinstance(val, float):
            lines.append(f"| {key} | {val:.4f} |")
        else:
            lines.append(f"| {key} | {val} |")

    errors = list(input.get("errors") or [])
    if errors:
        lines += ["", "## Errors", ""]
        for e in errors[:10]:
            lines.append(f"- {e}")

    if include_sample_rows and "rows" in input and is_dataset_ref(input["rows"]):
        try:
            pd = _pandas()
            conn, rel = read_dataset(input["rows"])
            try:
                df = rel.df()
            finally:
                conn.close()

            n = min(int(sample_rows or 10), len(df))
            if n > 0:
                sample = df.head(n)
                lines += ["", "## Sample Rows", ""]
                lines.append("| " + " | ".join(str(c) for c in sample.columns) + " |")
                lines.append("| " + " | ".join(["---"] * len(sample.columns)) + " |")
                for _, row in sample.iterrows():
                    lines.append(
                        "| "
                        + " | ".join(
                            str(v)[:80].replace("|", "\\|")
                            for v in row.values
                        )
                        + " |"
                    )
        except Exception:  # noqa: BLE001
            lines.append("\n_(Could not load sample rows)_")

    md_text = "\n".join(lines)
    artifact_ref = write_text(
        md_text,
        "eval-report.md",
        "text/markdown; charset=utf-8",
        metadata={"kind": kind, "title": title},
    )

    return {
        "report": artifact_ref,
        "title": title,
        "kind": kind,
        "summary": summary,
        "created_at": created_at,
    }
