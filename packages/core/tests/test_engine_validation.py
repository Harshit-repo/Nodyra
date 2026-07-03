"""Tests for engine validation: schema sanitization, NodeValidationError,
ValidationWarning, and graph schema inference."""

import pytest

from nodyra.engine.node_exec import (
    _sanitize_schema,
    _validate_node_input_schema,
    _validate_node_output_schema,
)
from nodyra.engine.types import GraphError, NodeValidationError, ValidationWarning
from nodyra.engine.validation import (
    _infer_schema_port_kinds,
    validate_graph,
)
from nodyra.models import Edge, GraphNode, WorkflowGraph

# ---------------------------------------------------------------------------
# Schema sanitization
# ---------------------------------------------------------------------------


class TestSanitizeSchema:
    def test_blocks_dollar_ref(self):
        schema = {"$ref": "#/defs/External", "type": "object"}
        cleaned = _sanitize_schema(schema)
        assert "$ref" not in cleaned
        assert cleaned == {"type": "object"}

    def test_blocks_dollar_schema(self):
        schema = {
            "$schema": "http://malicious.com/schema",
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        }
        cleaned = _sanitize_schema(schema)
        assert "$schema" not in cleaned

    def test_blocks_dollar_id(self):
        schema = {
            "$id": "http://malicious.com/schema",
            "type": "object",
        }
        cleaned = _sanitize_schema(schema)
        assert "$id" not in cleaned

    def test_recursive_nested_blocks(self):
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "$ref": "#/defs/User",
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "$ref": "#/defs/Name"},
                    },
                }
            },
        }
        cleaned = _sanitize_schema(schema)
        # Check no $ref anywhere in the result
        cleaned_str = str(cleaned)
        assert "$ref" not in cleaned_str

    def test_cleans_in_array_items(self):
        schema = {
            "type": "array",
            "items": [{"$ref": "#/defs/Item1"}, {"type": "string"}],
        }
        cleaned = _sanitize_schema(schema)
        # Array items are cleaned recursively (blocked keys removed, item preserved)
        assert len(cleaned["items"]) == 2
        # First item had only $ref — now an empty dict
        assert cleaned["items"][0] == {}
        assert cleaned["items"][1]["type"] == "string"
        assert "$ref" not in str(cleaned)

    def test_preserves_valid_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "name": {"type": "string"},
            },
            "required": ["name"],
        }
        cleaned = _sanitize_schema(schema)
        assert cleaned == schema


# ---------------------------------------------------------------------------
# NodeValidationError
# ---------------------------------------------------------------------------


class TestNodeValidationError:
    def test_has_node_id(self):
        err = NodeValidationError("bad data", node_id="node_1")
        assert err.node_id == "node_1"
        assert "bad data" in str(err)

    def test_is_node_error(self):
        from nodyra.engine.types import NodeError

        assert issubclass(NodeValidationError, NodeError)
        assert issubclass(NodeError, Exception)


# ---------------------------------------------------------------------------
# ValidationWarning
# ---------------------------------------------------------------------------


class TestValidationWarning:
    def test_default_severity(self):
        w = ValidationWarning(node_id="n1", message="test warning")
        assert w.severity == "warning"

    def test_explicit_severity(self):
        w = ValidationWarning(node_id="n1", message="test", severity="warning")
        assert w.severity == "warning"


# ---------------------------------------------------------------------------
# Input / Output validation via _validate_node_input_schema / output
# ---------------------------------------------------------------------------


class TestNodeInputSchemaValidation:
    def test_valid_input_passes(self):
        params = {
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "number"},
                    "user_id": {"type": "integer"},
                },
                "required": ["user_id"],
            }
        }
        node_input = {"score": 95.5, "user_id": 42}
        # Should not raise
        _validate_node_input_schema(params, node_input, "node_1")

    def test_rejects_wrong_type(self):
        params = {
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "number"},
                    "user_id": {"type": "integer"},
                },
                "required": ["user_id"],
            }
        }
        node_input = {"score": "not-a-number", "user_id": 42}
        with pytest.raises(NodeValidationError) as exc_info:
            _validate_node_input_schema(params, node_input, "node_1")
        assert "Input validation failed" in str(exc_info.value)
        assert exc_info.value.node_id == "node_1"

    def test_rejects_missing_required(self):
        params = {
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "number"},
                    "user_id": {"type": "integer"},
                },
                "required": ["user_id"],
            }
        }
        node_input = {"score": 95.5}
        with pytest.raises(NodeValidationError) as exc_info:
            _validate_node_input_schema(params, node_input, "node_1")
        assert "Input validation failed" in str(exc_info.value)

    def test_no_input_schema_skips_validation(self):
        params = {}
        node_input = {"anything": "goes"}
        # Should not raise
        _validate_node_input_schema(params, node_input, "node_1")

    def test_non_dict_input_skips_validation(self):
        params = {
            "input_schema": {"type": "object", "properties": {}}
        }
        node_input = "string input"
        # Should not raise when input is not a dict
        _validate_node_input_schema(params, node_input, "node_1")


