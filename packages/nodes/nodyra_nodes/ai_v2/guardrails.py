"""AI Guardrail supplier nodes + concrete guardrail adapters — WP10."""

from __future__ import annotations

import re
from typing import Any

from nodyra.ai_runtime import ChatResponse, GuardrailAdapter
from nodyra.context import node_debug
from nodyra.sdk import node

AI_CATEGORY = "AI"
MAX_GUARDRAIL_EVENTS = 50


def _record_guardrail_event(event: dict[str, Any]) -> None:
    debug = node_debug.get()
    if debug is None:
        return
    events = debug.setdefault("guardrail_events", [])
    if not isinstance(events, list) or len(events) >= MAX_GUARDRAIL_EVENTS:
        return
    events.append(event)


class KeywordGuardrail(GuardrailAdapter):
    """Blocks or redacts responses containing banned terms / patterns.

    - ``blocked_terms``: case-insensitive substrings that, if present, raise
      ``ValueError`` (triggering an agent retry).
    - ``redact_patterns``: regex patterns whose matches are replaced with
      ``[REDACTED]`` before the response leaves the node.
    - ``max_length``: if > 0, responses longer than this are rejected.
    """

    def __init__(
        self,
        *,
        blocked_terms: list[str] | None = None,
        redact_patterns: list[str] | None = None,
        max_length: int = 0,
    ) -> None:
        self._blocked = [t.lower() for t in (blocked_terms or []) if t]
        self._redact = []
        for pat in redact_patterns or []:
            if not pat:
                continue
            try:
                self._redact.append(re.compile(pat))
            except re.error:
                continue
        self._max_length = int(max_length or 0)

    def check(self, response: ChatResponse) -> ChatResponse:
        text = response.text or ""
        lowered = text.lower()
        for term in self._blocked:
            if term in lowered:
                _record_guardrail_event(
                    {
                        "type": "guardrail_blocked",
                        "adapter": "keyword_guardrail",
                        "reason": "blocked_term",
                    }
                )
                raise ValueError(f"guardrail: blocked term detected: {term!r}")
        if self._max_length > 0 and len(text) > self._max_length:
            _record_guardrail_event(
                {
                    "type": "guardrail_blocked",
                    "adapter": "keyword_guardrail",
                    "reason": "max_length",
                    "max_length": self._max_length,
                    "observed_length": len(text),
                }
            )
            raise ValueError(
                f"guardrail: response exceeds max length {self._max_length}"
            )
        redacted = text
        replacements = 0
        for pattern in self._redact:
            redacted, count = pattern.subn("[REDACTED]", redacted)
            replacements += count
        if redacted == text:
            return response
        _record_guardrail_event(
            {
                "type": "guardrail_redacted",
                "adapter": "keyword_guardrail",
                "replacement_count": replacements,
            }
        )
        return response.model_copy(update={"text": redacted})

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "keyword_guardrail",
            "blocked_terms": self._blocked,
            "max_length": self._max_length,
        }


@node(
    name="AI Guardrail",
    id="ai_guardrail",
    category=AI_CATEGORY,
    role="supplier",
    icon="ai",
    outputs=["guardrail"],
    output_kinds={"guardrail": "ai_guardrail"},
    param_groups={"Options": ["redact_patterns", "max_length"]},
    params={
        "blocked_terms": {
            "widget": "code",
            "description": "JSON array of terms that block the response if present.",
        },
        "redact_patterns": {
            "widget": "code",
            "description": "JSON array of regex patterns to redact from the response.",
            "group": "Options",
        },
        "max_length": {
            "description": "Reject responses longer than this (0 = no limit).",
            "group": "Options",
        },
    },
)
def ai_guardrail(
    blocked_terms: Any = None,
    redact_patterns: Any = None,
    max_length: int = 0,
) -> GuardrailAdapter:
    """Supply a response guardrail to a downstream AI Agent."""
    return KeywordGuardrail(
        blocked_terms=_as_str_list(blocked_terms),
        redact_patterns=_as_str_list(redact_patterns),
        max_length=int(max_length or 0),
    )


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str) and value.strip():
        import json

        try:
            loaded = json.loads(value)
            if isinstance(loaded, list):
                return [str(v) for v in loaded if v]
        except ValueError:
            return [t.strip() for t in value.split(",") if t.strip()]
    return []
