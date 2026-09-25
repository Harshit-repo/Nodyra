"""A param the UI hides must not be demanded when the node runs.

Consolidated integration nodes (Slack, OpenAI, Jira, GitLab, Google Sheets and
24 others) present one node with a resource/operation selector. Each operation
declares its own params, and the node manifest holds the *union* of them, with
``display_when`` recording which (resource, operation) pairs each param belongs
to. The editor uses that to show only the params for the selected operation.

The engine's required-param check did not. It walked every param in the union
and demanded any marked required, so ``required`` - which the spec authors mean
as "required *for this operation*" - was enforced as "required always".

The result, reproduced against a running server on a correctly configured node:

  slack   resource=message  operation=send
    -> missing required parameters: ts, timestamp, reaction_name, users
  openai  resource=chat     operation=create
    -> missing required parameters: input_text, prompt, name, instructions

Every one of those fields is hidden by the editor for the selected operation.
So the run failed naming fields the user was never shown and cannot fill in,
and it blocked a shipped template (webhook_to_slack) out of the box.

27 nodes and 114 params were in that state.
"""

from __future__ import annotations

import pytest

from nodyra.engine.node_exec import matches_display_when


@pytest.mark.parametrize(
    "display_when,params,expected",
    [
        (None, {}, True),
        ({}, {}, True),
        ({"param": "resource", "value": "message"}, {"resource": "message"}, True),
        ({"param": "resource", "value": "message"}, {"resource": "reaction"}, False),
        ({"param": "resource", "values": ["a", "b"]}, {"resource": "b"}, True),
        ({"param": "resource", "values": ["a", "b"]}, {"resource": "c"}, False),
        # AND group
        (
            {"conditions": [
                {"param": "resource", "value": "message"},
                {"param": "operation", "value": "send"},
            ]},
            {"resource": "message", "operation": "send"},
            True,
        ),
        (
            {"conditions": [
                {"param": "resource", "value": "message"},
                {"param": "operation", "value": "send"},
            ]},
            {"resource": "message", "operation": "update"},
            False,
        ),
        # OR of AND groups - the shape node_factory emits for a shared param
        (
            {"any": [
                {"conditions": [
                    {"param": "resource", "value": "message"},
                    {"param": "operation", "value": "send"},
                ]},
                {"conditions": [
                    {"param": "resource", "value": "message"},
                    {"param": "operation", "value": "update"},
                ]},
            ]},
            {"resource": "message", "operation": "update"},
            True,
        ),
        (
            {"any": [
                {"conditions": [
                    {"param": "resource", "value": "message"},
                    {"param": "operation", "value": "send"},
                ]},
            ]},
            {"resource": "reaction", "operation": "add"},
            False,
        ),
    ],
)
def test_matches_display_when(display_when, params, expected) -> None:
    """Must agree with apps/web/src/editor/node-details/displayRules.ts - the
    editor and the engine disagreeing about a field is the whole bug."""
    assert matches_display_when(display_when, params) is expected


def test_a_hidden_param_is_not_required() -> None:
    """The rule the fix exists for."""
    reaction_only = {"conditions": [
        {"param": "resource", "value": "reaction"},
        {"param": "operation", "value": "add"},
    ]}
    sending = {"resource": "message", "operation": "send"}
    assert matches_display_when(reaction_only, sending) is False


def test_a_visible_param_is_still_required() -> None:
    """Guard the guard: the fix must not make `required` meaningless."""
    send_only = {"conditions": [
        {"param": "resource", "value": "message"},
        {"param": "operation", "value": "send"},
    ]}
    sending = {"resource": "message", "operation": "send"}
    assert matches_display_when(send_only, sending) is True


def test_a_param_with_no_display_when_is_always_required() -> None:
    """Params shared by every operation (credentials) carry no display_when and
    must keep being enforced."""
    assert matches_display_when(None, {"resource": "message"}) is True
