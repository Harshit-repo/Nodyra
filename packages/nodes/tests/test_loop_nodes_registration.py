"""Loop node registration + deprecation."""
from __future__ import annotations

import nodyra_nodes  # noqa: F401 - registers nodes
from nodyra.sdk import registry


def test_loop_start_registered_with_ports_and_params():
    nd = registry.get("loop_start")
    m = nd.manifest
    assert [o.name for o in m.outputs] == ["item", "index", "state"]
    pnames = {p.name for p in m.params}
    assert {"concurrency", "on_error", "max_rows"} <= pnames


def test_loop_start_has_mode_and_per_mode_params():
    nd = registry.get("loop_start")
    pnames = {p.name for p in nd.manifest.params}
    assert {
        "mode", "batch_size", "group_key", "count",
        "initial", "condition", "max_iterations", "on_max_iterations",
    } <= pnames
    mode = next(p for p in nd.manifest.params if p.name == "mode")
    assert set(mode.choices or []) == {
        "each", "batch", "group", "range", "window", "while", "until",
    }
    assert {"start", "step", "accumulate"} <= pnames


def test_loop_end_registered_with_outputs_and_hidden_pair_param():
    nd = registry.get("loop_end")
    m = nd.manifest
    assert [o.name for o in m.outputs] == ["main", "results", "errors"]
    pnames = {p.name for p in m.params}
    assert {"loop_start_id", "output_mode", "conditional_output"} <= pnames


def test_loop_over_items_is_deprecated_pointing_to_loop_start():
    nd = registry.get("loop_over_items")
    assert nd.manifest.deprecated is True
    assert nd.manifest.replacement_id == "loop_start"


def test_loop_over_items_still_executes():
    # Deprecated, but must keep working for existing graphs.
    from nodyra_nodes.builtin import loop_over_items
    out = loop_over_items(input=[1, 2, 3])
    assert out["item"] == [1, 2, 3]
    assert out["done"]["count"] == 3
