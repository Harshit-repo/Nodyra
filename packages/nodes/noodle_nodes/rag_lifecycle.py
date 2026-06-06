"""RAG (Retrieval-Augmented Generation) lifecycle nodes.

These nodes help build, evaluate, and maintain production RAG systems.
Heavy optional packages (openai, tiktoken) are imported lazily.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from noodle.artifacts import write_text
from noodle.sdk import node
from noodle_nodes._creds import cred_single
from noodle_nodes.datasets import materialize_dataset, records_to_dataset

ML_CATEGORY = "Machine Learning"

_OPENAI_ERROR = (
    "openai>=1.0 is required for this node. Add it to the workflow environment "
    "and rebuild, then run again."
)
_TIKTOKEN_ERROR = (
    "tiktoken>=0.7 is required for this node. Add it to the workflow environment "
    "and rebuild."
)


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
        return materialize_dataset(input_value, cap=100_000, allow_truncate=True)
    if isinstance(input_value, list):
        return [r for r in input_value if isinstance(r, dict)]
    return []


def _count_tokens(text: str, enc: Any) -> int:
    try:
        return len(enc.encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Node: Document Chunk
# ---------------------------------------------------------------------------

_CHUNK_STRATEGIES = [
    "fixed_size",
    "sentence",
    "paragraph",
    "recursive",
    "semantic_boundary",
]

@node(
    name="Document Chunk",
    id="document_chunk",
    category=ML_CATEGORY,
    icon="scissors",
    description=(
        "Split documents into chunks for vector indexing. Supports fixed-size, "
        "sentence, paragraph, and recursive splitting strategies. "
        "Outputs a DatasetRef of chunks with metadata."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "text_column": {
            "description": "Column containing the document text to split.",
        },
        "strategy": {
            "choices": _CHUNK_STRATEGIES,
            "description": "Chunking strategy.",
        },
        "chunk_size": {
            "description": "Target chunk size in characters (fixed_size, recursive) or tokens (if use_tokens=true).",
        },
        "chunk_overlap": {
            "description": "Number of characters (or tokens) to overlap between chunks.",
        },
        "use_tokens": {
            "description": "Measure chunk size in tokens instead of characters (requires tiktoken).",
        },
        "token_encoding": {
            "group": "Tokens",
            "description": "Tiktoken encoding to use for token counting (e.g. cl100k_base).",
        },
        "min_chunk_size": {
            "group": "Options",
            "description": "Drop chunks shorter than this many characters.",
        },
        "include_metadata": {
            "group": "Options",
            "description": "Carry all non-text columns through to each chunk row.",
        },
        "chunk_index_column": {
            "group": "Options",
            "description": "Name of the column to store the chunk index within its source document.",
        },
    },
    param_groups={"Tokens": [], "Options": []},
)
def document_chunk(
    input: Any = None,
    text_column: str = "text",
    strategy: str = "fixed_size",
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    use_tokens: bool = False,
    token_encoding: str = "cl100k_base",
    min_chunk_size: int = 50,
    include_metadata: bool = True,
    chunk_index_column: str = "chunk_index",
) -> dict[str, Any]:
    """Split document rows into chunks for RAG indexing."""
    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of document records.")

    t_col = (text_column or "text").strip()
    if rows and t_col not in rows[0]:
        available = list(rows[0].keys())
        raise ValueError(f"text_column {t_col!r} not found. Available: {available}")

    enc = None
    if use_tokens:
        tt = _tiktoken()
        try:
            enc = tt.get_encoding((token_encoding or "cl100k_base").strip())
        except Exception as exc:
            raise ValueError(f"Cannot load tiktoken encoding: {exc}") from exc

    def _measure(text: str) -> int:
        if enc:
            return _count_tokens(text, enc)
        return len(text)

    def _split_fixed(text: str) -> list[str]:
        size = max(1, int(chunk_size or 512))
        overlap = max(0, min(int(chunk_overlap or 64), size - 1))
        step = size - overlap
        chunks = []
        start = 0
        while start < len(text):
            end = start + size
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start += step
            if start >= len(text):
                break
        return chunks

    def _split_paragraph(text: str) -> list[str]:
        paras = re.split(r"\n\s*\n", text)
        result: list[str] = []
        current = ""
        size = max(1, int(chunk_size or 512))
        overlap = max(0, min(int(chunk_overlap or 64), size - 1))
        for para in paras:
            para = para.strip()
            if not para:
                continue
            if _measure(current + "\n\n" + para) <= size:
                current = (current + "\n\n" + para).strip() if current else para
            else:
                if current:
                    result.append(current)
                    # Add overlap from end of previous chunk
                    overlap_text = current[-overlap:] if overlap else ""
                    current = (overlap_text + " " + para).strip() if overlap_text else para
                else:
                    current = para
        if current:
            result.append(current)
        return result

    def _split_sentence(text: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        result: list[str] = []
        current = ""
        size = max(1, int(chunk_size or 512))
        overlap = max(0, min(int(chunk_overlap or 64), size - 1))
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            candidate = (current + " " + sent).strip() if current else sent
            if _measure(candidate) <= size:
                current = candidate
            else:
                if current:
                    result.append(current)
                    overlap_text = current[-overlap:] if overlap else ""
                    current = (overlap_text + " " + sent).strip() if overlap_text else sent
                else:
                    result.append(sent)
                    current = ""
        if current:
            result.append(current)
        return result

    def _split_recursive(text: str, separators: list[str] | None = None) -> list[str]:
        if separators is None:
            separators = ["\n\n", "\n", ". ", " ", ""]
        size = max(1, int(chunk_size or 512))
        overlap = max(0, min(int(chunk_overlap or 64), size - 1))
        if _measure(text) <= size:
            return [text.strip()] if text.strip() else []
        for sep in separators:
            if sep and sep in text:
                parts = text.split(sep)
                chunks: list[str] = []
                current = ""
                for part in parts:
                    candidate = (current + sep + part).strip() if current else part.strip()
                    if _measure(candidate) <= size:
                        current = candidate
                    else:
                        if current:
                            chunks.append(current)
                        if _measure(part) > size:
                            # Recurse with next separator
                            next_seps = separators[separators.index(sep) + 1:] if sep != separators[-1] else [""]
                            chunks.extend(_split_recursive(part, next_seps))
                            current = ""
                        else:
                            overlap_text = current[-overlap:] if current and overlap else ""
                            current = (overlap_text + " " + part).strip() if overlap_text else part.strip()
                if current:
                    chunks.append(current)
                if chunks:
                    return chunks
        # Fall back to fixed split
        return _split_fixed(text)

    def _split_semantic_boundary(text: str) -> list[str]:
        # Split on Markdown headers or numbered sections
        sections = re.split(r"(?=#{1,4}\s|\n\d+\.\s)", text)
        result: list[str] = []
        size = max(1, int(chunk_size or 512))
        for sec in sections:
            sec = sec.strip()
            if not sec:
                continue
            if _measure(sec) <= size:
                result.append(sec)
            else:
                result.extend(_split_fixed(sec))
        return result

    dispatch = {
        "fixed_size": _split_fixed,
        "sentence": _split_sentence,
        "paragraph": _split_paragraph,
        "recursive": _split_recursive,
        "semantic_boundary": _split_semantic_boundary,
    }
    splitter = dispatch.get(strategy or "fixed_size", _split_fixed)

    chunk_rows: list[dict[str, Any]] = []
    min_size = max(0, int(min_chunk_size or 0))
    idx_col = (chunk_index_column or "chunk_index").strip()
    ci_col = "chunk_index_column"

    total_chunks = 0
    total_docs = 0
    skipped_short = 0

    for doc_idx, row in enumerate(rows):
        text = str(row.get(t_col, "") or "")
        if not text.strip():
            continue
        total_docs += 1
        chunks = splitter(text)

        meta: dict[str, Any] = {}
        if include_metadata:
            for k, v in row.items():
                if k != t_col:
                    meta[k] = v

        for ci, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            if min_size > 0 and len(chunk) < min_size:
                skipped_short += 1
                continue
            chunk_row: dict[str, Any] = {}
            if include_metadata:
                chunk_row.update(meta)
            chunk_row["doc_index"] = doc_idx
            chunk_row[idx_col] = ci
            chunk_row[t_col] = chunk
            chunk_row["chunk_char_count"] = len(chunk)
            if enc:
                chunk_row["chunk_token_count"] = _count_tokens(chunk, enc)
            chunk_rows.append(chunk_row)
            total_chunks += 1

    if not chunk_rows:
        raise ValueError(
            "No chunks produced. Check text_column and chunk_size parameters."
        )

    dataset_ref = records_to_dataset(chunk_rows, name="chunks.parquet")

    return {
        "main": dataset_ref,
        "n_chunks": total_chunks,
        "n_documents": total_docs,
        "n_skipped_short": skipped_short,
        "strategy": strategy,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "created_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: RAG Answer Eval (rule-based + optional LLM)
# ---------------------------------------------------------------------------

_RAG_EVAL_MODES = [
    "exact_match",
    "contains_answer",
    "factual_overlap",
    "llm_judge",
]

@node(
    name="RAG Answer Eval",
    id="rag_answer_eval",
    category=ML_CATEGORY,
    icon="check-circle",
    description=(
        "Evaluate RAG-generated answers against expected answers or source "
        "contexts. Supports deterministic (exact, contains, overlap) and "
        "LLM-as-judge modes. Returns an EvalResultRef. Deterministic modes "
        "require no packages; llm_judge mode requires openai>=1.0."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "mode": {
            "choices": _RAG_EVAL_MODES,
            "description": "Evaluation strategy.",
        },
        "answer_column": {
            "description": "Column containing the generated answer.",
        },
        "expected_column": {
            "description": "Column containing the expected/gold answer.",
        },
        "context_column": {
            "description": "Column containing the retrieved context (for faithfulness checks).",
        },
        "question_column": {
            "description": "Column containing the original question (used by LLM judge).",
        },
        "openai_api_key": {
            "group": "LLM Judge",
            "description": "OpenAI API key (required for llm_judge mode).",
            **cred_single("openai", "api_key", "OpenAI API key"),
        },
        "judge_model": {
            "group": "LLM Judge",
            "description": "Model to use as judge.",
        },
        "score_scale": {
            "group": "LLM Judge",
            "description": "Score scale max (1-10).",
        },
        "overlap_threshold": {
            "group": "Options",
            "description": "Minimum word overlap fraction to pass factual_overlap mode (0.0-1.0).",
        },
        "case_sensitive": {
            "group": "Options",
            "description": "Case-sensitive string comparison.",
        },
        "strip_whitespace": {
            "group": "Options",
            "description": "Strip whitespace before comparison.",
        },
        "concurrency": {
            "group": "Options",
            "description": "Parallel LLM calls for llm_judge mode.",
        },
    },
    param_groups={"LLM Judge": [], "Options": []},
)
def rag_answer_eval(
    input: Any = None,
    mode: str = "contains_answer",
    answer_column: str = "answer",
    expected_column: str = "expected",
    context_column: str = "context",
    question_column: str = "question",
    openai_api_key: Any = None,
    judge_model: str = "gpt-4o",
    score_scale: int = 5,
    overlap_threshold: float = 0.5,
    case_sensitive: bool = False,
    strip_whitespace: bool = True,
    concurrency: int = 5,
) -> dict[str, Any]:
    """Evaluate RAG answers and return an EvalResultRef."""
    _EVAL_RESULT_MARKER = "__noodle_eval_result__"

    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records.")

    a_col = (answer_column or "answer").strip()
    e_col = (expected_column or "expected").strip()
    c_col = (context_column or "context").strip()
    q_col = (question_column or "question").strip()

    if rows and a_col not in rows[0]:
        available = list(rows[0].keys())
        raise ValueError(f"answer_column {a_col!r} not found. Available: {available}")

    def _normalize(text: str) -> str:
        s = str(text)
        if strip_whitespace:
            s = s.strip()
        if not case_sensitive:
            s = s.lower()
        return s

    def _word_overlap(a: str, b: str) -> float:
        words_a = set(re.findall(r"\w+", _normalize(a)))
        words_b = set(re.findall(r"\w+", _normalize(b)))
        if not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_b)

    result_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    if mode == "llm_judge":
        api_key, base_url, org = _extract_api_key(openai_api_key)
        if not api_key:
            raise ValueError("openai_api_key is required for llm_judge mode.")
        client = _openai_client(api_key, base_url, org)
        scale = max(2, min(int(score_scale or 5), 10))

        def _judge_row(row: dict[str, Any]) -> dict[str, Any]:
            answer = str(row.get(a_col, "")).strip()
            expected = str(row.get(e_col, "")).strip()
            context = str(row.get(c_col, "")).strip()
            question = str(row.get(q_col, "")).strip()

            parts = []
            if question:
                parts.append(f"Question: {question}")
            if context:
                parts.append(f"Retrieved Context: {context}")
            parts.append(f"Generated Answer: {answer}")
            if expected:
                parts.append(f"Expected Answer: {expected}")

            prompt = (
                "\n".join(parts) + "\n\n"
                f"Rate the generated answer on faithfulness to context and correctness "
                f"on a scale of 1 to {scale} (1=wrong/unfaithful, {scale}=perfect). "
                f'Respond with JSON: {{"score": <integer 1-{scale}>, "reason": "<one sentence>"}}'
            )
            resp = client.chat.completions.create(
                model=judge_model or "gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=128,
                response_format={"type": "json_object"},
            )
            raw = str(resp.choices[0].message.content or "{}")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r'"?score"?\s*:\s*(\d+)', raw)
                parsed = {"score": int(m.group(1)) if m else 1}
            score = parsed.get("score")
            reason = parsed.get("reason", "")
            threshold = scale * 0.6
            return {
                "eval_passed": float(score or 0) >= threshold,
                "judge_score": score,
                "judge_reason": reason,
            }

        labeled: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(1, int(concurrency or 5))) as pool:
            futures = {pool.submit(_judge_row, row): i for i, row in enumerate(rows)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    labeled[idx] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"row {idx}: {exc}")
                    labeled[idx] = {"eval_passed": False, "judge_score": None, "judge_reason": str(exc)}

        scores: list[float] = []
        for i, row in enumerate(rows):
            r = dict(row)
            r.update(labeled.get(i, {"eval_passed": False}))
            result_rows.append(r)
            s = r.get("judge_score")
            if s is not None:
                try:
                    scores.append(float(s))
                except (TypeError, ValueError):
                    pass

        n = len(result_rows)
        n_passed = sum(1 for r in result_rows if r.get("eval_passed") is True)
        accuracy = n_passed / n if n else 0.0
        avg_score = sum(scores) / len(scores) if scores else None
        summary: dict[str, Any] = {
            "mode": mode,
            "n_rows": n,
            "n_passed": n_passed,
            "accuracy": round(accuracy, 4),
            "avg_score": round(avg_score, 3) if avg_score is not None else None,
            "score_scale": scale,
        }

    else:
        # Deterministic modes — no openai needed
        for row in rows:
            r = dict(row)
            answer = str(row.get(a_col, "")).strip()
            expected = str(row.get(e_col, "")).strip()
            context = str(row.get(c_col, "")).strip()

            if mode == "exact_match":
                passed = _normalize(answer) == _normalize(expected)
            elif mode == "contains_answer":
                passed = _normalize(expected) in _normalize(answer) if expected else True
            elif mode == "factual_overlap":
                ref = expected or context
                passed = _word_overlap(answer, ref) >= float(overlap_threshold or 0.5)
                r["word_overlap"] = round(_word_overlap(answer, ref), 4)
            else:
                passed = False

            r["eval_passed"] = passed
            result_rows.append(r)

        n = len(result_rows)
        n_passed = sum(1 for r in result_rows if r.get("eval_passed") is True)
        accuracy = n_passed / n if n else 0.0
        summary = {
            "mode": mode,
            "n_rows": n,
            "n_passed": n_passed,
            "n_failed": n - n_passed,
            "accuracy": round(accuracy, 4),
        }

    rows_ref = records_to_dataset(result_rows, name="rag_eval_results.parquet")

    return {
        _EVAL_RESULT_MARKER: True,
        "version": 1,
        "kind": "rag_answer_eval",
        "summary": summary,
        "rows": rows_ref,
        "errors": errors[:10],
        "created_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: Chunking Experiment
# ---------------------------------------------------------------------------

@node(
    name="Chunking Experiment",
    id="rag_chunking_experiment",
    category=ML_CATEGORY,
    icon="flask",
    description=(
        "Compare multiple chunking strategies and sizes on a document dataset. "
        "Reports chunk count, size distribution, and coverage metrics for each "
        "strategy. Useful for tuning RAG chunk parameters before indexing."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "text_column": {
            "description": "Column containing document text.",
        },
        "strategies": {
            "description": "Comma-separated chunking strategies to compare. "
                           "Options: fixed_size, sentence, paragraph, recursive.",
        },
        "chunk_sizes": {
            "description": "Comma-separated chunk sizes to test (e.g. 256, 512, 1024).",
        },
        "chunk_overlap": {
            "description": "Overlap in characters (shared across all experiments).",
        },
        "min_chunk_size": {
            "description": "Drop chunks shorter than this. Counts toward coverage loss.",
        },
        "max_documents": {
            "description": "Max documents to sample for the experiment (0 = all).",
        },
    },
)
def rag_chunking_experiment(
    input: Any = None,
    text_column: str = "text",
    strategies: str = "fixed_size, sentence, paragraph",
    chunk_sizes: str = "256, 512, 1024",
    chunk_overlap: int = 64,
    min_chunk_size: int = 50,
    max_documents: int = 50,
) -> dict[str, Any]:
    """Compare chunking strategies and report metrics."""
    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of document records.")

    t_col = (text_column or "text").strip()
    if rows and t_col not in rows[0]:
        available = list(rows[0].keys())
        raise ValueError(f"text_column {t_col!r} not found. Available: {available}")

    cap = int(max_documents or 0)
    if cap > 0 and len(rows) > cap:
        rows = rows[:cap]

    strategy_list = [s.strip() for s in (strategies or "fixed_size").split(",") if s.strip()]
    size_list: list[int] = []
    for s in (chunk_sizes or "512").split(","):
        s = s.strip()
        if s.isdigit():
            size_list.append(int(s))
    if not size_list:
        size_list = [512]

    def _split_fixed(text: str, size: int, overlap: int) -> list[str]:
        step = max(1, size - overlap)
        return [text[i:i+size].strip() for i in range(0, len(text), step) if text[i:i+size].strip()]

    def _split_sentence(text: str) -> list[str]:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

    def _split_paragraph(text: str) -> list[str]:
        return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    def _split_recursive(text: str, size: int, overlap: int) -> list[str]:
        if len(text) <= size:
            return [text.strip()] if text.strip() else []
        for sep in ["\n\n", "\n", ". ", " ", ""]:
            if sep and sep in text:
                parts = text.split(sep)
                result: list[str] = []
                current = ""
                for part in parts:
                    candidate = (current + sep + part).strip() if current else part.strip()
                    if len(candidate) <= size:
                        current = candidate
                    else:
                        if current:
                            result.append(current)
                        current = part.strip()[-overlap:] + " " + part.strip() if overlap else part.strip()
                if current:
                    result.append(current)
                if result:
                    return result
        return _split_fixed(text, size, overlap)

    results: list[dict[str, Any]] = []
    for strategy in strategy_list:
        for size in size_list:
            all_chunks: list[str] = []
            total_chars = 0
            for row in rows:
                text = str(row.get(t_col, "") or "")
                if not text.strip():
                    continue
                total_chars += len(text)
                if strategy == "fixed_size":
                    chunks = _split_fixed(text, size, chunk_overlap)
                elif strategy == "sentence":
                    chunks = _split_sentence(text)
                elif strategy == "paragraph":
                    chunks = _split_paragraph(text)
                elif strategy == "recursive":
                    chunks = _split_recursive(text, size, chunk_overlap)
                else:
                    chunks = _split_fixed(text, size, chunk_overlap)
                all_chunks.extend([c for c in chunks if c])

            valid = [c for c in all_chunks if len(c) >= min_chunk_size]
            chunk_lens = [len(c) for c in valid]
            n = len(chunk_lens)
            if n == 0:
                results.append({
                    "strategy": strategy, "chunk_size_param": size,
                    "n_chunks": 0, "mean_chars": 0, "min_chars": 0,
                    "max_chars": 0, "n_docs": len(rows), "coverage_pct": 0.0,
                    "short_dropped": len(all_chunks),
                })
                continue

            covered_chars = sum(chunk_lens)
            coverage = min(1.0, covered_chars / max(1, total_chars))
            results.append({
                "strategy": strategy,
                "chunk_size_param": size,
                "n_chunks": n,
                "mean_chars": round(sum(chunk_lens) / n, 1),
                "min_chars": min(chunk_lens),
                "max_chars": max(chunk_lens),
                "median_chars": sorted(chunk_lens)[n // 2],
                "n_docs": len(rows),
                "coverage_pct": round(coverage * 100, 2),
                "short_dropped": len(all_chunks) - len(valid),
            })

    if not results:
        raise ValueError("No chunking results produced.")

    results_ref = records_to_dataset(results, name="chunking_experiment.parquet")

    # Build a simple text report
    report_lines = [
        "# Chunking Experiment Report",
        f"\n**Documents sampled:** {len(rows)}  ",
        f"**Strategies tested:** {', '.join(strategy_list)}  ",
        f"**Chunk sizes tested:** {', '.join(str(s) for s in size_list)}  ",
        "\n## Results\n",
        "| Strategy | Size | Chunks | Mean Chars | Coverage % | Dropped |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        report_lines.append(
            f"| {r['strategy']} | {r['chunk_size_param']} | {r['n_chunks']} "
            f"| {r['mean_chars']} | {r['coverage_pct']}% | {r['short_dropped']} |"
        )

    report_ref = write_text(
        "\n".join(report_lines),
        "chunking-experiment.md",
        "text/markdown; charset=utf-8",
        metadata={"kind": "chunking_experiment"},
    )

    return {
        "main": results_ref,
        "report": report_ref,
        "n_experiments": len(results),
        "n_documents": len(rows),
        "created_at": _ts(),
    }


# ---------------------------------------------------------------------------
# Node: Context Relevance Check
# ---------------------------------------------------------------------------

@node(
    name="Context Relevance Check",
    id="rag_context_relevance",
    category=ML_CATEGORY,
    icon="link",
    description=(
        "Check whether retrieved context is relevant to the query. "
        "keyword_overlap mode uses word overlap and requires no packages. "
        "llm_judge mode uses an LLM and requires openai>=1.0. "
        "Helps identify retrieval failures before answer generation."
    ),
    inputs=["input"],
    outputs=["main"],
    params={
        "mode": {
            "choices": ["keyword_overlap", "llm_judge"],
            "description": "Relevance check strategy.",
        },
        "question_column": {
            "description": "Column containing the user question.",
        },
        "context_column": {
            "description": "Column containing the retrieved context/passage.",
        },
        "output_column": {
            "description": "Column to write the relevance score or pass/fail.",
        },
        "overlap_threshold": {
            "group": "Keyword",
            "description": "Minimum word overlap fraction for keyword_overlap mode.",
        },
        "openai_api_key": {
            "group": "LLM",
            **cred_single("openai", "api_key", "OpenAI API key"),
            "description": "OpenAI API key (required for llm_judge mode).",
        },
        "model": {
            "group": "LLM",
            "description": "Model to use as relevance judge.",
        },
        "concurrency": {
            "group": "LLM",
            "description": "Parallel LLM calls.",
        },
    },
    param_groups={"Keyword": [], "LLM": []},
)
def rag_context_relevance(
    input: Any = None,
    mode: str = "keyword_overlap",
    question_column: str = "question",
    context_column: str = "context",
    output_column: str = "context_relevant",
    overlap_threshold: float = 0.3,
    openai_api_key: Any = None,
    model: str = "gpt-4.1-mini",
    concurrency: int = 5,
) -> dict[str, Any]:
    """Score retrieved context relevance for each row."""
    _EVAL_RESULT_MARKER = "__noodle_eval_result__"

    rows = _to_records(input)
    if not rows:
        raise ValueError("input must be a DatasetRef or list of records.")

    q_col = (question_column or "question").strip()
    c_col = (context_column or "context").strip()
    out_col = (output_column or "context_relevant").strip()

    for col in (q_col, c_col):
        if rows and col not in rows[0]:
            available = list(rows[0].keys())
            raise ValueError(f"Column {col!r} not found. Available: {available}")

    result_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    def _word_set(text: str) -> set[str]:
        return set(re.findall(r"\w+", text.lower()))

    if mode == "keyword_overlap":
        thresh = float(overlap_threshold or 0.3)
        for row in rows:
            r = dict(row)
            q_words = _word_set(str(row.get(q_col, "")))
            c_words = _word_set(str(row.get(c_col, "")))
            if not q_words:
                overlap = 0.0
            else:
                overlap = len(q_words & c_words) / len(q_words)
            r[out_col] = overlap >= thresh
            r["context_overlap_score"] = round(overlap, 4)
            result_rows.append(r)

        n = len(result_rows)
        n_relevant = sum(1 for r in result_rows if r.get(out_col) is True)
        summary: dict[str, Any] = {
            "mode": mode,
            "n_rows": n,
            "n_relevant": n_relevant,
            "relevance_rate": round(n_relevant / n, 4) if n else 0.0,
            "threshold": thresh,
        }

    else:  # llm_judge
        api_key, base_url, org = _extract_api_key(openai_api_key)
        if not api_key:
            raise ValueError("openai_api_key is required for llm_judge mode.")
        client = _openai_client(api_key, base_url, org)

        def _judge(row: dict[str, Any]) -> dict[str, Any]:
            question = str(row.get(q_col, "")).strip()
            context = str(row.get(c_col, "")).strip()
            prompt = (
                f"Question: {question}\n\nRetrieved Context: {context}\n\n"
                "Is the retrieved context relevant to answering the question? "
                'Respond with JSON: {"relevant": true or false, "reason": "<one sentence>"}'
            )
            resp = client.chat.completions.create(
                model=model or "gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=64,
                response_format={"type": "json_object"},
            )
            raw = str(resp.choices[0].message.content or "{}")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r'"?relevant"?\s*:\s*(true|false)', raw)
                parsed = {"relevant": m.group(1) == "true" if m else False}
            return {
                out_col: bool(parsed.get("relevant", False)),
                "relevance_reason": parsed.get("reason", ""),
            }

        labeled: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(1, int(concurrency or 5))) as pool:
            futures = {pool.submit(_judge, row): i for i, row in enumerate(rows)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    labeled[idx] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"row {idx}: {exc}")
                    labeled[idx] = {out_col: False}

        for i, row in enumerate(rows):
            r = dict(row)
            r.update(labeled.get(i, {out_col: False}))
            result_rows.append(r)

        n = len(result_rows)
        n_relevant = sum(1 for r in result_rows if r.get(out_col) is True)
        summary = {
            "mode": mode,
            "n_rows": n,
            "n_relevant": n_relevant,
            "relevance_rate": round(n_relevant / n, 4) if n else 0.0,
        }

    rows_ref = records_to_dataset(result_rows, name="context_relevance.parquet")

    return {
        _EVAL_RESULT_MARKER: True,
        "version": 1,
        "kind": "rag_context_relevance",
        "summary": summary,
        "rows": rows_ref,
        "errors": errors[:10],
        "created_at": _ts(),
    }
