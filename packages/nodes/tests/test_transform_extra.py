"""Tests for the pure-Python Batch 1 transform nodes.

These don't go through the engine — they call the underlying functions
directly so we can assert behaviour without spinning up a workflow.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.sdk import registry
from noodle_nodes.datasets import csv_parse, csv_write, dataset_to_records
from noodle_nodes.transform_extra import (
    decrypt_fernet,
    encrypt_fernet,
    gzip_compress,
    gzip_decompress,
    html_extract,
    jmespath_query,
    json_schema_validate,
    markdown_to_html,
    template_render,
    xml_parse,
    yaml_dump,
    yaml_parse,
)

# --- Registration -----------------------------------------------------------


def test_batch1_nodes_are_registered() -> None:
    ids = {m.id for m in registry.manifests()}
    expected = {
        "jmespath_query",
        "template_render",
        "csv_parse",
        "csv_write",
        "xml_parse",
        "yaml_parse",
        "yaml_dump",
        "markdown_to_html",
        "html_extract",
        "json_schema_validate",
        "gzip_compress",
        "gzip_decompress",
        "encrypt_fernet",
        "decrypt_fernet",
    }
    assert expected <= ids


# --- JMESPath ---------------------------------------------------------------


def test_jmespath_extracts_nested_values() -> None:
    data: Any = {
        "items": [
            {"id": 1, "status": "open"},
            {"id": 2, "status": "closed"},
            {"id": 3, "status": "open"},
        ]
    }
    assert jmespath_query(input=data, expression="items[?status=='open'].id") == [1, 3]


def test_jmespath_blank_expression_returns_input() -> None:
    assert jmespath_query(input={"a": 1}, expression="") == {"a": 1}


# --- Jinja ------------------------------------------------------------------


def test_template_render_with_dict_input() -> None:
    out = template_render(
        input={"name": "Alice", "items": [1, 2, 3]},
        template="Hello {{ name }}, you have {{ items|length }} items.",
    )
    assert out == "Hello Alice, you have 3 items."


def test_template_render_autoescape() -> None:
    out = template_render(
        input={"text": "<script>"},
        template="{{ text }}",
        autoescape=True,
    )
    assert out == "&lt;script&gt;"


# --- CSV --------------------------------------------------------------------


def _with_store(tmp_path):
    from noodle.artifacts import LocalArtifactStore
    from noodle.context import artifact_store, current_node_id

    store = LocalArtifactStore(tmp_path, run_id="test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("test-node")
    return store, a, n


def test_csv_parse_with_header_returns_dataset_ref(tmp_path) -> None:
    from noodle.context import artifact_store, current_node_id
    from noodle.datasets import is_dataset_ref

    _, a, n = _with_store(tmp_path)
    try:
        ref = csv_parse(text="a,b\n1,2\n3,4\n", has_header=True)
        assert is_dataset_ref(ref)
        assert ref["row_count"] == 2
        assert [c["name"] for c in ref["schema"]] == ["a", "b"]
        rows = dataset_to_records(input=ref, max_rows=10)
        assert rows == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    finally:
        current_node_id.reset(n)
        artifact_store.reset(a)


def test_csv_write_dataset_returns_artifact_ref(tmp_path) -> None:
    from noodle.artifacts import is_artifact_ref
    from noodle.context import artifact_store, current_node_id

    _, a, n = _with_store(tmp_path)
    try:
        ds = csv_parse(text="a,b\n1,2\n3,4\n", has_header=True)
        out = csv_write(input=ds)
        assert is_artifact_ref(out)
        assert out["content_type"].startswith("text/csv")
    finally:
        current_node_id.reset(n)
        artifact_store.reset(a)


# --- XML --------------------------------------------------------------------


def test_xml_parse_emits_dict_shape() -> None:
    parsed = xml_parse(text="<root><item>1</item><item>2</item></root>")
    # Elements with text but no attrs/children land under '#text' so the
    # shape stays uniform when attrs/children appear elsewhere.
    assert parsed == {"root": {"item": [{"#text": "1"}, {"#text": "2"}]}}


def test_xml_parse_handles_attributes() -> None:
    parsed = xml_parse(text='<root id="42"><item>hi</item></root>')
    # Single-child tags unwrap (helper: ``values[0] if len==1 else values``).
    assert parsed == {
        "root": {"@attrib": {"id": "42"}, "item": {"#text": "hi"}}
    }


# --- YAML -------------------------------------------------------------------


def test_yaml_parse_and_dump_round_trip() -> None:
    data = {"name": "test", "values": [1, 2, 3]}
    dumped = yaml_dump(input=data)
    assert yaml_parse(text=dumped) == data


# --- Markdown ---------------------------------------------------------------


def test_markdown_to_html_renders_fenced_code() -> None:
    html = markdown_to_html(text="# Hi\n\n```python\nprint('hi')\n```")
    assert "<h1>" in html
    assert "<code" in html


# --- HTML extract -----------------------------------------------------------


def test_html_extract_returns_text_for_selector() -> None:
    html = "<ul><li>one</li><li>two</li><li>three</li></ul>"
    assert html_extract(html=html, selector="li") == ["one", "two", "three"]


def test_html_extract_returns_attribute_when_requested() -> None:
    html = '<a href="https://a.com">A</a><a href="https://b.com">B</a>'
    assert html_extract(html=html, selector="a", attribute="href") == [
        "https://a.com",
        "https://b.com",
    ]


# --- JSON Schema ------------------------------------------------------------


def test_json_schema_validate_passes_for_valid_data() -> None:
    schema = json.dumps(
        {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    )
    result = json_schema_validate(input={"name": "ok"}, schema=schema)
    assert result == {"valid": True, "errors": []}


def test_json_schema_validate_lists_errors() -> None:
    schema = json.dumps(
        {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    )
    result = json_schema_validate(input={}, schema=schema)
    assert result["valid"] is False
    assert any("name" in err for err in result["errors"])


# --- GZIP -------------------------------------------------------------------


def test_gzip_round_trip() -> None:
    payload = "hello world " * 100
    blob = gzip_compress(text=payload)
    assert gzip_decompress(data_b64=blob) == payload


# --- Fernet -----------------------------------------------------------------


def test_fernet_round_trip() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("ascii")
    token = encrypt_fernet(text="top secret", key=key)
    assert decrypt_fernet(token=token, key=key) == "top secret"


def test_decrypt_fernet_invalid_token_raises_clear_error() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("ascii")
    with pytest.raises(ValueError, match="invalid token or key"):
        decrypt_fernet(token="not-a-token", key=key)


def test_encrypt_fernet_requires_key() -> None:
    with pytest.raises(ValueError, match="key is required"):
        encrypt_fernet(text="x", key="")
