"""LLM fine-tuning nodes — dataset prep, provider jobs, model registration.

Heavy dependencies (openai, pandas, tiktoken) are imported lazily so this
module can be enumerated in a base environment without ML packages installed.

Nodes follow a job-oriented split:
  prepare -> upload -> create_job -> status -> collect -> evaluate -> register

This avoids blocking long-running provider training inside a single node.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

from noodle.artifacts import is_artifact_ref, read_bytes, write_bytes
from noodle.datasets import is_dataset_ref
from noodle.sdk import node
from noodle_nodes._creds import cred_single
from noodle_nodes.datasets import materialize_dataset

# ---------------------------------------------------------------------------
# Markers / reference envelopes
# ---------------------------------------------------------------------------

_FT_DATASET_MARKER = "__noodle_finetune_dataset__"
_FT_JOB_MARKER = "__noodle_finetune_job__"
_MODEL_ARTIFACT_MARKER = "__noodle_model_artifact__"
_MODEL_REGISTRY_MARKER = "__noodle_model_registry__"

ML_CATEGORY = "Machine Learning"
MAX_FINE_TUNE_EXAMPLES = 100_000
OPENAI_FINE_TUNE_MODELS = [
    "gpt-4.1-mini",
    "gpt-4o-mini-2024-07-18",
    "gpt-4o-2024-08-06",
    "gpt-3.5-turbo-0125",
    "davinci-002",
    "babbage-002",
]
FINE_TUNE_FORMATS = ["openai_chat_jsonl", "prompt_completion_jsonl"]


def _is_ft_dataset(value: Any) -> bool:
    return isinstance(value, dict) and value.get(_FT_DATASET_MARKER) is True


def _is_ft_job(value: Any) -> bool:
    return isinstance(value, dict) and value.get(_FT_JOB_MARKER) is True


def _is_model_registry(value: Any) -> bool:
    return isinstance(value, dict) and value.get(_MODEL_REGISTRY_MARKER) is True


# ---------------------------------------------------------------------------
# Lazy import helpers
# ---------------------------------------------------------------------------

_TIKTOKEN_ERROR = (
    "tiktoken is required for token counting. Add tiktoken>=0.7 to the "
    "workflow environment and rebuild it."
)
_OPENAI_ERROR = (
    "openai is required for OpenAI fine-tuning nodes. Add openai>=1.0 to the "
    "workflow environment and rebuild it, then run again."
)


def _tiktoken():
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(_TIKTOKEN_ERROR) from exc
    return tiktoken


def _openai_client(api_key: str, base_url: str | None = None, organization: str | None = None):
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(_OPENAI_ERROR) from exc
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if organization:
        kwargs["organization"] = organization
    return OpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _example_cap(max_examples: int | None) -> int:
    cap = max(1, int(max_examples or MAX_FINE_TUNE_EXAMPLES))
    if cap > MAX_FINE_TUNE_EXAMPLES:
        raise ValueError(
            f"llm_fine_tune_dataset: max_examples must be <= "
            f"{MAX_FINE_TUNE_EXAMPLES}."
        )
    return cap


def _to_records(input_value: Any, *, max_examples: int | None) -> list[dict[str, Any]]:
    """Return input as a list of plain dicts without requiring pandas."""
    cap = _example_cap(max_examples)
    if is_dataset_ref(input_value):
        return materialize_dataset(input_value, cap=cap, allow_truncate=False)
    if isinstance(input_value, list):
        if len(input_value) > cap:
            raise ValueError(
                f"llm_fine_tune_dataset received {len(input_value)} rows but "
                f"max_examples is {cap}."
            )
        return [r for r in input_value if isinstance(r, dict)]
    raise ValueError(
        "input must be a DatasetRef or a list of records — add a Records To "
        "Dataset or CSV Parse node upstream to produce one."
    )


def _estimate_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """Silently fall back to character estimate if tiktoken is absent."""
    try:
        tt = _tiktoken()
        enc = tt.get_encoding(encoding_name)
        return len(enc.encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001 — tiktoken is optional
        return max(1, len(text) // 4)


def _extract_api_key(credentials: Any) -> tuple[str, str | None, str | None]:
    """Return (api_key, base_url, organization) from a cred or raw key."""
    if isinstance(credentials, dict):
        key = str(credentials.get("api_key") or "").strip()
        base = str(credentials.get("base_url") or "").strip() or None
        org = str(credentials.get("organization") or "").strip() or None
        return key, base, org
    if isinstance(credentials, str):
        return credentials.strip(), None, None
    raise ValueError("openai_api_key must be an API key string or credential dict.")


# ---------------------------------------------------------------------------
# Node: LLM Fine-Tune Dataset
# ---------------------------------------------------------------------------

@node(
    name="LLM Fine-Tune Dataset",
    id="llm_fine_tune_dataset",
    category=ML_CATEGORY,
    icon="database",
    description=(
        "Convert a dataset or records into provider-ready fine-tuning JSONL. "
        "Validates format, deduplicates, estimates token costs, and outputs a "
        "FineTuneDatasetRef artifact."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "format": {
            "choices": FINE_TUNE_FORMATS,
            "description": (
                "JSONL format to produce. Use openai_chat_jsonl for "
                "ChatCompletion models."
            ),
        },
        "messages_column": {
            "description": "Column holding pre-formatted messages list (list of {role, content}). "
                           "Used when format=openai_chat_jsonl and data is already structured.",
        },
        "system_column": {
            "description": "Column for the system message. Combined with user/assistant columns.",
        },
        "user_column": {
            "description": "Column holding the user/human turn.",
        },
        "assistant_column": {
            "description": "Column holding the assistant/model answer.",
        },
        "prompt_column": {
            "group": "Prompt-Completion",
            "description": "Column for the prompt (prompt_completion_jsonl format only).",
        },
        "completion_column": {
            "group": "Prompt-Completion",
            "description": "Column for the completion (prompt_completion_jsonl format only).",
        },
        "min_examples": {
            "group": "Validation",
            "description": "Minimum rows required. Raises an error if dataset is smaller.",
        },
        "max_examples": {
            "group": "Validation",
            "description": (
                "Maximum input rows to process before producing JSONL "
                f"(hard cap {MAX_FINE_TUNE_EXAMPLES})."
            ),
        },
        "max_tokens_per_example": {
            "group": "Validation",
            "description": "Warn if any example exceeds this token count (0 = no check).",
        },
        "dedupe": {
            "group": "Options",
            "description": "Remove duplicate rows before conversion.",
        },
        "validation_split": {
            "group": "Options",
            "description": "Fraction held out for validation (0 = no validation file).",
        },
    },
    param_groups={"Validation": [], "Options": [], "Prompt-Completion": []},
)
def llm_fine_tune_dataset(
    input: Any = None,
    format: str = "openai_chat_jsonl",
    messages_column: str = "",
    system_column: str = "",
    user_column: str = "user",
    assistant_column: str = "assistant",
    prompt_column: str = "prompt",
    completion_column: str = "completion",
    min_examples: int = 10,
    max_examples: int = MAX_FINE_TUNE_EXAMPLES,
    max_tokens_per_example: int = 4096,
    dedupe: bool = True,
    validation_split: float = 0.0,
) -> dict[str, Any]:
    """Convert rows into provider-ready JSONL and return a FineTuneDatasetRef."""
    raw_rows = _to_records(input, max_examples=max_examples)

    if dedupe:
        before = len(raw_rows)
        seen: set[tuple[Any, ...]] = set()
        deduped: list[dict[str, Any]] = []
        for r in raw_rows:
            key = tuple(sorted((k, str(v)) for k, v in r.items()))
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        raw_rows = deduped
        dropped = before - len(raw_rows)
    else:
        dropped = 0

    n_total = len(raw_rows)
    if n_total < max(1, min_examples):
        raise ValueError(
            f"Dataset has {n_total} rows but min_examples={min_examples}. "
            "Add more training examples or lower min_examples."
        )

    errors: list[str] = []
    warnings: list[str] = []

    def _build_chat_messages(row: dict[str, Any]) -> list[dict[str, str]]:
        if messages_column and messages_column in row and row[messages_column]:
            raw = row[messages_column]
            if isinstance(raw, str):
                raw = json.loads(raw)
            return list(raw)
        msgs: list[dict[str, str]] = []
        sys_text = str(row.get(system_column, "") if system_column else "").strip()
        if sys_text:
            msgs.append({"role": "system", "content": sys_text})
        u_col = user_column or "user"
        a_col = assistant_column or "assistant"
        if u_col not in row or a_col not in row:
            raise ValueError(
                f"Columns {u_col!r} and {a_col!r} are required. "
                f"Available columns: {list(row.keys())}"
            )
        user_text = str(row.get(u_col, "")).strip()
        asst_text = str(row.get(a_col, "")).strip()
        if not user_text or not asst_text:
            return []  # skip blank rows
        msgs.append({"role": "user", "content": user_text})
        msgs.append({"role": "assistant", "content": asst_text})
        return msgs

    def _build_pc(row: dict[str, Any]) -> dict[str, str] | None:
        p = str(row.get(prompt_column or "prompt", "")).strip()
        c = str(row.get(completion_column or "completion", "")).strip()
        if not p or not c:
            return None
        return {"prompt": p, "completion": c}

    jsonl_lines: list[str] = []
    total_tokens = 0
    token_warnings = 0

    for row in raw_rows:
        try:
            if format == "openai_chat_jsonl":
                msgs = _build_chat_messages(row)
                if not msgs:
                    continue
                example = {"messages": msgs}
                text_for_tokens = " ".join(m["content"] for m in msgs)
            else:
                pc = _build_pc(row)
                if pc is None:
                    continue
                example = pc
                text_for_tokens = pc["prompt"] + pc["completion"]
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
            continue

        tok = _estimate_tokens(text_for_tokens)
        total_tokens += tok
        if max_tokens_per_example > 0 and tok > max_tokens_per_example:
            token_warnings += 1

        jsonl_lines.append(json.dumps(example, ensure_ascii=False))

    if errors:
        joined = "; ".join(errors[:3])
        raise ValueError(f"Dataset conversion errors: {joined}")

    if token_warnings > 0:
        warnings.append(
            f"{token_warnings} examples exceed max_tokens_per_example={max_tokens_per_example}."
        )

    n_examples = len(jsonl_lines)
    if n_examples < max(1, min_examples):
        raise ValueError(
            f"Only {n_examples} valid examples produced (min_examples={min_examples})."
        )

    # Split into train + optional validation
    n_val = int(n_examples * min(max(float(validation_split or 0), 0.0), 0.5))
    n_train = n_examples - n_val
    train_lines = jsonl_lines[:n_train]
    val_lines = jsonl_lines[n_train:]

    train_bytes = ("\n".join(train_lines) + "\n").encode("utf-8")
    train_ref = write_bytes(
        train_bytes,
        "finetune-train.jsonl",
        "application/x-jsonlines",
        kind="finetune_jsonl",
        metadata={"format": format, "n_examples": n_train},
        preview=train_lines[:3],
    )

    val_ref: dict[str, Any] | None = None
    if val_lines:
        val_bytes = ("\n".join(val_lines) + "\n").encode("utf-8")
        val_ref = write_bytes(
            val_bytes,
            "finetune-val.jsonl",
            "application/x-jsonlines",
            kind="finetune_jsonl",
            metadata={"format": format, "n_examples": n_val},
            preview=val_lines[:3],
        )

    return {
        _FT_DATASET_MARKER: True,
        "version": 1,
        "format": format,
        "artifact": train_ref,
        "validation_artifact": val_ref,
        "n_examples": n_train,
        "n_validation": n_val,
        "n_dropped_duplicates": dropped,
        "token_estimate": total_tokens,
        "validation": {
            "errors": errors,
            "warnings": warnings,
        },
    }


# ---------------------------------------------------------------------------
# Node: OpenAI Upload Fine-Tune File
# ---------------------------------------------------------------------------

@node(
    name="OpenAI Upload Fine-Tune File",
    id="openai_upload_fine_tune_file",
    category=ML_CATEGORY,
    icon="cloud-arrow-up",
    description=(
        "Upload a JSONL fine-tuning file to OpenAI. Returns an OpenAI file ID "
        "that can be passed to Create Fine-Tune Job."
    ),
    requirements=["openai>=1.0"],
    inputs=["input"],
    outputs=["main"],
    params={
        "openai_api_key": {
            "description": "OpenAI API key.",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
        "purpose": {
            "choices": ["fine-tune"],
            "description": "File purpose. Must be fine-tune for fine-tuning jobs.",
        },
    },
)
def openai_upload_fine_tune_file(
    input: Any = None,
    openai_api_key: Any = None,
    purpose: str = "fine-tune",
) -> dict[str, Any]:
    """Upload a FineTuneDatasetRef or artifact to OpenAI Files API."""
    # Accept FineTuneDatasetRef or bare artifact ref
    if _is_ft_dataset(input):
        artifact_ref = input.get("artifact")
        n_examples = int(input.get("n_examples") or 0)
        fmt = str(input.get("format") or "unknown")
    elif is_artifact_ref(input):
        artifact_ref = input
        n_examples = 0
        fmt = "unknown"
    else:
        raise ValueError(
            "input must be a FineTuneDatasetRef (from LLM Fine-Tune Dataset) "
            "or a JSONL artifact ref."
        )

    if not artifact_ref or not is_artifact_ref(artifact_ref):
        raise ValueError("FineTuneDatasetRef has no valid artifact ref.")

    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")

    client = _openai_client(api_key, base_url, org)
    data = read_bytes(artifact_ref)

    file_obj = io.BytesIO(data)
    file_obj.name = "finetune.jsonl"

    response = client.files.create(file=file_obj, purpose=purpose)  # type: ignore[arg-type]

    return {
        "file_id": response.id,
        "filename": getattr(response, "filename", "finetune.jsonl"),
        "bytes": getattr(response, "bytes", len(data)),
        "purpose": getattr(response, "purpose", purpose),
        "status": getattr(response, "status", "uploaded"),
        "created_at": getattr(response, "created_at", None),
        "n_examples": n_examples,
        "format": fmt,
    }


# ---------------------------------------------------------------------------
# Node: OpenAI Create Fine-Tune Job
# ---------------------------------------------------------------------------

@node(
    name="OpenAI Create Fine-Tune Job",
    id="openai_create_fine_tune_job",
    category=ML_CATEGORY,
    icon="play-circle",
    description=(
        "Create an OpenAI fine-tuning job. Returns a FineTuneJobRef immediately "
        "without waiting for completion. Poll with OpenAI Fine-Tune Status."
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
            "choices": OPENAI_FINE_TUNE_MODELS,
            "description": "Base model to fine-tune.",
        },
        "suffix": {
            "description": "Up to 18 chars appended to the fine-tuned model name.",
        },
        "validation_file_id": {
            "description": "OpenAI file ID for the validation JSONL (optional).",
        },
        "n_epochs": {
            "group": "Hyperparameters",
            "description": "Number of training epochs (leave blank for provider auto).",
        },
        "batch_size": {
            "group": "Hyperparameters",
            "description": "Batch size (leave blank for provider auto).",
        },
        "learning_rate_multiplier": {
            "group": "Hyperparameters",
            "description": "Learning rate multiplier (leave blank for provider auto).",
        },
        "seed": {
            "group": "Hyperparameters",
            "description": "Random seed for reproducibility.",
        },
    },
    param_groups={"Hyperparameters": []},
)
def openai_create_fine_tune_job(
    input: Any = None,
    openai_api_key: Any = None,
    model: str = "gpt-4.1-mini",
    suffix: str = "",
    validation_file_id: str = "",
    n_epochs: Any = None,
    batch_size: Any = None,
    learning_rate_multiplier: Any = None,
    seed: Any = None,
) -> dict[str, Any]:
    """Create an OpenAI fine-tuning job and return a FineTuneJobRef."""
    # Resolve training file ID from upload result or direct string
    training_file_id: str = ""
    if isinstance(input, dict):
        training_file_id = str(input.get("file_id") or "").strip()
    elif isinstance(input, str):
        training_file_id = input.strip()

    if not training_file_id:
        raise ValueError(
            "input must be the result of OpenAI Upload Fine-Tune File "
            "(containing file_id), or a raw file ID string."
        )

    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")

    client = _openai_client(api_key, base_url, org)

    hyperparams: dict[str, Any] = {}
    if n_epochs is not None and str(n_epochs).strip():
        hyperparams["n_epochs"] = int(n_epochs)
    if batch_size is not None and str(batch_size).strip():
        hyperparams["batch_size"] = int(batch_size)
    if learning_rate_multiplier is not None and str(learning_rate_multiplier).strip():
        hyperparams["learning_rate_multiplier"] = float(learning_rate_multiplier)

    create_kwargs: dict[str, Any] = {
        "training_file": training_file_id,
        "model": model,
    }
    if suffix and suffix.strip():
        create_kwargs["suffix"] = suffix.strip()[:18]
    if validation_file_id and validation_file_id.strip():
        create_kwargs["validation_file"] = validation_file_id.strip()
    if hyperparams:
        create_kwargs["hyperparameters"] = hyperparams
    if seed is not None and str(seed).strip():
        create_kwargs["seed"] = int(seed)

    job = client.fine_tuning.jobs.create(**create_kwargs)

    return {
        _FT_JOB_MARKER: True,
        "version": 1,
        "provider": "openai",
        "job_id": job.id,
        "status": job.status,
        "model": model,
        "fine_tuned_model": getattr(job, "fine_tuned_model", None),
        "training_file_id": training_file_id,
        "validation_file_id": validation_file_id or None,
        "created_at": datetime.fromtimestamp(
            job.created_at, tz=UTC
        ).isoformat() if job.created_at else None,
        "hyperparameters": dict(getattr(job, "hyperparameters", None) or {}),
        "suffix": suffix or None,
    }


# ---------------------------------------------------------------------------
# Node: OpenAI Fine-Tune Status
# ---------------------------------------------------------------------------

@node(
    name="OpenAI Fine-Tune Status",
    id="openai_fine_tune_status",
    category=ML_CATEGORY,
    icon="pulse",
    description=(
        "Retrieve the current status of an OpenAI fine-tuning job. "
        "Use in a loop with a Wait/Delay node to poll until completion."
    ),
    requirements=["openai>=1.0"],
    inputs=["input"],
    outputs=["main", "succeeded", "failed", "running"],
    params={
        "openai_api_key": {
            "description": "OpenAI API key.",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
        "include_events": {
            "description": "Include the last training events in the output.",
        },
        "include_result_files": {
            "description": "Include result file IDs when the job completes.",
        },
        "fail_if_failed": {
            "description": "Raise an error if the provider reports the job as failed.",
        },
    },
)
def openai_fine_tune_status(
    input: Any = None,
    openai_api_key: Any = None,
    include_events: bool = True,
    include_result_files: bool = True,
    fail_if_failed: bool = True,
) -> dict[str, Any]:
    """Return current FineTuneJobRef with updated status and branch outputs."""
    job_id = _resolve_job_id(input)
    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")

    client = _openai_client(api_key, base_url, org)
    job = client.fine_tuning.jobs.retrieve(job_id)

    status = str(job.status or "unknown")
    fine_tuned_model = getattr(job, "fine_tuned_model", None)

    result: dict[str, Any] = {
        _FT_JOB_MARKER: True,
        "version": 1,
        "provider": "openai",
        "job_id": job.id,
        "status": status,
        "model": getattr(job, "model", None),
        "fine_tuned_model": fine_tuned_model,
        "training_file_id": getattr(job, "training_file", None),
        "validation_file_id": getattr(job, "validation_file", None),
        "created_at": _ts(getattr(job, "created_at", None)),
        "finished_at": _ts(getattr(job, "finished_at", None)),
        "estimated_finish": _ts(getattr(job, "estimated_finish", None)),
        "trained_tokens": getattr(job, "trained_tokens", None),
        "error": _fmt_error(getattr(job, "error", None)),
    }

    if include_events:
        try:
            events_page = client.fine_tuning.jobs.list_events(job_id, limit=20)
            result["events"] = [
                {
                    "created_at": _ts(e.created_at),
                    "level": e.level,
                    "message": e.message,
                }
                for e in events_page.data
            ]
        except Exception:  # noqa: BLE001
            result["events"] = []

    if include_result_files and status == "succeeded":
        result_files = list(getattr(job, "result_files", None) or [])
        result["result_files"] = result_files

    if fail_if_failed and status == "failed":
        err_msg = result.get("error") or "provider reported failure"
        raise RuntimeError(f"Fine-tuning job {job_id} failed: {err_msg}")

    # Multi-output branching
    if status == "succeeded":
        return {"succeeded": result, "main": result}
    if status == "failed":
        return {"failed": result, "main": result}
    return {"running": result, "main": result}


# ---------------------------------------------------------------------------
# Node: OpenAI Fine-Tune Checkpoints
# ---------------------------------------------------------------------------

@node(
    name="OpenAI Fine-Tune Checkpoints",
    id="openai_fine_tune_checkpoints",
    category=ML_CATEGORY,
    icon="flag",
    description="List checkpoint model IDs and step metrics for a fine-tuning job.",
    requirements=["openai>=1.0"],
    inputs=["input"],
    outputs=["main"],
    params={
        "openai_api_key": {
            "description": "OpenAI API key.",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
    },
)
def openai_fine_tune_checkpoints(
    input: Any = None,
    openai_api_key: Any = None,
) -> dict[str, Any]:
    """Return a list of checkpoint model IDs and metrics."""
    job_id = _resolve_job_id(input)
    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")

    client = _openai_client(api_key, base_url, org)

    try:
        checkpoints_page = client.fine_tuning.jobs.checkpoints.list(job_id)
        checkpoints = [
            {
                "checkpoint_id": str(getattr(cp, "id", "")),
                "step_number": getattr(cp, "step_number", None),
                "fine_tuned_model_checkpoint": getattr(cp, "fine_tuned_model_checkpoint", None),
                "metrics": dict(getattr(cp, "metrics", None) or {}),
                "created_at": _ts(getattr(cp, "created_at", None)),
            }
            for cp in checkpoints_page.data
        ]
    except Exception as exc:
        checkpoints = []
        return {"job_id": job_id, "checkpoints": checkpoints, "error": str(exc)}

    return {
        "job_id": job_id,
        "checkpoints": checkpoints,
        "n_checkpoints": len(checkpoints),
    }


# ---------------------------------------------------------------------------
# Node: OpenAI Cancel Fine-Tune Job
# ---------------------------------------------------------------------------

@node(
    name="OpenAI Cancel Fine-Tune Job",
    id="openai_cancel_fine_tune_job",
    category=ML_CATEGORY,
    icon="x-circle",
    description=(
        "Cancel a running OpenAI fine-tuning job. Safe to call on already-"
        "completed or already-cancelled jobs (idempotent)."
    ),
    requirements=["openai>=1.0"],
    inputs=["input"],
    outputs=["main"],
    params={
        "openai_api_key": {
            "description": "OpenAI API key.",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
    },
)
def openai_cancel_fine_tune_job(
    input: Any = None,
    openai_api_key: Any = None,
) -> dict[str, Any]:
    """Cancel a fine-tuning job. Idempotent — safe to retry."""
    job_id = _resolve_job_id(input)
    api_key, base_url, org = _extract_api_key(openai_api_key)
    if not api_key:
        raise ValueError("openai_api_key is required.")

    client = _openai_client(api_key, base_url, org)

    try:
        job = client.fine_tuning.jobs.cancel(job_id)
        status = str(job.status or "cancelled")
    except Exception as exc:
        # If already cancelled or completed, treat as success
        err_str = str(exc).lower()
        if "cancel" in err_str or "already" in err_str or "completed" in err_str:
            status = "cancelled"
        else:
            raise

    return {
        _FT_JOB_MARKER: True,
        "version": 1,
        "provider": "openai",
        "job_id": job_id,
        "status": status,
        "cancelled_at": datetime.now(tz=UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Node: Register Fine-Tuned Model
# ---------------------------------------------------------------------------

@node(
    name="Register Fine-Tuned Model",
    id="register_fine_tuned_model",
    category=ML_CATEGORY,
    icon="bookmarks",
    description=(
        "Register a fine-tuned model in Noodle's workflow-local model registry. "
        "Records provider, model IDs, training metadata, and eval metrics for "
        "auditing and promotion workflows."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "name": {
            "description": "Human-readable name for this model version.",
        },
        "description": {
            "description": "What this model was trained for.",
        },
        "task": {
            "choices": [
                "chat",
                "classification",
                "extraction",
                "summarization",
                "translation",
                "custom",
            ],
            "description": "Task type this model performs.",
        },
        "tags": {
            "description": "Comma-separated tags for search/filtering.",
        },
        "eval_metric_name": {
            "group": "Evaluation",
            "description": "Primary metric name (e.g. accuracy, win_rate, f1).",
        },
        "eval_metric_value": {
            "group": "Evaluation",
            "description": "Primary metric value.",
        },
    },
    param_groups={"Evaluation": []},
)
def register_fine_tuned_model(
    input: Any = None,
    name: str = "",
    description: str = "",
    task: str = "chat",
    tags: str = "",
    eval_metric_name: str = "",
    eval_metric_value: Any = None,
) -> dict[str, Any]:
    """Register a fine-tuned model and return a ModelRegistryRef artifact."""
    # Accept FineTuneJobRef or raw model ID string
    if _is_ft_job(input):
        provider = str(input.get("provider") or "openai")
        job_id = str(input.get("job_id") or "")
        model_id = str(input.get("fine_tuned_model") or "")
        base_model = str(input.get("model") or "")
        training_file_id = str(input.get("training_file_id") or "")
        status = str(input.get("status") or "unknown")
        if status not in ("succeeded",) and not model_id:
            raise ValueError(
                f"Fine-tuning job status is {status!r} and fine_tuned_model is "
                "not set. Wait for the job to succeed before registering."
            )
    elif isinstance(input, str) and input.strip():
        provider = "openai"
        job_id = ""
        model_id = input.strip()
        base_model = ""
        training_file_id = ""
    else:
        raise ValueError(
            "input must be a FineTuneJobRef (from Fine-Tune Status) or a model "
            "ID string."
        )

    if not model_id:
        raise ValueError(
            "fine_tuned_model is empty — the job may not have completed yet."
        )

    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    metrics: dict[str, Any] = {}
    if eval_metric_name and eval_metric_name.strip():
        try:
            metrics[eval_metric_name.strip()] = float(str(eval_metric_value or 0))
        except (ValueError, TypeError):
            metrics[eval_metric_name.strip()] = eval_metric_value

    registry_entry: dict[str, Any] = {
        _MODEL_REGISTRY_MARKER: True,
        "version": 1,
        "provider": provider,
        "model_id": model_id,
        "base_model": base_model,
        "job_id": job_id,
        "training_file_id": training_file_id,
        "name": name or model_id,
        "description": description,
        "task": task,
        "tags": tag_list,
        "metrics": metrics,
        "status": "registered",
        "registered_at": datetime.now(tz=UTC).isoformat(),
    }

    # Persist as a JSON artifact for auditing
    artifact_ref = write_bytes(
        json.dumps(registry_entry, indent=2, ensure_ascii=False).encode("utf-8"),
        f"model-registry-{model_id[:40]}.json",
        "application/json",
        kind="model_registry",
        metadata={"model_id": model_id, "provider": provider},
        preview=registry_entry,
    )
    registry_entry["artifact"] = artifact_ref

    return registry_entry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_job_id(input_value: Any) -> str:
    """Extract job ID from a FineTuneJobRef or raw string."""
    if _is_ft_job(input_value):
        job_id = str(input_value.get("job_id") or "").strip()
    elif isinstance(input_value, str):
        job_id = input_value.strip()
    else:
        raise ValueError(
            "input must be a FineTuneJobRef (from Create or Status node) or "
            "a raw job ID string."
        )
    if not job_id:
        raise ValueError("job_id is empty.")
    return job_id


def _ts(value: Any) -> str | None:
    """Convert a Unix timestamp or None to ISO string."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)


def _fmt_error(error: Any) -> str | None:
    if error is None:
        return None
    if isinstance(error, str):
        return error or None
    if isinstance(error, dict):
        return str(error.get("message") or error) or None
    if hasattr(error, "message"):
        return str(error.message) or None
    return str(error) or None
