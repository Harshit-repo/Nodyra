"""Text processing nodes."""

from __future__ import annotations

import difflib
import json
import re
import textwrap
from typing import Any

from nodyra.sdk import node


@node(
    name="Text Slugify",
    id="text_slugify",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Text to slugify. Falls back to wired input.",
            "placeholder": "Hello World!",
        },
        "separator": {
            "group": "Options",
            "default": "-",
            "placeholder": "-",
            "description": "Word separator.",
        },
        "lowercase": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Convert to lowercase.",
        },
    },
)
def text_slugify(
    input: Any = None,
    text: str = "",
    separator: str = "-",
    lowercase: bool = True,
) -> dict[str, Any]:
    """Convert text into a URL-friendly slug."""
    raw = str(input if input is not None else text)
    if lowercase:
        raw = raw.lower()
    slug = re.sub(r"[^\w\s" + re.escape(separator) + "]", "", raw)
    slug = re.sub(r"[" + re.escape(separator) + r"\s]+", separator, slug)
    slug = slug.strip(separator)
    return {"slug": slug, "original": str(text)}


@node(
    name="Text Truncate",
    id="text_truncate",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Text to truncate. Falls back to wired input.",
            "multiline": True,
        },
        "max_length": {
            "type": "integer",
            "required": True,
            "default": 100,
            "description": "Maximum character length.",
        },
        "ellipsis": {
            "group": "Options",
            "default": "...",
            "description": "Suffix when truncated.",
        },
        "word_boundary": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Break at word boundary.",
        },
    },
)
def text_truncate(
    input: Any = None,
    text: str = "",
    max_length: int = 100,
    ellipsis: str = "...",
    word_boundary: bool = True,
) -> dict[str, Any]:
    """Truncate text to a maximum length."""
    raw = str(input if input is not None else text)
    original_length = len(raw)
    max_len = max(0, int(max_length or 100))
    if original_length <= max_len:
        return {
            "text": raw,
            "truncated": False,
            "original_length": original_length,
            "truncated_length": original_length,
        }
    if max_len == 0:
        return {
            "text": "",
            "truncated": True,
            "original_length": original_length,
            "truncated_length": 0,
        }
    if len(ellipsis) >= max_len:
        return {
            "text": ellipsis[:max_len],
            "truncated": True,
            "original_length": original_length,
            "truncated_length": max_len,
        }
    target = max_len - len(ellipsis)
    if word_boundary:
        truncated = raw[:target]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
    else:
        truncated = raw[:target]
    result = truncated + ellipsis
    return {
        "text": result,
        "truncated": True,
        "original_length": original_length,
        "truncated_length": len(result),
    }


def _to_snake(text: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", text)
    return "_".join(p.lower() for p in parts if p)


def _to_camel(text: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", text)
    if not parts:
        return ""
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def _to_pascal(text: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", text)
    return "".join(p.capitalize() for p in parts if p)


def _to_kebab(text: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", text)
    return "-".join(p.lower() for p in parts if p)


@node(
    name="Text Case Convert",
    id="text_case_convert",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Text to convert. Falls back to wired input.",
        },
        "case": {
            "required": True,
            "choices": [
                "lower",
                "upper",
                "title",
                "capitalize",
                "swapcase",
                "snake_case",
                "camelCase",
                "PascalCase",
                "kebab-case",
            ],
            "description": "Target case.",
        },
    },
)
def text_case_convert(
    input: Any = None,
    text: str = "",
    case: str = "lower",
) -> dict[str, Any]:
    """Convert text between different cases."""
    raw = str(input if input is not None else text)
    case_map: dict[str, Any] = {
        "lower": str.lower,
        "upper": str.upper,
        "title": str.title,
        "capitalize": str.capitalize,
        "swapcase": str.swapcase,
        "snake_case": _to_snake,
        "camelCase": _to_camel,
        "PascalCase": _to_pascal,
        "kebab-case": _to_kebab,
    }
    converter = case_map.get(case, str.lower)
    result = converter(raw) if raw else ""
    return {"result": result, "case": case, "original": str(text)}


@node(
    name="Text Word Count",
    id="text_word_count",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Text to analyze. Falls back to wired input.",
            "multiline": True,
        },
    },
    tool_side_effecting=False,
)
def text_word_count(
    input: Any = None,
    text: str = "",
) -> dict[str, Any]:
    """Count words, characters, lines, sentences, and paragraphs."""
    raw = str(input if input is not None else text)
    if not raw:
        return {
            "word_count": 0,
            "char_count": 0,
            "char_count_no_spaces": 0,
            "line_count": 0,
            "sentence_count": 0,
            "paragraph_count": 0,
            "avg_word_length": 0.0,
        }
    words = raw.split()
    word_count = len(words)
    char_count = len(raw)
    char_count_no_spaces = len(
        raw.replace(" ", "").replace("\t", "").replace("\n", "").replace("\r", "")
    )
    line_count = len(raw.splitlines())
    sentence_count = len(re.split(r"[.!?]+", raw)) - 1
    paragraph_count = len([p for p in re.split(r"\n\s*\n", raw) if p.strip()])
    avg_word_length = round(sum(len(w) for w in words) / word_count, 2) if word_count else 0.0
    return {
        "word_count": word_count,
        "char_count": char_count,
        "char_count_no_spaces": char_count_no_spaces,
        "line_count": line_count,
        "sentence_count": sentence_count,
        "paragraph_count": paragraph_count,
        "avg_word_length": avg_word_length,
    }


