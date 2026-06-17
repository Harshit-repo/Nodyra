"""Synthetic data and dataset quality nodes.

These nodes help create, label, and profile training and evaluation data
without requiring GPU infrastructure. Heavy optional packages (openai,
tiktoken) are imported lazily.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from noodle.sdk import node
from noodle_nodes._creds import cred_single
from noodle_nodes.datasets import materialize_dataset, records_to_dataset

ML_CATEGORY = "Machine Learning"

_OPENAI_ERROR = (
    "openai>=1.0 is required for this node. Add it to the workflow environment "
    "and rebuild, then run again."
)
_TIKTOKEN_ERROR = (
    "tiktoken>=0.7 is required for token profiling. Add it to the workflow "
    "environment and rebuild."
)
MAX_SYNTHETIC_EXAMPLES = 1_000
MAX_LLM_ROWS = 1_000
MAX_LLM_CONCURRENCY = 20


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


def _tiktoken():
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(_TIKTOKEN_ERROR) from exc
    return tiktoken


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


def _ts() -> str:
    return datetime.now(tz=UTC).isoformat()


def _to_records(input_value: Any) -> list[dict[str, Any]]:
    from noodle.datasets import is_dataset_ref
    if is_dataset_ref(input_value):
        return materialize_dataset(input_value, cap=50_000, allow_truncate=True)
    if isinstance(input_value, list):
        return [r for r in input_value if isinstance(r, dict)]
    return []


def _bounded_concurrency(value: int, *, label: str = "concurrency") -> int:
    workers = max(1, int(value or 1))
    if workers > MAX_LLM_CONCURRENCY:
        raise ValueError(f"{label} exceeds max allowed value {MAX_LLM_CONCURRENCY}")
    return workers


def _limit_llm_rows(
    rows: list[dict[str, Any]],
    *,
    max_rows: int,
    label: str,
) -> list[dict[str, Any]]:
    requested = int(max_rows or 0)
    if requested > MAX_LLM_ROWS:
        raise ValueError(f"{label}: max_rows exceeds cap {MAX_LLM_ROWS}")
    if requested > 0:
        return rows[:requested]
    if len(rows) > MAX_LLM_ROWS:
        raise ValueError(
            f"{label}: row count {len(rows)} exceeds cap {MAX_LLM_ROWS}; "
            "set max_rows to a lower value"
        )
    return rows


# ---------------------------------------------------------------------------
# Node: Synthetic Examples Generate
# ---------------------------------------------------------------------------

@node(
    name="Synthetic Examples Generate",
    id="synthetic_examples_generate",
    category=ML_CATEGORY,
    icon="sparkles",
    description=(
        "Generate synthetic training or evaluation examples using an LLM. "
        "Accepts optional seed examples to guide variety and style. "
        "Outputs a DatasetRef of generated records."
    ),
    requirements=["openai>=1.0"],
    inputs=["input"],
    outputs=["main"],
    params={
        "openai_api_key": {
            "description": "OpenAI API key.",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
        "model": {
            "description": "Model to use for generation (e.g. gpt-4.1-mini).",
        },
        "instruction": {
            "description": (
                "What to generate. Describe the desired examples clearly. "
                "E.g. 'Generate customer support Q&A pairs about billing issues.'"
            ),
            "multiline": True,
        },
        "output_schema": {
            "description": (
                "JSON object describing the output fields and types. "
                "E.g. {\"user\": \"string\", \"assistant\": \"string\"}. "
                "Leave blank to let the model choose a schema."
            ),
            "multiline": True,
        },
        "n_examples": {
            "description": "Total number of examples to generate.",
        },
        "batch_size": {
            "group": "Options",
            "description": (
                "Examples to request per API call (1-20). Larger batches are "
                "faster but less diverse."
            ),
        },
        "temperature": {
            "group": "Options",
            "description": "Sampling temperature (0.7-1.2 for diversity, 0.2 for consistency).",
        },
        "seed_column": {
            "group": "Options",
            "description": (
                "Column from the wired input DatasetRef/records to use as "
                "few-shot seeds (leave blank to use all columns)."
            ),
        },
        "n_seed_examples": {
            "group": "Options",
            "description": "Number of seed rows to include as few-shot examples (0 = no seeds).",
        },
        "dedupe_column": {
            "group": "Options",
            "description": "Column to deduplicate on after generation (blank = no dedup).",
        },
        "system_prompt": {
            "group": "Options",
            "description": "System message to prepend. Leave blank to use the default.",
            "multiline": True,
        },
    },
    param_groups={"Options": []},
)
def synthetic_examples_generate(
    input: Any = None,
    openai_api_key: Any = None,
    model: str = "gpt-4.1-mini",
    instruction: str = "",
    output_schema: str = "",
    n_examples: int = 20,
    batch_size: int = 5,
    temperature: float = 0.9,
    seed_column: str = "",
    n_seed_examples: int = 3,
    dedupe_column: str = "",
    system_prompt: str = "",
) -> dict[str, Any]:
    """Generate synthetic examples using an LLM and return a DatasetRef."""
    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")
    if not instruction or not instruction.strip():
        raise ValueError("instruction is required — describe what examples to generate.")

    client = _openai_client(api_key, base_url, org)

    # Parse output schema
    schema_dict: dict[str, Any] = {}
    if output_schema and output_schema.strip():
        try:
            schema_dict = json.loads(output_schema.strip())
        except json.JSONDecodeError as exc:
            raise ValueError(f"output_schema is not valid JSON: {exc}") from exc

    # Collect seed examples from input
    seed_rows: list[dict[str, Any]] = []
    if input is not None and n_seed_examples and n_seed_examples > 0:
        all_rows = _to_records(input)
        if all_rows:
            seeds = all_rows[:n_seed_examples]
            if seed_column and seed_column.strip():
                seed_rows = [{seed_column: r.get(seed_column, "")} for r in seeds]
            else:
                seed_rows = seeds

    n = max(1, int(n_examples or 20))
    if n > MAX_SYNTHETIC_EXAMPLES:
        raise ValueError(
            "synthetic_examples_generate: n_examples exceeds cap "
            f"{MAX_SYNTHETIC_EXAMPLES}"
        )
    bs = max(1, min(20, int(batch_size or 5)))

    def _build_prompt(batch_n: int) -> str:
        parts = [instruction.strip()]
        if schema_dict:
            parts.append(
                f"\nReturn a JSON array of exactly {batch_n} objects. "
                f"Each object must have these fields: "
                + ", ".join(f'"{k}" ({v})' for k, v in schema_dict.items())
                + "."
            )
        else:
            parts.append(
                f"\nReturn a JSON array of exactly {batch_n} objects. "
                "Use consistent field names across all objects."
            )
        if seed_rows:
            parts.append("\nHere are example records for style/structure reference:")
            for i, r in enumerate(seed_rows[:5]):
                parts.append(f"Example {i+1}: {json.dumps(r, ensure_ascii=False)}")
        parts.append(
            "\nRespond with ONLY the JSON array, no markdown, no explanation."
        )
        return "\n".join(parts)

    sys_msg = (system_prompt or "").strip() or (
        "You are a helpful data generation assistant. "
        "Produce realistic, diverse, and useful examples."
    )

    all_results: list[dict[str, Any]] = []
    errors: list[str] = []

    batches = []
    remaining = n
    while remaining > 0:
        b = min(bs, remaining)
        batches.append(b)
        remaining -= b

    def _call_batch(batch_n: int) -> list[dict[str, Any]]:
        prompt = _build_prompt(batch_n)
        resp = client.chat.completions.create(
            model=model or "gpt-4.1-mini",
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=float(temperature or 0.9),
            max_tokens=4096,
            response_format={"type": "json_object"}
            if schema_dict
            else None,  # type: ignore[arg-type]
        )
        raw = str(resp.choices[0].message.content or "[]").strip()
        # Extract JSON array from response
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # Model may have wrapped array in a key
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v
                    break
        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
        return [r for r in parsed if isinstance(r, dict)]

    concurrency = max(1, min(5, len(batches)))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_call_batch, b): b for b in batches}
        for fut in as_completed(futures):
            try:
                rows = fut.result()
                all_results.extend(rows)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

    # Deduplicate if requested
    if dedupe_column and dedupe_column.strip() and all_results:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in all_results:
            key = str(r.get(dedupe_column, "")).strip().lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(r)
        n_before = len(all_results)
        all_results = deduped
        dupes_removed = n_before - len(all_results)
    else:
        dupes_removed = 0

    if not all_results:
        raise ValueError(
            f"No examples were generated. Errors: {errors[:3]}. "
            "Check your instruction and API key."
        )

    dataset_ref = records_to_dataset(all_results, name="synthetic_examples.parquet")

    return {
        "main": dataset_ref,
        "n_generated": len(all_results),
        "n_requested": n,
        "n_batches": len(batches),
        "dupes_removed": dupes_removed,
        "errors": errors[:5],
        "generated_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: Preference Pair Generate
# ---------------------------------------------------------------------------

@node(
    name="Preference Pair Generate",
    id="preference_pair_generate",
    category=ML_CATEGORY,
    icon="thumbs-up",
    description=(
        "Generate chosen/rejected response pairs for DPO (Direct Preference "
        "Optimization) fine-tuning. Outputs a DatasetRef with 'prompt', "
        "'chosen', and 'rejected' columns."
    ),
    requirements=["openai>=1.0"],
    inputs=["input"],
    outputs=["main"],
    params={
        "openai_api_key": {
            "description": "OpenAI API key.",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
        "prompt_column": {
            "description": "Column containing the user prompt/question.",
        },
        "chosen_model": {
            "description": "Model to generate the preferred (chosen) response.",
        },
        "rejected_model": {
            "description": (
                "Model to generate the rejected response. If blank, uses the "
                "chosen model with a 'bad response' system prompt."
            ),
        },
        "chosen_system_prompt": {
            "description": "System prompt for the chosen (good) response.",
            "multiline": True,
        },
        "rejected_system_prompt": {
            "description": (
                "System prompt for the rejected response. Leave blank to "
                "auto-generate an adversarially bad response."
            ),
            "multiline": True,
        },
        "max_rows": {
            "group": "Options",
            "description": "Maximum rows to process (0 = all).",
        },
        "concurrency": {
            "group": "Options",
            "description": "Parallel API calls.",
        },
        "temperature": {
            "group": "Options",
            "description": "Sampling temperature.",
        },
        "max_tokens": {
            "group": "Options",
            "description": "Max tokens per response.",
        },
    },
    param_groups={"Options": []},
)
def preference_pair_generate(
    input: Any = None,
    openai_api_key: Any = None,
    prompt_column: str = "prompt",
    chosen_model: str = "gpt-4.1",
    rejected_model: str = "",
    chosen_system_prompt: str = "",
    rejected_system_prompt: str = "",
    max_rows: int = 100,
    concurrency: int = 5,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """Generate preference pairs for DPO training."""
    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")

    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records with prompts.")

    p_col = (prompt_column or "prompt").strip()
    if rows and p_col not in rows[0]:
        available = list(rows[0].keys())
        raise ValueError(
            f"prompt_column {p_col!r} not found. Available: {available}"
        )

    rows = _limit_llm_rows(rows, max_rows=int(max_rows or 0), label="preference_pair_generate")

    client = _openai_client(api_key, base_url, org)

    good_sys = (chosen_system_prompt or "").strip() or (
        "You are a helpful, accurate, and thoughtful assistant."
    )
    bad_sys = (rejected_system_prompt or "").strip() or (
        "You are a poorly performing assistant. Respond in a way that is "
        "unhelpful, vague, off-topic, or incorrect. Do not be outright harmful, "
        "just clearly worse than a good assistant."
    )
    rej_model = (rejected_model or "").strip() or (chosen_model or "gpt-4.1-mini")

    def _generate(prompt: str, model: str, sys_msg: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=float(temperature or 0.7),
            max_tokens=int(max_tokens or 512),
        )
        return str(resp.choices[0].message.content or "").strip()

    result_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    def _process_row(row: dict[str, Any]) -> dict[str, Any] | None:
        prompt = str(row.get(p_col, "")).strip()
        if not prompt:
            return None
        try:
            chosen = _generate(prompt, chosen_model or "gpt-4.1-mini", good_sys)
            rejected = _generate(prompt, rej_model, bad_sys)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            return None
        result = dict(row)
        result["prompt"] = prompt
        result["chosen"] = chosen
        result["rejected"] = rejected
        return result

    worker_count = _bounded_concurrency(concurrency)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_process_row, row): i for i, row in enumerate(rows)}
        indexed: dict[int, dict[str, Any] | None] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                indexed[idx] = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"row {idx}: {exc}")
                indexed[idx] = None

    result_rows = [indexed[i] for i in sorted(indexed) if indexed[i] is not None]

    if not result_rows:
        raise ValueError(
            f"No preference pairs were generated. Errors: {errors[:3]}."
        )

    dataset_ref = records_to_dataset(result_rows, name="preference_pairs.parquet")

    return {
        "main": dataset_ref,
        "n_pairs": len(result_rows),
        "n_input": len(rows),
        "errors": errors[:5],
        "created_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: Weak Label
# ---------------------------------------------------------------------------

_WEAK_LABEL_MODES = [
    "llm_classify",
    "rule_exact",
    "rule_contains",
    "rule_regex",
    "rule_keyword_vote",
]

@node(
    name="Weak Label",
    id="weak_label",
    category=ML_CATEGORY,
    icon="tag",
    description=(
        "Automatically label rows using LLM classification or rule-based "
        "matching. Useful for building training data from unlabeled corpora. "
        "Rule modes (rule_exact, rule_contains, rule_regex, rule_keyword_vote) "
        "require no extra packages. llm_classify mode requires openai>=1.0."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "mode": {
            "choices": _WEAK_LABEL_MODES,
            "description": "Labeling strategy.",
        },
        "text_column": {
            "description": "Column containing the text to classify.",
        },
        "labels": {
            "description": "Comma-separated list of possible labels.",
        },
        "output_column": {
            "description": "Column name for the predicted label.",
        },
        "openai_api_key": {
            "group": "LLM",
            "description": "OpenAI API key (required for llm_classify mode).",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
        "model": {
            "group": "LLM",
            "description": "LLM to use for classification.",
        },
        "label_instruction": {
            "group": "LLM",
            "description": (
                "Instructions for the LLM classifier. Include guidance on "
                "label semantics, ambiguity, and edge cases."
            ),
            "multiline": True,
        },
        "add_confidence": {
            "group": "LLM",
            "description": "Ask the LLM to output a confidence score (0.0-1.0).",
        },
        "concurrency": {
            "group": "LLM",
            "description": "Parallel LLM calls for llm_classify mode.",
        },
        "rule_patterns": {
            "group": "Rules",
            "description": (
                "JSON object mapping labels to patterns. "
                "E.g. {\"billing\": [\"invoice\", \"payment\"], \"tech\": [\"bug\", \"error\"]}. "
                "Used by rule_exact, rule_contains, and rule_regex modes."
            ),
            "multiline": True,
        },
        "default_label": {
            "group": "Rules",
            "description": "Label to assign when no rule matches (blank = leave unlabeled).",
        },
        "case_sensitive": {
            "group": "Rules",
            "description": "Case-sensitive pattern matching for rule modes.",
        },
    },
    param_groups={"LLM": [], "Rules": []},
)
def weak_label(
    input: Any = None,
    mode: str = "llm_classify",
    text_column: str = "text",
    labels: str = "",
    output_column: str = "label",
    openai_api_key: Any = None,
    model: str = "gpt-4.1-mini",
    label_instruction: str = "",
    add_confidence: bool = True,
    concurrency: int = 5,
    rule_patterns: str = "",
    default_label: str = "",
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Apply weak labels to a dataset using LLM or rules."""
    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records.")

    t_col = (text_column or "text").strip()
    if rows and t_col not in rows[0]:
        available = list(rows[0].keys())
        raise ValueError(f"text_column {t_col!r} not found. Available: {available}")

    label_list = [label.strip() for label in (labels or "").split(",") if label.strip()]
    out_col = (output_column or "label").strip()

    # ------- LLM classify -------
    if mode == "llm_classify":
        api_key, base_url, org = _extract_api_key(openai_api_key)
        if not api_key:
            raise ValueError("openai_api_key is required for llm_classify mode.")
        if not label_list:
            raise ValueError("labels is required for llm_classify mode.")

        client = _openai_client(api_key, base_url, org)
        rows = _limit_llm_rows(rows, max_rows=0, label="weak_label")
        label_enum = ", ".join(f'"{label}"' for label in label_list)
        instr = (label_instruction or "").strip() or (
            f"Classify the text into exactly one of these labels: [{label_enum}]. "
            "Choose the best label based on the content."
        )

        conf_schema = ""
        if add_confidence:
            conf_schema = ', "confidence": <float 0.0-1.0>'

        sys_msg = (
            f"You are a text classifier. {instr}\n\n"
            f'Respond with JSON: {{"label": <one of {label_enum}>{conf_schema}}}'
        )

        def _classify(row: dict[str, Any]) -> dict[str, Any]:
            text = str(row.get(t_col, "")).strip()
            if not text:
                return {out_col: default_label or "", "label_confidence": None}
            resp = client.chat.completions.create(
                model=model or "gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=64,
                response_format={"type": "json_object"},
            )
            raw = str(resp.choices[0].message.content or "{}")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r'"label"\s*:\s*"([^"]+)"', raw)
                parsed = {"label": m.group(1) if m else (default_label or "")}
            lbl = str(parsed.get("label") or default_label or "").strip()
            if label_list and lbl not in label_list:
                lbl = default_label or ""
            conf = parsed.get("confidence")
            result = {out_col: lbl}
            if add_confidence:
                result["label_confidence"] = float(conf) if conf is not None else None
            return result

        errors: list[str] = []
        labeled: dict[int, dict[str, Any]] = {}
        worker_count = _bounded_concurrency(concurrency)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(_classify, row): i for i, row in enumerate(rows)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    labeled[idx] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"row {idx}: {exc}")
                    labeled[idx] = {out_col: default_label or ""}

        result_rows = []
        label_counts: dict[str, int] = {}
        for i, row in enumerate(rows):
            r = dict(row)
            r.update(labeled.get(i, {out_col: default_label or ""}))
            result_rows.append(r)
            lbl_val = str(r.get(out_col, "") or "")
            label_counts[lbl_val] = label_counts.get(lbl_val, 0) + 1

    # ------- Rule modes -------
    else:
        patterns_dict: dict[str, list[str]] = {}
        if rule_patterns and rule_patterns.strip():
            try:
                patterns_dict = json.loads(rule_patterns.strip())
            except json.JSONDecodeError as exc:
                raise ValueError(f"rule_patterns is not valid JSON: {exc}") from exc

        if not patterns_dict:
            raise ValueError("rule_patterns is required for rule modes.")

        # Pre-compile regexes for regex mode
        compiled: dict[str, list[re.Pattern[str]]] = {}
        if mode in ("rule_regex", "rule_contains"):
            flags = 0 if case_sensitive else re.IGNORECASE
            for lbl, pats in patterns_dict.items():
                compiled[lbl] = [re.compile(p, flags) for p in (pats or [])]

        def _rule_label(text: str) -> str:
            t = text if case_sensitive else text.lower()
            if mode == "rule_exact":
                for lbl, pats in patterns_dict.items():
                    for p in pats:
                        if t == (p if case_sensitive else p.lower()):
                            return lbl
            elif mode == "rule_contains":
                for lbl, rxs in compiled.items():
                    for rx in rxs:
                        if rx.search(text):
                            return lbl
            elif mode == "rule_regex":
                for lbl, rxs in compiled.items():
                    for rx in rxs:
                        if rx.search(text):
                            return lbl
            elif mode == "rule_keyword_vote":
                votes: dict[str, int] = {}
                for lbl, pats in patterns_dict.items():
                    for p in pats:
                        keyword = p if case_sensitive else p.lower()
                        if keyword in t:
                            votes[lbl] = votes.get(lbl, 0) + 1
                if votes:
                    return max(votes, key=lambda k: votes[k])
            return default_label or ""

        errors = []
        result_rows = []
        label_counts = {}
        for row in rows:
            r = dict(row)
            text = str(row.get(t_col, "")).strip()
            lbl = _rule_label(text)
            r[out_col] = lbl
            result_rows.append(r)
            label_counts[lbl] = label_counts.get(lbl, 0) + 1

    dataset_ref = records_to_dataset(result_rows, name="weak_labels.parquet")

    labeled_count = sum(1 for r in result_rows if r.get(out_col))
    return {
        "main": dataset_ref,
        "n_labeled": labeled_count,
        "n_unlabeled": len(result_rows) - labeled_count,
        "label_distribution": label_counts,
        "errors": errors[:5] if errors else [],
        "created_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: Token Profile
# ---------------------------------------------------------------------------

@node(
    name="Token Profile",
    id="token_profile",
    category=ML_CATEGORY,
    icon="calculator",
    description=(
        "Profile token counts and estimate costs across dataset rows using "
        "tiktoken. Useful for estimating fine-tuning costs and finding "
        "oversized examples before uploading."
    ),
    requirements=["tiktoken>=0.7"],
    inputs=["input"],
    outputs=["main"],
    params={
        "text_columns": {
            "description": "Comma-separated column names to concatenate for token counting.",
            "placeholder": "prompt, completion",
        },
        "encoding": {
            "description": "Tiktoken encoding name (e.g. cl100k_base, o200k_base).",
        },
        "model": {
            "description": (
                "If set, auto-select encoding from model name "
                "(overrides encoding param)."
            ),
            "placeholder": "gpt-4o",
        },
        "add_token_column": {
            "description": "Add a 'token_count' column to each row in the output dataset.",
        },
        "price_per_1k_tokens": {
            "group": "Cost",
            "description": "Estimated cost per 1000 tokens (for cost estimation only). 0 = skip.",
        },
        "max_tokens_warning": {
            "group": "Options",
            "description": "Warn about rows exceeding this token count (0 = no warning).",
        },
    },
    param_groups={"Cost": [], "Options": []},
)
def token_profile(
    input: Any = None,
    text_columns: str = "",
    encoding: str = "cl100k_base",
    model: str = "",
    add_token_column: bool = True,
    price_per_1k_tokens: float = 0.0,
    max_tokens_warning: int = 4096,
) -> dict[str, Any]:
    """Profile token counts across dataset rows."""
    tt = _tiktoken()

    # Resolve encoding
    try:
        if model and model.strip():
            enc = tt.encoding_for_model(model.strip())
        else:
            enc = tt.get_encoding((encoding or "cl100k_base").strip())
    except Exception as exc:
        raise ValueError(f"Cannot load tiktoken encoding: {exc}") from exc

    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records.")

    cols = [c.strip() for c in (text_columns or "").split(",") if c.strip()]
    if not cols and rows:
        # Auto-select string columns
        sample = rows[0]
        cols = [k for k, v in sample.items() if isinstance(v, str)]
    if not cols:
        raise ValueError(
            "text_columns is required — specify comma-separated column names to profile."
        )

    missing = [c for c in cols if c not in rows[0]]
    if missing:
        available = list(rows[0].keys())
        raise ValueError(f"Columns {missing} not found. Available: {available}")

    token_counts: list[int] = []
    oversized: list[int] = []
    result_rows: list[dict[str, Any]] = []
    warn_thresh = int(max_tokens_warning or 0)

    for i, row in enumerate(rows):
        text = " ".join(str(row.get(c, "") or "") for c in cols)
        n_tokens = len(enc.encode(text, disallowed_special=()))
        token_counts.append(n_tokens)
        if warn_thresh > 0 and n_tokens > warn_thresh:
            oversized.append(i)
        if add_token_column:
            r = dict(row)
            r["token_count"] = n_tokens
            result_rows.append(r)
        else:
            result_rows.append(dict(row))

    n = len(token_counts)
    total = sum(token_counts)
    mean_tokens = round(total / n, 1) if n else 0.0
    min_tokens = min(token_counts) if token_counts else 0
    max_tokens = max(token_counts) if token_counts else 0
    p50 = sorted(token_counts)[n // 2] if token_counts else 0
    p95 = sorted(token_counts)[int(n * 0.95)] if token_counts else 0

    cost_estimate: float | None = None
    if price_per_1k_tokens and price_per_1k_tokens > 0:
        cost_estimate = round(total * price_per_1k_tokens / 1000, 4)

    dataset_ref = records_to_dataset(result_rows, name="token_profile.parquet")

    summary: dict[str, Any] = {
        "n_rows": n,
        "total_tokens": total,
        "mean_tokens": mean_tokens,
        "min_tokens": min_tokens,
        "max_tokens": max_tokens,
        "p50_tokens": p50,
        "p95_tokens": p95,
        "n_oversized": len(oversized),
        "encoding": enc.name,
        "columns_profiled": cols,
    }
    if cost_estimate is not None:
        summary["estimated_cost_usd"] = cost_estimate

    return {
        "main": dataset_ref,
        **summary,
        "created_at": _ts(),
    }
