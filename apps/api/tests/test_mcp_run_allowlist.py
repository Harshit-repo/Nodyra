"""An operator can restrict which workflows an MCP agent may execute.

``enable_mcp_tool`` sets ``mcp_enabled`` on a workflow — the operator
saying "an agent may invoke this one". It publishes the workflow as its own
named MCP tool.

But ``run_workflow`` takes any ``workflow_id`` and runs it, and never looked at
``mcp_enabled``. So the opt-in governed how a workflow was *advertised*, not
whether it could be *invoked*: an agent that knew or guessed an id could run any
workflow in the org — including ones that send mail, post to Slack, or call a
paid API — regardless of what the operator had opted in.

``MCP_RUN_REQUIRES_OPT_IN`` closes that. When enabled, ``run_workflow`` executes
only workflows the operator has exposed. It defaults to **off**, because turning
it on by default would break every existing integration on upgrade; the
recommended production posture is documented in ``docs/connect-mcp.md``.

The refusal names the workflow and says how to allow it, because an agent that
cannot tell "you may not" from "it is broken" will retry, and the operator will
see noise instead of a decision.
"""

from __future__ import annotations

import pytest

from app.config import Settings


def _settings(**kw) -> Settings:
    base = {"secret_key": "x" * 48}
    base.update(kw)
    return Settings(**base)


def test_the_setting_exists():
    assert hasattr(_settings(), "mcp_run_requires_opt_in")


def test_it_defaults_to_off_so_upgrades_do_not_break():
    """Existing MCP integrations call run_workflow on workflows that were never
    exposed as tools. Flipping this on by default would break them silently."""
    assert _settings().mcp_run_requires_opt_in is False


def test_it_can_be_enabled():
    assert _settings(mcp_run_requires_opt_in=True).mcp_run_requires_opt_in is True


class _Workflow:
    def __init__(self, *, mcp_enabled: bool, wid: str = "wf1", name: str = "Payroll"):
        self.id = wid
        self.name = name
        self.mcp_enabled = mcp_enabled


def _check(workflow, enabled: bool):
    from app.mcp.tools import assert_workflow_runnable_over_mcp

    return assert_workflow_runnable_over_mcp(workflow, require_opt_in=enabled)


def test_an_opted_in_workflow_runs_when_the_restriction_is_on():
    _check(_Workflow(mcp_enabled=True), True)  # must not raise


def test_a_workflow_that_was_not_opted_in_is_refused():
    """The behaviour the operator is buying."""
    from app.mcp.tools import McpToolError

    with pytest.raises(McpToolError) as excinfo:
        _check(_Workflow(mcp_enabled=False), True)
    message = str(excinfo.value)
    assert "Payroll" in message or "wf1" in message, (
        f"the refusal does not say which workflow was refused: {message!r}"
    )
    assert "enable_mcp_tool" in message, (
        f"the refusal does not say how to allow it: {message!r}"
    )


def test_nothing_changes_while_the_restriction_is_off():
    """Default deployments keep today's behaviour exactly."""
    _check(_Workflow(mcp_enabled=False), False)  # must not raise
    _check(_Workflow(mcp_enabled=True), False)


def test_the_refusal_is_a_tool_error_not_a_crash():
    """An MCP client should receive a normal tool error it can relay to its
    human, not a 500 that reads as an outage."""
    from app.mcp.tools import McpToolError

    with pytest.raises(McpToolError):
        _check(_Workflow(mcp_enabled=False), True)


def test_run_workflow_consults_the_guard():
    """Guard the guard: the helper existing is not the same as it being wired
    into the tool that needed it."""
    import inspect

    from app.mcp import tools

    source = inspect.getsource(tools.run_workflow_by_id)
    assert "assert_workflow_runnable_over_mcp" in source, (
        "run_workflow_by_id does not call the allowlist guard, so the setting "
        "has no effect on the path it was written for"
    )


def test_retry_run_consults_the_guard():
    """The allowlist has to cover every path that starts an execution.

    ``retry_run`` re-executes an existing run by id, and it reached the REST
    route directly. Verified against a running server with the restriction on:
    ``run_workflow`` on a revoked workflow was refused, and ``retry_run`` on an
    earlier run of that same workflow started a new one anyway.

    A door the operator believes is locked being open is worse than no door.
    """
    import inspect

    from app.mcp import tools

    source = inspect.getsource(tools._retry_run)
    assert "assert_workflow_runnable_over_mcp" in source, (
        "retry_run starts an execution without consulting the allowlist, so an "
        "agent holding any run id of a non-exposed workflow can run it anyway"
    )


def test_every_execution_entry_point_is_covered():
    """Guard the guard, generically: if a new tool is added that starts a run,
    this fails until it is either gated or explicitly listed as reviewed."""
    import inspect

    from app.mcp import tools

    reviewed = {
        "_run_workflow",  # delegates to run_workflow_by_id, which is gated
        "_retry_run",
        "_cancel_run",  # stops execution; nothing to allowlist
        "_resolve_run_approval",  # gated by approved_by_user, resumes not starts
    }
    starters = {
        name
        for name, fn in vars(tools).items()
        if name.startswith("_") and inspect.iscoroutinefunction(fn)
        and "run" in name
        and any(k in inspect.getsource(fn) for k in ("run_workflow_by_id", "retry_from_failure", "start_run"))
    }
    unreviewed = sorted(starters - reviewed)
    assert not unreviewed, (
        f"these tools can start an execution but were never checked against the "
        f"MCP allowlist: {unreviewed}"
    )


def test_the_setting_is_documented():
    """A security control nobody knows about protects nobody. An operator has
    to be able to find this from the page they read to wire up MCP."""
    from pathlib import Path

    doc = Path(__file__).resolve().parents[3] / "docs" / "connect-mcp.md"
    if not doc.exists():
        pytest.skip("docs/connect-mcp.md is not present in this checkout")
    text = doc.read_text(encoding="utf-8")

    assert "MCP_RUN_REQUIRES_OPT_IN" in text, (
        "the setting is not documented, so operators cannot discover it"
    )
    assert "enable_mcp_tool" in text, (
        "the docs do not say how to opt a workflow in, which is the other half "
        "of the instruction"
    )
