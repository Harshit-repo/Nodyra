"""AI agent power-tool nodes + sub-agent node — Phase B.

Each tool node outputs a ToolAdapter on an ``ai_tool`` port (same pattern as
AI HTTP Tool). ``ai_sub_agent`` outputs a SubAgentAdapter on ``ai_subagent``.
Heavy deps (simpleeval, playwright) are lazy-imported with friendly errors.
"""

from __future__ import annotations

import json
import math
from typing import Any

from noodle.ai_runtime import ToolAdapter, ToolParameterSchema, ToolSchema
from noodle.sdk import node

AI_CATEGORY = "AI"

# Names/functions exposed to the calculator. factorial is wrapped to cap input.
_SAFE_MATH_NAMES: dict[str, Any] = {
    "pi": math.pi, "e": math.e, "inf": math.inf, "nan": math.nan,
}


def _capped_factorial(n: Any) -> int:
    value = int(n)
    if value < 0 or value > 1000:
        raise ValueError("factorial argument must be between 0 and 1000")
    return math.factorial(value)


_SAFE_MATH_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt, "abs": abs, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "atan2": math.atan2, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "log": math.log, "log2": math.log2, "log10": math.log10, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "round": round, "pow": pow,
    "factorial": _capped_factorial, "gcd": math.gcd, "degrees": math.degrees,
    "radians": math.radians, "sum": sum, "min": min, "max": max,
}

_BLOCKED_SUBSTRINGS = ("__", "import", "exec", "eval", "open", "lambda")


class CalculatorToolAdapter(ToolAdapter):
    """Safe math expression evaluator backed by simpleeval (no eval/exec)."""

    def __init__(self, *, name: str, description: str, precision: int, allow_complex: bool) -> None:
        self._name = name or "calculate"
        self._description = description or "Evaluate a mathematical expression safely."
        self._precision = max(0, min(15, int(precision or 10)))
        self._allow_complex = bool(allow_complex)

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description=self._description,
            parameters=ToolParameterSchema(
                properties={"expression": {"type": "string", "description": "Math expression to evaluate."}},
                required=["expression"],
            ),
        )

    @property
    def side_effecting(self) -> bool:
        return False

    def invoke(self, arguments: dict[str, Any]) -> str:
        expression = str((arguments or {}).get("expression") or "").strip()
        if not expression:
            return json.dumps({"error": "No expression provided"})
        lowered = expression.lower()
        if any(token in lowered for token in _BLOCKED_SUBSTRINGS):
            return json.dumps({"error": "Expression contains a blocked construct"})
        try:
            from simpleeval import SimpleEval
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "AI Calculator Tool requires simpleeval. Add simpleeval>=0.9 to "
                "the workflow environment, rebuild, then run again."
            ) from exc
        evaluator = SimpleEval(functions=_SAFE_MATH_FUNCS, names=_SAFE_MATH_NAMES)
        try:
            value = evaluator.eval(expression)
        except ZeroDivisionError:
            return json.dumps({"error": "Division by zero"})
        except OverflowError:
            return json.dumps({"error": "Result too large to represent"})
        except (ValueError, TypeError, KeyError, SyntaxError, NameError) as exc:
            return json.dumps({"error": f"Invalid expression: {exc}"})
        if isinstance(value, complex):
            if not self._allow_complex:
                return json.dumps({"error": "Result is complex. Enable allow_complex or reformulate."})
            return json.dumps({"result": str(value), "expression": expression})
        if isinstance(value, float):
            if math.isnan(value):
                return json.dumps({"result": None, "expression": expression, "note": "Result is not a number"})
            if math.isinf(value):
                return json.dumps({"result": None, "expression": expression, "note": "Result is infinite"})
            value = round(value, self._precision)
        try:
            return json.dumps({"result": value, "expression": expression})
        except ValueError as exc:
            return json.dumps({"error": f"Result too large to represent: {exc}"})


@node(
    name="AI Calculator Tool",
    id="ai_calculator_tool",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    requirements=["simpleeval>=0.9"],
    params={
        "name": {"description": "Tool name exposed to the model (snake_case)."},
        "description": {"widget": "textarea", "description": "What the tool does."},
        "precision": {"description": "Decimal places in the result (0-15)."},
        "allow_complex": {"widget": "toggle", "description": "Allow complex-number results."},
    },
)
def ai_calculator_tool(
    name: str = "calculate",
    description: str = "Evaluate a mathematical expression safely.",
    precision: int = 10,
    allow_complex: bool = False,
) -> ToolAdapter:
    """Supply a safe math-evaluation tool to a downstream AI Agent."""
    return CalculatorToolAdapter(
        name=name, description=description, precision=precision, allow_complex=allow_complex
    )
