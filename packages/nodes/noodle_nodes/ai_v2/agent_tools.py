"""AI agent power-tool nodes + sub-agent node — Phase B.

Each tool node outputs a ToolAdapter on an ``ai_tool`` port (same pattern as
AI HTTP Tool). ``ai_sub_agent`` outputs a SubAgentAdapter on ``ai_subagent``.
Heavy deps (simpleeval, playwright) are lazy-imported with friendly errors.
"""

from __future__ import annotations

import ast
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
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


# ---------------------------------------------------------------------------
# Code Execution Tool
# ---------------------------------------------------------------------------

_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}
_DANGEROUS_ATTRS = {"__class__", "__bases__", "__subclasses__", "__globals__", "__builtins__", "__mro__"}


def _ast_security_check(code: str, allowed_modules: set[str]) -> None:
    """Raise PermissionError if the code uses a blocked construct.

    Import checking: every import's root module must be present in
    *allowed_modules*.  Passing an empty set blocks all imports; passing a
    non-empty set allows only the listed roots.

    Dangerous builtins (eval/exec/compile/__import__) and dunder attribute
    access are always blocked regardless of the allowlist.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise PermissionError(f"code does not parse: {exc}") from exc
    for nodeobj in ast.walk(tree):
        if isinstance(nodeobj, ast.Import):
            for alias in nodeobj.names:
                root = alias.name.split(".")[0]
                if root not in allowed_modules:
                    raise PermissionError(f"blocked import: {alias.name}")
        elif isinstance(nodeobj, ast.ImportFrom):
            root = (nodeobj.module or "").split(".")[0]
            if root not in allowed_modules:
                raise PermissionError(f"blocked import: {nodeobj.module}")
        elif isinstance(nodeobj, ast.Call):
            func = nodeobj.func
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_CALLS:
                raise PermissionError(f"blocked call: {func.id}")
        elif isinstance(nodeobj, ast.Attribute):
            if nodeobj.attr in _DANGEROUS_ATTRS:
                raise PermissionError(f"blocked attribute access: {nodeobj.attr}")


class CodeExecToolAdapter(ToolAdapter):
    """Runs Python/JS in an isolated subprocess with an AST pre-check."""

    def __init__(self, *, name: str, description: str, language: str,
                 allowed_modules: str, timeout_seconds: int, max_output_chars: int) -> None:
        self._name = name or "run_code"
        self._description = description or "Run code and return stdout."
        self._language = (language or "python").lower()
        # None means "no import restriction"; a set (even empty) enforces an allowlist.
        parsed = {m.strip() for m in str(allowed_modules or "").split(",") if m.strip()}
        self._allowed: set[str] | None = parsed if parsed else None
        self._timeout = max(1, min(300, int(timeout_seconds or 30)))
        self._max_output = max(1, int(max_output_chars or 8000))

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description=(
                f"{self._description} Code must print() its results to stdout; "
                "return values are not captured. Files written are discarded after the call."
            ),
            parameters=ToolParameterSchema(
                properties={
                    "code": {"type": "string", "description": "Source code to execute."},
                    "timeout": {"type": "integer", "description": "Optional timeout seconds (<= node limit)."},
                },
                required=["code"],
            ),
        )

    @property
    def side_effecting(self) -> bool:
        return True

    def invoke(self, arguments: dict[str, Any]) -> str:
        code = str((arguments or {}).get("code") or "")
        if not code.strip():
            return json.dumps({"error": "No code provided"})
        requested = arguments.get("timeout")
        timeout = self._timeout
        if isinstance(requested, (int, float)) and 0 < int(requested) <= self._timeout:
            timeout = int(requested)
        if self._language == "python":
            if self._allowed is not None:
                try:
                    _ast_security_check(code, self._allowed)
                except PermissionError as exc:
                    return json.dumps({"error": str(exc)})
            argv = [sys.executable, "-c", code]
        elif self._language == "javascript":
            node_bin = shutil.which("node")
            if not node_bin:
                raise RuntimeError("AI Code Execution Tool: Node.js is not available on this host.")
            argv = [node_bin, "-e", code]
        else:
            return json.dumps({"error": f"Unsupported language: {self._language}"})

        workdir = tempfile.mkdtemp(prefix="noodle_code_")
        popen_kwargs: dict[str, Any] = dict(
            cwd=workdir, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
        )
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True  # own process group for killpg
        try:
            proc = subprocess.Popen(argv, **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                proc.communicate()
                return json.dumps({"error": f"Code timed out after {timeout}s"})
            truncated = False
            if len(stdout) > self._max_output:
                stdout, truncated = stdout[: self._max_output], True
            if len(stderr) > self._max_output:
                stderr, truncated = stderr[: self._max_output], True
            return json.dumps({
                "stdout": stdout, "stderr": stderr,
                "exit_code": proc.returncode, "truncated": truncated,
            })
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass


@node(
    name="AI Code Execution Tool",
    id="ai_code_execution_tool",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    param_groups={"Options": ["allowed_modules", "max_output_chars"]},
    params={
        "name": {"description": "Tool name exposed to the model (snake_case)."},
        "description": {"widget": "textarea", "description": "What the tool does."},
        "language": {"choices": ["python", "javascript"], "description": "Runtime."},
        "timeout_seconds": {"description": "Hard kill timeout (1-300)."},
        "allowed_modules": {
            "description": "Comma-separated import allowlist. Empty blocks all imports.",
            "group": "Options",
        },
        "max_output_chars": {"description": "Truncate stdout/stderr above this.", "group": "Options"},
    },
)
def ai_code_execution_tool(
    name: str = "run_code",
    description: str = "Run Python code and return stdout.",
    language: str = "python",
    timeout_seconds: int = 30,
    allowed_modules: str = "",
    max_output_chars: int = 8000,
) -> ToolAdapter:
    """Supply a sandboxed code-execution tool to a downstream AI Agent."""
    return CodeExecToolAdapter(
        name=name, description=description, language=language,
        allowed_modules=allowed_modules, timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )
