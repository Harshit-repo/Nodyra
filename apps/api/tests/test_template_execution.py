"""Starter templates must actually run, not merely parse.

``scripts/validate_templates.py`` checks JSON shape, ids, SemVer, screenshots
and graph *structure*. It has never executed a template. So
``text_extract_regex`` shipped with a ``code`` node that called ``.get()`` on
the output of ``regex_extract`` — which is ``re.findall``, and a two-group
pattern yields tuples, not dicts, named groups included. Every run of that
template died with ``AttributeError: 'tuple' object has no attribute 'get'``,
and every check in the repository stayed green.

A starter template is the first thing a new user clicks. One that dies on
contact is worse than no template at all.

Templates that reach the network, send mail, or read files the repository does
not ship cannot run here. This executes the rest — the ones whose behaviour is
entirely determined by the graph — which is exactly where the bug was.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import nodyra_nodes  # noqa: F401  (importing registers the built-in nodes)
from nodyra.engine.scheduler import execute
from nodyra.models import WorkflowGraph
from nodyra.sdk import registry

TEMPLATE_DIR = (
    Path(__file__).resolve().parents[3] / "apps" / "api" / "app" / "data" / "templates"
)

#: Node types whose behaviour depends only on the graph — no sockets, no SMTP,
#: no files outside what the node itself creates. A template built solely from
#: these can be executed here and must succeed.
SELF_CONTAINED = {
    "manual_trigger",
    "error_trigger",
    "webhook_trigger",
    "code",
    "if",
    "no_op",
    "limit",
    "sort",
    "regex_extract",
    "regex_replace",
    # schema_validate is deliberately absent: it needs the dataset context
    # the API supplies, and raises "datasets are not available in this
    # execution context" here. That is a limit of this harness, not a bug.
    "string_normalize",
    "remove_duplicates",
    "edit_fields",
}


def _templates() -> list[tuple[str, dict]]:
    out = []
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        out.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
    return out


def _is_self_contained(template: dict) -> bool:
    types = {node["type"] for node in template["graph"]["nodes"]}
    return types <= SELF_CONTAINED


RUNNABLE = [(name, t) for name, t in _templates() if _is_self_contained(t)]
SKIPPED = [(name, t) for name, t in _templates() if not _is_self_contained(t)]


def test_there_is_something_to_run():
    """Guard the guard. If the allowlist or the catalogue drifts so that
    nothing is executable, these tests would pass by testing nothing."""
    assert len(RUNNABLE) >= 3, (
        f"only {len(RUNNABLE)} template(s) are executable offline: "
        f"{[n for n, _ in RUNNABLE]}"
    )


def test_the_template_that_broke_is_covered():
    """text_extract_regex is the one that shipped broken. If it ever stops
    being executable here, this suite has lost the case it was written for."""
    assert "text_extract_regex" in [name for name, _ in RUNNABLE]


@pytest.mark.parametrize("name,template", RUNNABLE, ids=[n for n, _ in RUNNABLE])
def test_a_starter_template_runs_to_completion(name, template):
    graph = WorkflowGraph.model_validate(template["graph"])
    result = asyncio.run(execute(graph, registry))

    failures = {
        node_id: outcome
        for node_id, outcome in (result.nodes or {}).items()
        if outcome.status.value == "error"
    }
    assert not failures, (
        f"{name}: node(s) failed — "
        + "; ".join(f"{k}: {str(getattr(v, 'error', v))[:200]}" for k, v in failures.items())
    )


@pytest.mark.parametrize("name,template", RUNNABLE, ids=[n for n, _ in RUNNABLE])
def test_a_starter_template_produces_output(name, template):
    """Completing is not the same as working. A template whose every node
    returns nothing would satisfy the test above while being useless."""
    graph = WorkflowGraph.model_validate(template["graph"])
    result = asyncio.run(execute(graph, registry))

    produced = [
        node_id
        for node_id, outcome in (result.nodes or {}).items()
        if outcome.outputs
    ]
    assert produced, f"{name}: no node produced any output"


@pytest.mark.parametrize("name,template", RUNNABLE, ids=[n for n, _ in RUNNABLE])
def test_a_starter_template_reaches_an_end_of_the_graph(name, template):
    """At least one leaf node must actually run.

    Not erroring is too weak a bar. ``conditional_routing`` wired both of its
    branch edges with ``sourceHandle``, a key ``Edge`` does not accept — pydantic
    dropped it and ``source_output`` silently defaulted to ``main``. The ``if``
    node emits on ``true``/``false``, so *both* downstream nodes were skipped
    waiting for a branch that never fired.

    Nothing errored. Two nodes produced output. The template simply did nothing
    it promised, and the two checks above were both satisfied. A leaf that never
    runs is the signature of that failure, so assert one does.
    """
    graph = WorkflowGraph.model_validate(template["graph"])
    result = asyncio.run(execute(graph, registry))

    with_outgoing = {edge.source for edge in graph.edges}
    leaves = [node.id for node in graph.nodes if node.id not in with_outgoing]
    assert leaves, f"{name}: graph has no terminal node — is it a cycle?"

    reached = [
        node_id
        for node_id in leaves
        if (outcome := (result.nodes or {}).get(node_id))
        and outcome.status.value == "success"
    ]
    assert reached, (
        f"{name}: no terminal node ran. Leaves {leaves} finished as "
        + ", ".join(
            f"{n}={getattr((result.nodes or {}).get(n), 'status', None)}" for n in leaves
        )
        + ". A branch edge pointing at a port the source never emits does exactly this."
    )


def test_the_skipped_templates_are_skipped_for_a_stated_reason():
    """Whatever is not executed here should be excluded because it reaches
    outside the process, not because nobody looked at it."""
    for name, template in SKIPPED:
        types = {node["type"] for node in template["graph"]["nodes"]}
        external = types - SELF_CONTAINED
        assert external, f"{name} is excluded but uses only self-contained nodes"
