"""The MCP approval gate must cover every tool that changes what code runs.

``_require_explicit_mcp_approval`` makes a tool refuse until the caller passes
``approved_by_user=true``, so an LLM has to go back to its human before doing
something consequential. Sixteen of the sixty-three tools carry it.

Found by driving the MCP server as an agent would: the gate had holes that a
caller reaches without doing anything clever.

  set_workflow_graph   gated    — writes a graph, which contains code
  update_code          UNGATED  — writes code into a node directly
  create_code_node     UNGATED  — adds a node containing code

Same capability, opposite treatment. Verified against a running server: an
``update_code`` call with no approval flag replaced a node's body with
``import os`` and returned graph_revision 2. Followed by ``run_workflow``, which
is also ungated, that is arbitrary code written and executed without the human
the gate exists to involve.

  toggle_schedule      gated    — decides whether a schedule fires
  toggle_workflow      UNGATED  — decides whether the workflow is live at all

  resolve_run_approval UNGATED  — resolves a *pending human approval*

The last is the sharpest: run approvals exist so an agent pauses for a person
before a side-effecting tool call. A tool that resolves them, reachable by the
same agent without a human in the loop, makes the pause self-satisfiable.

The flag is a convention, not a wall — a determined caller can always assert it
falsely. That is precisely why it should be consistent: its value is in making
the consequential moments legible to the human on the other end, and a gate with
holes teaches everyone to ignore it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "app" / "mcp" / "tools.py"
SOURCE = TOOLS.read_text(encoding="utf-8")


def gated_tools() -> set[str]:
    return set(re.findall(r'_require_explicit_mcp_approval\(args, "([a-z_]+)"', SOURCE))


def registered_tools() -> set[str]:
    return set(re.findall(r'name="([a-z_]+)",', SOURCE))


def test_the_gate_is_actually_in_use():
    """Guard the guard: if the helper is renamed or removed, every assertion
    below would pass by comparing two empty sets."""
    gated = gated_tools()
    assert len(gated) >= 10, f"only {len(gated)} gated tools found: {sorted(gated)}"
    assert "set_workflow_graph" in gated, "the reference gated tool is no longer gated"


@pytest.mark.parametrize(
    "tool,because",
    [
        ("update_code", "writes executable Python into a node, like set_workflow_graph"),
        ("create_code_node", "adds a node containing executable Python"),
        ("toggle_workflow", "decides whether the workflow runs live, like toggle_schedule"),
        (
            "resolve_run_approval",
            "resolves a pending human approval; an agent must not satisfy its own gate",
        ),
    ],
)
def test_consequential_tools_require_explicit_approval(tool, because):
    assert tool in registered_tools(), f"{tool} is no longer a registered MCP tool"
    assert tool in gated_tools(), (
        f"{tool} does not require approved_by_user, but it {because}. "
        f"An MCP client reaches it without involving the human the gate exists for."
    )


def test_reads_are_not_gated():
    """The gate must stay meaningful. If everything requires approval, callers
    pass the flag reflexively and it stops carrying information."""
    gated = gated_tools()
    for read_only in ("list_workflows", "get_workflow", "get_run", "search_node_catalog"):
        assert read_only not in gated, (
            f"{read_only} only reads; gating it trains callers to assert approval "
            f"by default, which is how a gate becomes decoration"
        )


def test_the_documented_policy_matches_the_code():
    """An operator wiring an LLM into their instance needs to know what it can
    do unattended. The tool list was documented; the approval boundary was not.
    """
    doc = Path(__file__).resolve().parents[3] / "docs" / "connect-mcp.md"
    if not doc.exists():
        pytest.skip("docs/connect-mcp.md is not present in this checkout")
    text = doc.read_text(encoding="utf-8")

    assert "approved_by_user" in text, (
        "docs/connect-mcp.md never mentions approved_by_user, so an operator "
        "cannot tell which tools an agent may use without asking them"
    )
    missing = [tool for tool in sorted(gated_tools()) if tool not in text]
    assert not missing, f"gated tools absent from the documented policy: {missing}"
