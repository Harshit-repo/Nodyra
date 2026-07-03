"""Integration tests for typed code node I/O.

Tests that code nodes with ``input_schema`` and ``output_schema`` in their
params correctly validate inputs and outputs at execution time, and that
nodes without schemas continue to work unchanged.
"""

import pytest

from nodyra.engine.node_exec import (
    _sanitize_schema,
    _validate_node_input_schema,
    _validate_node_output_schema,
)
from nodyra.engine.types import NodeValidationError

# ---------------------------------------------------------------------------
# Input schema validation
# ---------------------------------------------------------------------------


class TestCodeNodeInputSchema:
    """Code node with ``input_schema`` validates inputs before execution."""

    def test_valid_input_passes(self):
        """A valid input matching the schema should not raise."""
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
        _validate_node_input_schema(params, node_input, "node_1")

    def test_rejects_wrong_type(self):
        """An input with wrong types should raise NodeValidationError."""
        params = {
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "number"},
                },
            }
        }
        node_input = {"score": "not-a-number"}
        with pytest.raises(NodeValidationError) as exc_info:
            _validate_node_input_schema(params, node_input, "node_1")
        assert "Input validation failed" in str(exc_info.value)
        assert exc_info.value.node_id == "node_1"

    def test_rejects_missing_required_field(self):
        """Missing required field should raise NodeValidationError."""
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
        with pytest.raises(NodeValidationError):
            _validate_node_input_schema(params, node_input, "node_1")

    def test_rejects_extra_field_when_additional_properties_false(self):
        """Additional properties should be rejected when schema disallows them."""
        params = {
            "input_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "number"},
                },
                "additionalProperties": False,
            }
        }
        node_input = {"score": 95.5, "unexpected": "field"}
        with pytest.raises(NodeValidationError):
            _validate_node_input_schema(params, node_input, "node_1")


# ---------------------------------------------------------------------------
# Output schema validation
# ---------------------------------------------------------------------------


class TestCodeNodeOutputSchema:
    """Code node with ``output_schema`` validates outputs after execution."""

    def test_valid_output_passes(self):
        """A valid output matching the schema should not raise."""
        params = {
            "output_schema": {
                "type": "object",
                "properties": {
                    "result": {"type": "number"},
                    "category": {"type": "string", "enum": ["low", "medium", "high"]},
                },
            }
        }
        outputs = {"result": 42.0, "category": "low"}
        _validate_node_output_schema(params, outputs, "node_1")

    def test_rejects_wrong_type(self):
        """An output with wrong types should raise NodeValidationError."""
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

    def test_rejects_missing_property(self):
        """A missing property when property is required should raise."""
        params = {
            "output_schema": {
                "type": "object",
                "properties": {
                    "result": {"type": "number"},
                    "required_field": {"type": "string"},
                },
                "required": ["required_field"],
            }
        }
        outputs = {"result": 42.0}
        with pytest.raises(NodeValidationError):
            _validate_node_output_schema(params, outputs, "node_1")


# ---------------------------------------------------------------------------
# No-schema regression
# ---------------------------------------------------------------------------


class TestCodeNodeNoSchema:
    """Code nodes without schema work exactly as before."""

    def test_no_input_schema_allows_any(self):
        """No ``input_schema`` means any input passes without validation."""
        params = {"code": "output = input['x'] * 2"}
        node_input = {"x": 21, "y": [1, 2, 3], "z": {"nested": True}}
        # Should not raise
        _validate_node_input_schema(params, node_input, "node_1")

    def test_no_output_schema_allows_any(self):
        """No ``output_schema`` means any output passes without validation."""
        params = {"code": "output = {'result': 42}"}
        outputs = {"result": 42, "extra": [1, 2, 3], "nested": {"key": "val"}}
        # Should not raise
        _validate_node_output_schema(params, outputs, "node_1")

    def test_partial_schema_only_validates_present_one(self):
        """Having only ``input_schema`` should not affect output validation."""
        params = {
            "code": "output = input['x'] * 2",
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "number"}},
            },
        }
        node_input = {"x": 21}
        _validate_node_input_schema(params, node_input, "node_1")
        # Output has no schema — should pass
        outputs = {"result": "anything"}
        _validate_node_output_schema(params, outputs, "node_1")


# ---------------------------------------------------------------------------
# Schema injection defense
# ---------------------------------------------------------------------------


class TestSchemaInjection:
    """Schemas containing $ref, $schema, or $id must never reach jsonschema."""

    def test_dollar_ref_stripped_from_input_schema(self):
        params = {
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "$ref": "#/defs/External",
            }
        }
        node_input = {"x": "hello"}
        # Should not fetch external resources
        _validate_node_input_schema(params, node_input, "n1")

    def test_dollar_ref_stripped_from_output_schema(self):
        params = {
            "output_schema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "$ref": "#/defs/External",
            }
        }
        outputs = {"x": "hello"}
        _validate_node_output_schema(params, outputs, "n1")

    def test_mixed_valid_and_blocked_keys(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "$id": "http://evil.com/schema",
        }
        cleaned = _sanitize_schema(schema)
        assert "$id" not in cleaned
        # type must still be present
        assert cleaned["type"] == "object"
        assert "name" in cleaned["properties"]
