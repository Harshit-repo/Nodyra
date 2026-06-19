"""Regular expression (regex) operations for text processing."""

from __future__ import annotations

import re
from typing import Any

from noodle.sdk import node

_FLAG_MAP = {
    "IGNORECASE": re.IGNORECASE,
    "DOTALL": re.DOTALL,
    "MULTILINE": re.MULTILINE,
}


def _resolve_flags(flags: str) -> int:
    result = 0
    for part in (flags or "").upper().split("|"):
        part = part.strip()
        if part in _FLAG_MAP:
            result |= _FLAG_MAP[part]
    return result


@node(
    name="Regex Extract",
    id="regex_extract_tool",
    category="Transform",
    icon="search",
    params={
        "text": {
            "multiline": True,
            "description": "Text to search. Falls back to wired input.",
            "placeholder": "Enter text...",
        },
        "pattern": {
            "description": "Regular expression pattern.",
            "placeholder": r"(\w+)@(\w+)\.(\w+)",
        },
        "group_num": {
            "group": "Options",
            "description": "Match group to return (0=full match, 1=first group, etc).",
            "default": 0,
        },
        "flags": {
            "group": "Options",
            "choices": ["", "IGNORECASE", "DOTALL", "MULTILINE", "IGNORECASE|DOTALL"],
            "default": "",
        },
    },
)
def regex_extract(
    input: Any = None,
    text: str = "",
    pattern: str = "",
    group_num: int = 0,
    flags: str = "",
) -> dict[str, Any]:
    """Extract regex matches from text."""
    if not pattern:
        raise ValueError("regex_extract: pattern is required")
    source = text if text else (input if isinstance(input, str) else "")
    if not source:
        return {"matches": [], "count": 0}
    try:
        compiled = re.compile(pattern, _resolve_flags(flags))
    except re.error as exc:
        raise ValueError(f"regex_extract: invalid pattern - {exc}") from exc
    matches = []
    for m in compiled.finditer(source):
        try:
            matches.append(m.group(group_num))
        except IndexError:
            matches.append(None)
    return {"matches": matches, "count": len(matches)}


@node(
    name="Regex Replace",
    id="regex_replace_tool",
    category="Transform",
    icon="search",
    params={
        "text": {
            "multiline": True,
            "description": "Text to transform. Falls back to wired input.",
        },
        "pattern": {
            "description": "Regex pattern to match.",
            "placeholder": r"\s+",
        },
        "replacement": {
            "description": "Replacement string (supports \\1 backreferences).",
            "placeholder": "_",
        },
        "count": {
            "group": "Options",
            "description": "Max replacements (0=all).",
            "default": 0,
        },
        "flags": {
            "group": "Options",
            "choices": ["", "IGNORECASE", "DOTALL", "MULTILINE"],
            "default": "",
        },
    },
)
def regex_replace(
    input: Any = None,
    text: str = "",
    pattern: str = "",
    replacement: str = "",
    count: int = 0,
    flags: str = "",
) -> dict[str, Any]:
    """Replace regex matches in text."""
    if not pattern:
        raise ValueError("regex_replace: pattern is required")
    source = text if text else (input if isinstance(input, str) else "")
    try:
        compiled = re.compile(pattern, _resolve_flags(flags))
    except re.error as exc:
        raise ValueError(f"regex_replace: invalid pattern - {exc}") from exc
    max_count = max(0, int(count or 0))
    result, n = compiled.subn(replacement, source, count=max_count or 0)
    return {"result": result, "replacement_count": n}


