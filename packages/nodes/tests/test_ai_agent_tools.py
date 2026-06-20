from __future__ import annotations

import json

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.sdk import registry
from noodle_nodes.ai_v2.agent_tools import CalculatorToolAdapter, CodeExecToolAdapter, _ast_security_check


def _result(adapter, **args) -> dict:
    return json.loads(adapter.invoke(args))


def test_calc_basic_arithmetic() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert _result(adapter, expression="2 + 2")["result"] == 4.0


def test_calc_sqrt() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert _result(adapter, expression="sqrt(144)")["result"] == 12.0


def test_calc_trig() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert round(_result(adapter, expression="sin(pi/2)")["result"], 6) == 1.0


def test_calc_blocks_import() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert "error" in _result(adapter, expression="__import__('os')")


def test_calc_division_by_zero() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert _result(adapter, expression="1/0")["error"] == "Division by zero"


def test_calc_overflow() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    out = _result(adapter, expression="10**10000")
    assert "error" in out


def test_calc_empty_expression() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert _result(adapter, expression="")["error"] == "No expression provided"


def test_calc_factorial_capped() -> None:
    adapter = CalculatorToolAdapter(name="calculate", description="", precision=10, allow_complex=False)
    assert "error" in _result(adapter, expression="factorial(5000)")


def test_calculator_node_registered_typed_port() -> None:
    manifest = registry.get("ai_calculator_tool").manifest
    out = next(o for o in manifest.outputs if o.name == "tool")
    assert out.data_kind == "ai_tool"


# ---------------------------------------------------------------------------
# Code Execution Tool tests
# ---------------------------------------------------------------------------


def _code(adapter, code, **extra) -> dict:
    return json.loads(adapter.invoke({"code": code, **extra}))


def _py_adapter(**kw):
    defaults = dict(name="run_code", description="", language="python",
                    allowed_modules="", timeout_seconds=10, max_output_chars=8000)
    defaults.update(kw)
    return CodeExecToolAdapter(**defaults)


def test_code_exec_simple_stdout() -> None:
    assert _code(_py_adapter(), "print(1 + 1)")["stdout"].strip() == "2"


def test_code_exec_captures_stderr() -> None:
    out = _code(_py_adapter(allowed_modules="sys"), "import sys; sys.stderr.write('boom')")
    assert "boom" in out["stderr"]


def test_code_exec_nonzero_exit_no_raise() -> None:
    out = _code(_py_adapter(allowed_modules="sys"), "import sys; sys.exit(3)")
    assert out["exit_code"] == 3


def test_code_exec_timeout_kills_process() -> None:
    out = _code(_py_adapter(timeout_seconds=1), "while True:\n    pass")
    assert "error" in out and "timed out" in out["error"].lower()


def test_ast_blocks_unlisted_import() -> None:
    with pytest.raises(PermissionError):
        _ast_security_check("import os", set())


def test_ast_blocks_eval() -> None:
    with pytest.raises(PermissionError):
        _ast_security_check("eval('1')", set())


def test_ast_blocks_dunder_import_call() -> None:
    with pytest.raises(PermissionError):
        _ast_security_check("__import__('os')", set())


def test_ast_blocks_dunder_attribute() -> None:
    with pytest.raises(PermissionError):
        _ast_security_check("().__class__.__bases__", set())


def test_ast_allows_listed_import() -> None:
    _ast_security_check("import math\nprint(math.pi)", {"math"})  # no raise


def test_code_exec_allowlist_permits_math() -> None:
    out = _code(_py_adapter(allowed_modules="math"), "import math\nprint(math.sqrt(9))")
    assert out["stdout"].strip() == "3.0"


def test_code_exec_blocked_import_returns_error() -> None:
    # allowed_modules="math" means only math is allowed; os is blocked by AST check.
    out = _code(_py_adapter(allowed_modules="math"), "import os\nprint(os.getcwd())")
    assert "error" in out


def test_code_exec_caps_output() -> None:
    out = _code(_py_adapter(max_output_chars=50), "print('x' * 5000)")
    assert out["truncated"] is True
    assert len(out["stdout"]) <= 50


def test_code_exec_node_registered_typed_port() -> None:
    manifest = registry.get("ai_code_execution_tool").manifest
    out = next(o for o in manifest.outputs if o.name == "tool")
    assert out.data_kind == "ai_tool"
