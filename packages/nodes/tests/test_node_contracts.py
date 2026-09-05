"""Contract harness over the whole node registry (F-07).

An audit of the 512 registered nodes found 100 with no test of any kind —
including every Google Workspace node, HubSpot, Asana, ClickUp, all image and
archive handling, and core transforms like ``aggregate`` and ``data_group_by``.
Writing 100 bespoke tests would close today's gap and reopen it with the next
node someone adds.

This harness closes it structurally instead: it is parameterized over the
registry, so every node — including ones that do not exist yet — must satisfy
the same contract. Nothing here performs I/O or needs a credential, so it
covers integration nodes that cannot otherwise be exercised in CI.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

import pytest

import nodyra_nodes  # noqa: F401 — importing registers every node
from nodyra.sdk import NodeDef, registry


def _is_shipped(node: NodeDef) -> bool:
    """Was this node defined by the shipped package, or by a test fixture?

    ``registry`` is global and this module snapshots it at import time, so which
    nodes are present depends on what pytest imported first. Several tests
    register fixture nodes into it (e09_key_spy, eng1_slow, stream_body_test),
    and the full suite therefore saw 515 nodes where running this file alone
    sees 512 - which made the ratchets below order-dependent. The same tree
    could pass alone and fail in the suite, reporting a number that described
    the test run rather than the product.

    Shipped nodes are either defined in ``nodyra_nodes`` or built by the
    integrations factory, which produces callables carrying no ``__module__``.
    A fixture defined inside a test module has that module's name.
    """
    module = getattr(node.func, "__module__", "") or ""
    return not module or module.split(".")[0] == "nodyra_nodes"


ALL_NODES: list[tuple[str, NodeDef]] = sorted(
    (node_id, node) for node_id, node in registry._nodes.items() if _is_shipped(node)
)

# Parameters the engine supplies rather than the user; they are legitimately
# absent from a manifest's declared params.
ENGINE_SUPPLIED = frozenset({"input", "inputs", "context", "ctx", "credentials"})

# PEP 508: distribution name, optional extras, optional version specifiers.
REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(\[[A-Za-z0-9._,-]+\])?"
    r"((===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._*+-]+"
    r"(\s*,\s*(===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._*+-]+)*)?"
    r"(\s*;\s*.+)?$"  # optional PEP 508 environment marker
)

NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def _ids() -> list[str]:
    return [node_id for node_id, _ in ALL_NODES]


def test_the_registry_is_populated() -> None:
    """Guard the guard: an empty registry would make every case below vacuous."""
    assert len(ALL_NODES) > 400, f"only {len(ALL_NODES)} nodes registered"


def test_node_ids_are_unique() -> None:
    ids = _ids()
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_identity_is_well_formed(node_id: str, node: NodeDef) -> None:
    """A node the palette cannot label is a node nobody finds."""
    manifest = node.manifest
    assert NODE_ID_RE.match(node_id), f"{node_id}: ids are lower_snake_case"
    assert manifest.id == node_id, f"{node_id}: manifest id is {manifest.id!r}"
    assert manifest.name.strip(), f"{node_id}: no display name"
    assert manifest.category.strip(), f"{node_id}: no category"


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_has_a_usable_description(node_id: str, node: NodeDef) -> None:
    """The description is the node's entire documentation in the palette, and
    for tool-enabled nodes it is what the model reads to decide whether to call
    it. A one-word description is a bug in both places."""
    description = (node.manifest.description or "").strip()
    assert description, f"{node_id}: no description"
    assert len(description) >= 15, f"{node_id}: description too thin: {description!r}"


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_manifest_serialises_to_json(node_id: str, node: NodeDef) -> None:
    """The manifest crosses the wire to the editor and to MCP clients. A value
    that will not serialise breaks the node list for every node, not just this
    one."""
    payload = json.dumps(node.manifest.model_dump(mode="json"))
    assert json.loads(payload)["id"] == node_id


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_declared_params_exist_on_the_function(node_id: str, node: NodeDef) -> None:
    """A manifest param with no matching argument is a control that silently
    does nothing when the user sets it."""
    if node.accepts_var_keyword:
        pytest.skip("accepts **kwargs; any declared param is bindable")
    accepted = set(inspect.signature(node.func).parameters)
    declared = {p.name for p in node.manifest.params}
    orphans = sorted(declared - accepted - ENGINE_SUPPLIED)
    assert not orphans, (
        f"{node_id}: manifest declares params the function ignores: {orphans}"
    )


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_function_arguments_are_declared(node_id: str, node: NodeDef) -> None:
    """The mirror of the above: an argument with no manifest entry is a knob
    with no UI, unreachable from the editor and invisible to MCP."""
    # An argument is reachable if it is a manifest param (an editor control),
    # a declared input port (wired from another node, as ai_agent's `model` and
    # merge's `input_a` are), or engine-supplied.
    reachable = (
        {p.name for p in node.manifest.params}
        | {p.name for p in node.manifest.inputs}
        | set(node.wires or {})
        | ENGINE_SUPPLIED
    )
    undeclared = sorted(
        name
        for name, param in inspect.signature(node.func).parameters.items()
        if name not in reachable
        and param.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    )
    assert not undeclared, (
        f"{node_id}: function takes params reachable from neither the editor "
        f"nor an input port: {undeclared}"
    )


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_params_are_documented_and_typed(node_id: str, node: NodeDef) -> None:
    seen: set[str] = set()
    for param in node.manifest.params:
        assert param.name, f"{node_id}: a param has no name"
        assert param.name not in seen, f"{node_id}: duplicate param {param.name!r}"
        seen.add(param.name)
        assert param.type, f"{node_id}.{param.name}: no type"


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_choices_and_defaults_agree(node_id: str, node: NodeDef) -> None:
    """A default outside its own choice list renders as an empty dropdown."""
    for param in node.manifest.params:
        if not param.choices or param.default in (None, ""):
            continue
        values = {
            choice.get("value") if isinstance(choice, dict) else choice
            for choice in param.choices
        }
        # Strict equality, deliberately: the editor compares the option values
        # it was handed, so an int default against string choices leaves the
        # dropdown with nothing selected even though the values look equal.
        assert param.default in values, (
            f"{node_id}.{param.name}: default {param.default!r} is not among "
            f"its choices {sorted(repr(v) for v in values if v is not None)}"
        )


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_ports_are_declared_and_named(node_id: str, node: NodeDef) -> None:
    """Every executable node needs at least one output or nothing downstream
    can consume it."""
    manifest = node.manifest
    for port in [*manifest.inputs, *manifest.outputs]:
        assert port.name, f"{node_id}: an unnamed port"
    output_names = [p.name for p in manifest.outputs]
    assert len(output_names) == len(set(output_names)), f"{node_id}: duplicate outputs"
    if manifest.role in ("executable", "trigger"):
        assert manifest.outputs, f"{node_id}: no outputs; nothing can consume it"


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_requirements_are_installable_specifiers(node_id: str, node: NodeDef) -> None:
    """Requirements are interpolated into ``uv pip install`` when an environment
    is built. A malformed specifier fails the build for every node in that
    environment, and shell metacharacters would be a build-time injection."""
    for requirement in node.manifest.requirements:
        assert REQUIREMENT_RE.match(requirement), (
            f"{node_id}: {requirement!r} is not a plain PEP 508 specifier"
        )


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_deprecated_nodes_point_somewhere_real(node_id: str, node: NodeDef) -> None:
    """A deprecation that names a replacement which does not exist strands the
    user on a node they have been told to stop using."""
    manifest = node.manifest
    if manifest.deprecated and manifest.replacement_id:
        assert manifest.replacement_id in registry._nodes, (
            f"{node_id}: replacement {manifest.replacement_id!r} is not registered"
        )


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_tool_enabled_nodes_describe_themselves(node_id: str, node: NodeDef) -> None:
    """``usable_as_tool`` nodes are handed to an LLM, which has only the
    description to go on when deciding whether to call one."""
    manifest = node.manifest
    if not manifest.usable_as_tool:
        return
    assert len(manifest.description.strip()) >= 25, (
        f"{node_id}: tool description is too thin for a model to choose it"
    )


def _undocumented_tool_params() -> dict[str, list[str]]:
    """Tool-exposed params with no description, by node."""
    gaps: dict[str, list[str]] = {}
    for node_id, node in ALL_NODES:
        if not node.manifest.usable_as_tool:
            continue
        missing = sorted(
            p.name
            for p in node.manifest.params
            if p.name not in ENGINE_SUPPLIED and not (p.description or "").strip()
        )
        if missing:
            gaps[node_id] = missing
    return gaps


# Ratchet, not a target. A tool param with no description reaches the model as a
# bare name, so it has to guess what the param means.
#
# This started at 702 across 188 nodes. `ParamSpec._describe_if_blank` now fills
# what can be filled without inventing anything — a param's own declared choices
# ("One of: create, get, list, delete.") and a vocabulary of names that mean the
# same thing wherever they appear — which took it to 332 across 111. The rest are
# genuinely provider-specific and need a node author, because a confident wrong
# description is worse than none: the model believes it.
#
# So the remainder is frozen: it may fall, never rise. A new tool node with
# undocumented params fails immediately. Drive these to zero and delete the
# ratchet.
_UNDOCUMENTED_TOOL_PARAM_BUDGET = 332
_TOOL_NODES_WITH_GAPS_BUDGET = 111


def test_no_new_undocumented_tool_params() -> None:
    gaps = _undocumented_tool_params()
    total = sum(len(names) for names in gaps.values())

    assert total <= _UNDOCUMENTED_TOOL_PARAM_BUDGET, (
        f"{total} tool params have no description, up from "
        f"{_UNDOCUMENTED_TOOL_PARAM_BUDGET}. A tool param reaches the model as a "
        "bare name — describe every param on any tool-enabled node you add."
    )
    assert len(gaps) <= _TOOL_NODES_WITH_GAPS_BUDGET, (
        f"{len(gaps)} tool nodes have undocumented params, up from "
        f"{_TOOL_NODES_WITH_GAPS_BUDGET}."
    )


def test_the_ratchet_is_tightened_when_the_debt_is_paid_down() -> None:
    """A budget left above the real number stops catching regressions: the debt
    could grow back to the stale ceiling unnoticed. Lower the constants whenever
    descriptions are added."""
    gaps = _undocumented_tool_params()
    total = sum(len(names) for names in gaps.values())
    assert total == _UNDOCUMENTED_TOOL_PARAM_BUDGET, (
        f"undocumented tool params are now {total}; lower "
        f"_UNDOCUMENTED_TOOL_PARAM_BUDGET to {total} to lock the improvement in"
    )
    assert len(gaps) == _TOOL_NODES_WITH_GAPS_BUDGET, (
        f"tool nodes with gaps are now {len(gaps)}; lower "
        f"_TOOL_NODES_WITH_GAPS_BUDGET to {len(gaps)}"
    )


@pytest.mark.parametrize("node_id,node", ALL_NODES, ids=_ids())
def test_callable_and_introspectable(node_id: str, node: NodeDef) -> None:
    """The engine inspects every node's signature to bind ports; a builtin or a
    C function would raise at bind time rather than at import."""
    assert callable(node.func), f"{node_id}: not callable"
    assert inspect.signature(node.func) is not None
    assert inspect.iscoroutinefunction(node.func) == bool(node.is_async), (
        f"{node_id}: is_async disagrees with the function"
    )


def test_every_shipped_node_is_covered_by_this_harness() -> None:
    """The contract must apply to the whole shipped library, not a curated
    subset — that curation is exactly how 100 nodes went untested.

    Compared against what ``nodyra_nodes`` registers rather than against the
    live registry: the registry is process-global, so a sibling test module
    that registers a fixture node (``eng1_slow``, ``e09_key_spy``) would
    otherwise make this fail purely on test ordering.
    """
    covered = {node_id for node_id, _ in ALL_NODES}
    shipped = {
        node_id
        for node_id, node in registry._nodes.items()
        if str(getattr(node.func, "__module__", "") or "").startswith("nodyra_nodes")
    }
    assert shipped, "no shipped nodes discovered — the check would be vacuous"

    missing = sorted(shipped - covered)
    assert not missing, (
        f"nodes registered by nodyra_nodes but not covered by this harness: "
        f"{missing}. ALL_NODES is snapshotted at import; a node registered "
        "later needs importing before that snapshot."
    )


def test_node_ids_do_not_collide_case_insensitively() -> None:
    """Node ids appear in URLs and MCP tool names, where two ids differing only
    by case are indistinguishable to the caller."""
    lowered: dict[str, str] = {}
    for node_id in _ids():
        previous = lowered.setdefault(node_id.lower(), node_id)
        assert previous == node_id, f"case-collision: {previous!r} vs {node_id!r}"


def test_param_types_come_from_a_closed_set() -> None:
    """The editor renders a control per param type. An unrecognised type falls
    back to a bare text box, which silently breaks structured inputs."""
    known: set[Any] = {
        "string", "number", "integer", "boolean", "json", "select",
        "multiselect", "code", "date", "datetime", "file", "credential",
        "expression", "text", "array", "object", "secret", "color", "any",
    }
    seen = {p.type for _, node in ALL_NODES for p in node.manifest.params}
    unknown = sorted(t for t in seen if t not in known)
    assert not unknown, f"unrecognised param types: {unknown}"