@node(
    name="Regex Split",
    id="regex_split_tool",
    category="Transform",
    icon="search",
    params={
        "text": {
            "multiline": True,
            "description": "Text to split. Falls back to wired input.",
        },
        "pattern": {
            "description": "Regex delimiter pattern.",
            "placeholder": r"[,\s]+",
        },
        "maxsplit": {
            "group": "Options",
            "description": "Max splits (0=all).",
            "default": 0,
        },
        "flags": {
            "group": "Options",
            "choices": ["", "IGNORECASE", "DOTALL", "MULTILINE"],
            "default": "",
        },
    },
)
def regex_split(
    input: Any = None,
    text: str = "",
    pattern: str = "",
    maxsplit: int = 0,
    flags: str = "",
) -> dict[str, Any]:
    """Split text by regex delimiter."""
    if not pattern:
        raise ValueError("regex_split: pattern is required")
    source = text if text else (input if isinstance(input, str) else "")
    try:
        compiled = re.compile(pattern, _resolve_flags(flags))
    except re.error as exc:
        raise ValueError(f"regex_split: invalid pattern - {exc}") from exc
    max_splits = max(0, int(maxsplit or 0))
    parts = compiled.split(source, maxsplit=max_splits or 0)
    return {"parts": parts, "count": len(parts)}


@node(
    name="Regex Test",
    id="regex_test_tool",
    category="Transform",
    icon="search",
    tool_side_effecting=False,
    params={
        "text": {
            "multiline": True,
            "description": "Text to test against. Falls back to wired input.",
        },
        "pattern": {
            "description": "Regex pattern to test.",
            "placeholder": r"^\d{3}-\d{3}-\d{4}$",
        },
        "flags": {
            "group": "Options",
            "choices": ["", "IGNORECASE", "DOTALL", "MULTILINE"],
            "default": "",
        },
    },
)
def regex_test(
    input: Any = None,
    text: str = "",
    pattern: str = "",
    flags: str = "",
) -> dict[str, Any]:
    """Test whether text matches a regex pattern."""
    if not pattern:
        raise ValueError("regex_test: pattern is required")
    source = text if text else (input if isinstance(input, str) else "")
    try:
        compiled = re.compile(pattern, _resolve_flags(flags))
    except re.error as exc:
        raise ValueError(f"regex_test: invalid pattern - {exc}") from exc
    m = compiled.search(source)
    return {"match": m is not None, "matched_text": m.group(0) if m else None}


@node(
    name="Regex List Patterns",
    id="regex_list_patterns_tool",
    category="Transform",
    icon="list",
    tool_side_effecting=False,
    params={},
)
def regex_list_patterns(input: Any = None) -> dict[str, Any]:
    """Useful built-in regex patterns for common use cases."""
    return {
        "patterns": [
            {
                "name": "Email",
                "pattern": r"[\w.+-]+@[\w-]+\.[\w.-]+",
            },
            {
                "name": "URL",
                "pattern": r"https?://[\w./?=&%-]+",
            },
            {
                "name": "IPv4",
                "pattern": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            },
            {
                "name": "IPv6",
                "pattern": r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b",
            },
            {
                "name": "Date (ISO)",
                "pattern": r"\b\d{4}-\d{2}-\d{2}\b",
            },
            {
                "name": "Phone (US)",
                "pattern": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
            },
            {
                "name": "UUID",
                "pattern": r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
            },
            {
                "name": "Hex Color",
                "pattern": r"#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b",
            },
            {
                "name": "Credit Card",
                "pattern": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
            },
            {
                "name": "HTML Tag",
                "pattern": r"<[^>]+>",
            },
            {
                "name": "Number with decimals",
                "pattern": r"\b\d+(?:\.\d+)?\b",
            },
            {
                "name": "Alphanumeric",
                "pattern": r"\b[a-zA-Z0-9]+\b",
            },
        ],
    }


@node(
    name="Regex Escape",
    id="regex_escape",
    category="Transform",
    icon="search",
    tool_side_effecting=False,
    params={
        "text": {
            "description": "Literal text to escape for safe use in regex. Falls back to wired input.",
            "placeholder": "foo.bar[0]",
        },
    },
)
def regex_escape(
    input: Any = None,
    text: str = "",
) -> dict[str, Any]:
    """Escape special regex characters in text."""
    raw = str(input if input is not None else text)
    escaped = re.escape(raw)
    return {"escaped": escaped, "original": raw}


__all__ = [
    "regex_extract",
    "regex_replace",
    "regex_split",
    "regex_test",
    "regex_list_patterns",
    "regex_escape",
]