class TestNodeOutputSchemaValidation:
    def test_valid_output_passes(self):
        params = {
            "output_schema": {
                "type": "object",
                "properties": {
                    "result": {"type": "number"},
                    "category": {"type": "string"},
                },
            }
        }
        outputs = {"result": 42.0, "category": "low"}
        _validate_node_output_schema(params, outputs, "node_1")

    def test_rejects_wrong_type(self):
        params = {
            "output_schema": {
                "type": "object",
                "properties": {
                    "result": {"type": "number"},
                },
            }
        }
        outputs = {"result": "not-a-number"}
        with pytest.raises(NodeValidationError) as exc_info:
            _validate_node_output_schema(params, outputs, "node_1")
        assert "Output validation failed" in str(exc_info.value)
        assert exc_info.value.node_id == "node_1"

    def test_no_output_schema_skips_validation(self):
        params = {}
        outputs = {"anything": "goes"}
        _validate_node_output_schema(params, outputs, "node_1")

    def test_non_dict_output_skips_validation(self):
        params = {
            "output_schema": {"type": "object", "properties": {}}
        }
        outputs = 42
        _validate_node_output_schema(params, outputs, "node_1")


# ---------------------------------------------------------------------------
# Schema injection defense: sanitize before validate
# ---------------------------------------------------------------------------


class TestSchemaInjectionDefense:
    def test_dollar_ref_removed_before_validation(self):
        """A schema with $ref should not crash — the $ref is stripped first."""
        params = {
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "$ref": "#/defs/Evil",
                "$id": "http://evil.com/pwn",
            }
        }
        node_input = {"x": "hello"}
        _validate_node_input_schema(params, node_input, "n1")

    def test_dollar_ref_in_nested_property(self):
        params = {
            "input_schema": {
                "type": "object",
                "properties": {
                    "data": {"$ref": "#/defs/External", "type": "object"},
                },
            }
        }
        node_input = {"data": {"key": "val"}}
        _validate_node_input_schema(params, node_input, "n1")


# ---------------------------------------------------------------------------
# Schema port kind inference
# ---------------------------------------------------------------------------


class TestInferSchemaPortKinds:
    def test_infers_from_output_schema(self):
        params = {
            "output_schema": {
                "type": "object",
                "properties": {
                    "result": {"type": "number"},
                    "label": {"type": "string"},
                    "count": {"type": "integer"},
                    "active": {"type": "boolean"},
                },
            }
        }
        kinds = _infer_schema_port_kinds(params)
        assert kinds == {
            "result": "number",
            "label": "string",
            "count": "integer",
            "active": "boolean",
        }

    def test_enum_property(self):
        params = {
            "output_schema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["a", "b", "c"]},
                },
            }
        }
        kinds = _infer_schema_port_kinds(params)
        assert kinds["category"] == "enum"

    def test_no_output_schema_returns_empty(self):
        assert _infer_schema_port_kinds({}) == {}
        assert _infer_schema_port_kinds({"code": "x = 1"}) == {}

    def test_no_properties_returns_empty(self):
        params = {"output_schema": {"type": "object"}}
        assert _infer_schema_port_kinds(params) == {}


# ---------------------------------------------------------------------------
# Graph-level validation
# ---------------------------------------------------------------------------


class TestValidateGraphWarnings:
    def test_empty_graph_raises(self):
        graph = WorkflowGraph(nodes=[], edges=[])
        with pytest.raises(GraphError):
            validate_graph(graph)

    def test_validate_graph_with_code_node(self):
        """validate_graph should not raise for a valid graph with a code node
        that has an output_schema."""
        graph = WorkflowGraph(
            nodes=[
                GraphNode(id="code_1", type="code", params={
                    "code": "output = {'result': 42}",
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "result": {"type": "number"},
                        },
                    },
                }),
                GraphNode(id="node_2", type="log"),
            ],
            edges=[
                Edge(source="code_1", source_output="main", target="node_2", target_input="input"),
            ],
        )
        # Without a registry, schema compatibility is skipped — no warnings.
        warnings = validate_graph(graph)
        assert isinstance(warnings, list)

    def test_validate_graph_returns_warning_list(self):
        graph = WorkflowGraph(
            nodes=[GraphNode(id="n1", type="code")],
            edges=[],
        )
        warnings = validate_graph(graph)
        assert isinstance(warnings, list)
