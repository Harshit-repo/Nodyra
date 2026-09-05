"""A shipped template's code must survive the data its own URL returns.

Two templates extracted rows with:

    rows = (input or {}).get('body') or input or []

which calls ``.get`` on whatever ``input`` is. ``http_request`` returns the
decoded JSON body, so for an endpoint that returns a JSON *array* - which is
exactly what both templates' hard-coded URLs return
(jsonplaceholder.typicode.com/users and /posts) - ``input`` is a list and the
first line raises:

    AttributeError: 'list' object has no attribute 'get'

Neither template had ever run. Found by instantiating all 16 shipped templates
and running them.

These tests execute the template's own code against both shapes rather than
calling the network, so they are deterministic and still fail if the code
regresses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "templates"

# (template id, node id, an input shape http_request can produce)
LIST_RETURNING_TEMPLATES = [
    ("json_api_to_csv", "select"),
    ("scheduled_dataset_snapshot", "recent"),
]

SAMPLE_ROWS = [
    {"id": 1, "name": "Ada", "email": "ada@example.com"},
    {"id": 2, "name": "Grace", "email": "grace@example.com"},
]


def _code_for(template_id: str, node_id: str) -> str:
    path = TEMPLATE_DIR / f"{template_id}.json"
    if not path.exists():
        pytest.skip(f"{path.name} is not present in this checkout")
    graph = json.loads(path.read_text(encoding="utf-8"))["graph"]
    node = next(n for n in graph["nodes"] if n["id"] == node_id)
    return node["params"]["code"]


def _run(code: str, value):
    namespace = {"input": value}
    exec(compile(code, "<template>", "exec"), namespace)  # noqa: S102
    return namespace["output"]


@pytest.mark.parametrize("template_id,node_id", LIST_RETURNING_TEMPLATES)
def test_template_code_handles_a_bare_list(template_id: str, node_id: str) -> None:
    """The shape these templates' own URLs actually return."""
    result = _run(_code_for(template_id, node_id), list(SAMPLE_ROWS))
    assert isinstance(result, list)
    assert len(result) == 2


@pytest.mark.parametrize("template_id,node_id", LIST_RETURNING_TEMPLATES)
def test_template_code_handles_a_body_envelope(template_id: str, node_id: str) -> None:
    """The shape the code was written for must keep working."""
    result = _run(_code_for(template_id, node_id), {"body": list(SAMPLE_ROWS)})
    assert isinstance(result, list)
    assert len(result) == 2


@pytest.mark.parametrize("template_id,node_id", LIST_RETURNING_TEMPLATES)
def test_template_code_handles_nothing(template_id: str, node_id: str) -> None:
    """An empty response must produce an empty result, not an exception."""
    for empty in (None, {}, []):
        assert _run(_code_for(template_id, node_id), empty) == []


def test_the_generator_and_the_shipped_json_agree() -> None:
    """scripts/generate_templates.py is the source of truth. If it is edited
    without regenerating, the fix never reaches the shipped template."""
    generator = Path(__file__).resolve().parents[3] / "scripts" / "generate_templates.py"
    if not generator.exists():
        pytest.skip("generator not present in this checkout")
    source = generator.read_text(encoding="utf-8")
    assert "(input or {}).get('body')" not in source, (
        "the generator still emits the pattern that raises on a list input"
    )
