"""AI Output Parser supplier nodes + concrete parsers — WP10."""

from __future__ import annotations

import json
from typing import Any

from nodyra.ai_runtime import OutputParserAdapter
from nodyra.sdk import node

AI_CATEGORY = "AI"


class StructuredOutputParser(OutputParserAdapter):
    """Parses model output as JSON and validates required top-level keys.

    Tolerates fenced ```json blocks and surrounding prose by extracting the
    first balanced JSON object/array.
    """

    def __init__(self, *, schema: dict[str, Any] | None = None) -> None:
        self._schema = schema or {}
        props = self._schema.get("properties")
        self._properties = props if isinstance(props, dict) else {}
        req = self._schema.get("required")
        self._required = [str(k) for k in req] if isinstance(req, list) else []

    def parse(self, text: str) -> Any:
        payload = _extract_json(text)
        if payload is None:
            raise ValueError("output parser: response was not valid JSON")
        if isinstance(payload, dict):
            missing = [k for k in self._required if k not in payload]
            if missing:
                raise ValueError(
                    f"output parser: missing required keys: {', '.join(missing)}"
                )
        return payload

    @property
    def format_instructions(self) -> str:
        if not self._properties:
            return "Respond with a single valid JSON object."
        keys = ", ".join(self._properties.keys())
        required = (
            f" Required keys: {', '.join(self._required)}." if self._required else ""
        )
        return (
            "Respond with a single valid JSON object containing these keys: "
            f"{keys}.{required} Do not wrap the JSON in markdown fences."
        )


def _extract_json(text: str) -> Any | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        # strip a fenced block
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    # try to locate the first balanced object / array
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except ValueError:
                continue
    return None


@node(
    name="AI Structured Output Parser",
    id="ai_structured_output_parser",
    category=AI_CATEGORY,
    role="output_parser",
    icon="ai",
    outputs=["parser"],
    output_kinds={"parser": "ai_output_parser"},
    params={
        "schema": {
            "widget": "code",
            "description": "JSON Schema describing the expected output object.",
        },
    },
)
def ai_structured_output_parser(
    schema: Any = None,
) -> OutputParserAdapter:
    """Supply a JSON output parser/validator to a downstream AI Agent."""
    parsed: dict[str, Any] = {}
    if isinstance(schema, str) and schema.strip():
        try:
            loaded = json.loads(schema)
            if isinstance(loaded, dict):
                parsed = loaded
        except ValueError:
            parsed = {}
    elif isinstance(schema, dict):
        parsed = schema
    return StructuredOutputParser(schema=parsed)