@node(
    name="Text Diff",
    id="text_diff",
    category="Transform",
    icon="hash",
    params={
        "old_text": {
            "description": "Original text.",
            "multiline": True,
        },
        "new_text": {
            "description": "New/Modified text.",
            "multiline": True,
        },
        "context_lines": {
            "group": "Options",
            "type": "integer",
            "default": 3,
            "description": "Context lines in unified diff.",
        },
    },
)
def text_diff(
    input: Any = None,
    old_text: str = "",
    new_text: str = "",
    context_lines: int = 3,
) -> dict[str, Any]:
    """Generate a unified diff between two texts."""
    old = str(old_text)
    new = str(new_text)
    n = max(0, int(context_lines or 3))
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(old_lines, new_lines, fromfile="original", tofile="modified", n=n)
    )
    diff = "".join(diff_lines)
    additions = sum(
        1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
    )
    return {
        "diff": diff,
        "has_changes": bool(diff.strip()),
        "additions": additions,
        "deletions": deletions,
    }


@node(
    name="Text Join",
    id="text_join",
    category="Transform",
    icon="hash",
    params={
        "items": {
            "description": "JSON array of strings or multiline text (one per line) to join.",
            "multiline": True,
        },
        "separator": {
            "default": ", ",
            "description": "Joiner string.",
        },
        "quote": {
            "group": "Options",
            "default": "",
            "placeholder": "'",
            "description": "Optional quote character around each item.",
        },
    },
)
def text_join(
    input: Any = None,
    items: str = "",
    separator: str = ", ",
    quote: str = "",
) -> dict[str, Any]:
    """Join multiple strings into one."""
    raw = items
    parsed: list[str] = []
    if raw and raw.strip():
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                parsed = raw.splitlines()
            else:
                parsed = [str(i) for i in parsed]
        except (json.JSONDecodeError, TypeError):
            parsed = raw.splitlines()
    if quote:
        parsed = [f"{quote}{item}{quote}" for item in parsed]
    result = separator.join(parsed)
    return {"result": result, "count": len(parsed)}


@node(
    name="Text Split",
    id="text_split",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Text to split. Falls back to wired input.",
            "multiline": True,
        },
        "separator": {
            "default": "\n",
            "description": "Delimiter to split on.",
        },
        "limit": {
            "group": "Options",
            "type": "integer",
            "default": 0,
            "description": "Max splits (0=all).",
        },
        "strip_items": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Strip whitespace from each item.",
        },
        "filter_empty": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Remove empty items.",
        },
    },
)
def text_split(
    input: Any = None,
    text: str = "",
    separator: str = "\n",
    limit: int = 0,
    strip_items: bool = True,
    filter_empty: bool = True,
) -> dict[str, Any]:
    """Split text into a list of strings."""
    raw = str(input if input is not None else text)
    maxsplit = max(0, int(limit or 0))
    if maxsplit > 0:
        parts = raw.split(separator, maxsplit)
    else:
        parts = raw.split(separator)
    if strip_items:
        parts = [p.strip() for p in parts]
    if filter_empty:
        parts = [p for p in parts if p]
    return {"items": parts, "count": len(parts)}


