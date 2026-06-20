from __future__ import annotations

import json

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.sdk import registry
from noodle_nodes.ai_v2.agent_tools import CalculatorToolAdapter


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