@node(
    name="Text Wrap",
    id="text_wrap",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "multiline": True,
            "description": "Text to wrap. Falls back to wired input.",
        },
        "width": {
            "type": "integer",
            "default": 80,
            "description": "Maximum line width.",
        },
        "break_long_words": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Break words longer than width.",
        },
    },
)
def text_wrap(
    input: Any = None,
    text: str = "",
    width: int = 80,
    break_long_words: bool = True,
) -> dict[str, Any]:
    """Wrap text to a specified line width."""
    raw = str(input if input is not None else text)
    w = max(1, int(width or 80))
    wrapped = textwrap.fill(raw, width=w, break_long_words=bool(break_long_words))
    return {"wrapped": wrapped, "line_count": wrapped.count("\n") + 1}


@node(
    name="Text Extract Between",
    id="text_extract_between",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "multiline": True,
            "description": "Text to search. Falls back to wired input.",
        },
        "start_delimiter": {
            "description": "Start delimiter.",
        },
        "end_delimiter": {
            "description": "End delimiter.",
        },
        "include_delimiters": {
            "group": "Options",
            "type": "boolean",
            "default": False,
            "description": "Include delimiters in results.",
        },
        "first_only": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Only return the first match.",
        },
    },
)
def text_extract_between(
    input: Any = None,
    text: str = "",
    start_delimiter: str = "",
    end_delimiter: str = "",
    include_delimiters: bool = False,
    first_only: bool = True,
) -> dict[str, Any]:
    """Extract text between delimiters."""
    raw = str(input if input is not None else text)
    if not start_delimiter or not end_delimiter:
        raise ValueError("text_extract_between: start_delimiter and end_delimiter are required")

    start_esc = re.escape(start_delimiter)
    end_esc = re.escape(end_delimiter)
    pattern = start_esc + r"(.*?)" + end_esc
    matches = re.findall(pattern, raw)
    if not matches:
        return {"matches": [], "count": 0}
    if not include_delimiters:
        result = matches[:1] if first_only else matches
    else:
        result = [
            (start_delimiter + m + end_delimiter) for m in (matches[:1] if first_only else matches)
        ]
    return {"matches": result, "count": len(result)}


@node(
    name="Text Strip",
    id="text_strip",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "multiline": True,
            "description": "Text to strip. Falls back to wired input.",
        },
        "chars": {
            "group": "Options",
            "description": "Characters to strip (blank=whitespace).",
        },
        "left_only": {
            "group": "Options",
            "type": "boolean",
            "default": False,
            "description": "Only strip from left side.",
        },
        "right_only": {
            "group": "Options",
            "type": "boolean",
            "default": False,
            "description": "Only strip from right side.",
        },
    },
)
def text_strip(
    input: Any = None,
    text: str = "",
    chars: str = "",
    left_only: bool = False,
    right_only: bool = False,
) -> dict[str, Any]:
    """Strip characters from the start/end of text."""
    raw = str(input if input is not None else text)
    strip_chars = chars or None
    if left_only:
        result = raw.lstrip(strip_chars)
    elif right_only:
        result = raw.rstrip(strip_chars)
    else:
        result = raw.strip(strip_chars)
    return {"result": result, "removed": len(raw) - len(result)}


@node(
    name="Text Contains",
    id="text_contains",
    category="Transform",
    icon="hash",
    tool_side_effecting=False,
    params={
        "text": {
            "multiline": True,
            "description": "Text to search. Falls back to wired input.",
        },
        "substring": {
            "description": "Substring or regex pattern to search for.",
        },
        "use_regex": {
            "group": "Options",
            "type": "boolean",
            "default": False,
            "description": "Treat substring as regex pattern.",
        },
        "case_sensitive": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Case-sensitive search.",
        },
    },
)
def text_contains(
    input: Any = None,
    text: str = "",
    substring: str = "",
    use_regex: bool = False,
    case_sensitive: bool = True,
) -> dict[str, Any]:
    """Check if text contains a substring or pattern."""
    if not substring:
        raise ValueError("text_contains: substring is required")
    raw = str(input if input is not None else text)
    if use_regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        m = re.search(substring, raw, flags=flags)
        return {"contains": m is not None, "match": m.group(0) if m else None}
    if not case_sensitive:
        result = substring.lower() in raw.lower()
    else:
        result = substring in raw
    return {"contains": result, "match": substring if result else None}


__all__ = [
    "text_slugify",
    "text_truncate",
    "text_case_convert",
    "text_word_count",
    "text_diff",
    "text_join",
    "text_split",
    "text_wrap",
    "text_extract_between",
    "text_strip",
    "text_contains",
]
