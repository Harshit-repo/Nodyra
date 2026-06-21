"""Tests for typed multi-condition evaluation (_matches_typed, _eval_conditions)."""
import pytest
from noodle_nodes.builtin import _matches_typed, _eval_conditions


# ---------------------------------------------------------------------------
# _matches_typed — string operators
# ---------------------------------------------------------------------------

def test_string_equals():
    assert _matches_typed("open", "string", "equals", "open") is True
    assert _matches_typed("open", "string", "equals", "closed") is False

def test_string_not_equals():
    assert _matches_typed("open", "string", "not equals", "closed") is True

def test_string_contains():
    assert _matches_typed("hello world", "string", "contains", "world") is True
    assert _matches_typed("hello", "string", "contains", "xyz") is False

def test_string_does_not_contain():
    assert _matches_typed("hello", "string", "does not contain", "xyz") is True

def test_string_starts_with():
    assert _matches_typed("foobar", "string", "starts with", "foo") is True
    assert _matches_typed("foobar", "string", "starts with", "bar") is False

def test_string_ends_with():
    assert _matches_typed("foobar", "string", "ends with", "bar") is True

def test_string_is_empty():
    assert _matches_typed("", "string", "is empty") is True
    assert _matches_typed("x", "string", "is empty") is False

def test_string_is_not_empty():
    assert _matches_typed("x", "string", "is not empty") is True
    assert _matches_typed("", "string", "is not empty") is False

def test_string_matches_regex():
    assert _matches_typed("abc123", "string", "matches regex", r"\d+") is True
    assert _matches_typed("abcdef", "string", "matches regex", r"\d+") is False

def test_string_matches_regex_invalid_pattern_returns_false():
    assert _matches_typed("abc", "string", "matches regex", "[invalid") is False


# ---------------------------------------------------------------------------
# _matches_typed — number operators
# ---------------------------------------------------------------------------

def test_number_equals():
    assert _matches_typed(42, "number", "equals", "42") is True
    assert _matches_typed(42, "number", "equals", "43") is False

def test_number_not_equals():
    assert _matches_typed(1, "number", "not equals", "2") is True

def test_number_greater_than():
    assert _matches_typed(10, "number", "greater than", "5") is True
    assert _matches_typed(3, "number", "greater than", "5") is False

def test_number_greater_than_or_equal():
    assert _matches_typed(5, "number", "greater than or equal", "5") is True
    assert _matches_typed(4, "number", "greater than or equal", "5") is False

def test_number_less_than():
    assert _matches_typed(3, "number", "less than", "5") is True

def test_number_less_than_or_equal():
    assert _matches_typed(5, "number", "less than or equal", "5") is True

def test_number_non_numeric_actual_returns_false():
    assert _matches_typed("hello", "number", "equals", "5") is False

def test_number_non_numeric_value_returns_false():
    assert _matches_typed(5, "number", "equals", "not-a-number") is False


# ---------------------------------------------------------------------------
# _matches_typed — boolean operators
# ---------------------------------------------------------------------------

def test_boolean_is_true():
    assert _matches_typed(True, "boolean", "is true") is True
    assert _matches_typed(False, "boolean", "is true") is False

def test_boolean_is_false():
    assert _matches_typed(False, "boolean", "is false") is True
    assert _matches_typed(True, "boolean", "is false") is False

def test_boolean_string_false():
    assert _matches_typed("false", "boolean", "is true") is False

def test_boolean_string_true():
    assert _matches_typed("true", "boolean", "is true") is True


# ---------------------------------------------------------------------------
# _matches_typed — array operators
# ---------------------------------------------------------------------------

def test_array_is_empty():
    assert _matches_typed([], "array", "is empty") is True
    assert _matches_typed([1], "array", "is empty") is False

def test_array_is_not_empty():
    assert _matches_typed([1, 2], "array", "is not empty") is True

def test_array_contains():
    assert _matches_typed(["a", "b"], "array", "contains", "a") is True
    assert _matches_typed(["a", "b"], "array", "contains", "c") is False

def test_array_does_not_contain():
    assert _matches_typed(["a"], "array", "does not contain", "b") is True

def test_array_length_equals():
    assert _matches_typed([1, 2, 3], "array", "length equals", "3") is True
    assert _matches_typed([1, 2], "array", "length equals", "3") is False

def test_array_length_not_equals():
    assert _matches_typed([1, 2], "array", "length not equals", "3") is True

def test_array_length_greater_than():
    assert _matches_typed([1, 2, 3], "array", "length greater than", "2") is True

def test_array_length_less_than():
    assert _matches_typed([1], "array", "length less than", "5") is True

def test_array_non_list_treated_as_empty():
    assert _matches_typed("not-a-list", "array", "is empty") is True


# ---------------------------------------------------------------------------
# _matches_typed — object operators
# ---------------------------------------------------------------------------

def test_object_has_key():
    assert _matches_typed({"name": "Alice"}, "object", "has key", "name") is True
    assert _matches_typed({"name": "Alice"}, "object", "has key", "age") is False

def test_object_does_not_have_key():
    assert _matches_typed({"a": 1}, "object", "does not have key", "b") is True

def test_object_is_empty():
    assert _matches_typed({}, "object", "is empty") is True
    assert _matches_typed({"a": 1}, "object", "is empty") is False

def test_object_is_not_empty():
    assert _matches_typed({"k": "v"}, "object", "is not empty") is True

def test_object_non_dict_treated_as_empty():
    assert _matches_typed("not-a-dict", "object", "is empty") is True


# ---------------------------------------------------------------------------
# _matches_typed — date operators
# ---------------------------------------------------------------------------

def test_date_before():
    assert _matches_typed("2024-01-01", "date", "before", "2025-01-01") is True
    assert _matches_typed("2026-01-01", "date", "before", "2025-01-01") is False

def test_date_after():
    assert _matches_typed("2026-01-01", "date", "after", "2025-01-01") is True

def test_date_equals_same_day():
    assert _matches_typed("2025-06-15T10:00:00", "date", "equals", "2025-06-15T18:00:00") is True
    assert _matches_typed("2025-06-15", "date", "equals", "2025-06-16") is False

def test_date_invalid_format_returns_false():
    assert _matches_typed("not-a-date", "date", "before", "2025-01-01") is False


# ---------------------------------------------------------------------------
# _matches_typed — any operators
# ---------------------------------------------------------------------------

def test_any_exists():
    assert _matches_typed("value", "any", "exists") is True
    assert _matches_typed(None, "any", "exists") is False

def test_any_does_not_exist():
    assert _matches_typed(None, "any", "does not exist") is True
    assert _matches_typed(0, "any", "does not exist") is False

def test_any_is_empty():
    assert _matches_typed(None, "any", "is empty") is True
    assert _matches_typed("", "any", "is empty") is True
    assert _matches_typed([], "any", "is empty") is True
    assert _matches_typed("x", "any", "is empty") is False

def test_any_is_not_empty():
    assert _matches_typed("x", "any", "is not empty") is True
    assert _matches_typed(None, "any", "is not empty") is False


# ---------------------------------------------------------------------------
# _eval_conditions
# ---------------------------------------------------------------------------

def test_eval_conditions_empty_list_passes():
    assert _eval_conditions({"x": 1}, {"logic": "AND", "conditions": []}) is True

def test_eval_conditions_none_returns_false():
    assert _eval_conditions({"x": 1}, None) is False

def test_eval_conditions_and_all_pass():
    cond = {
        "logic": "AND",
        "conditions": [
            {"field": "status", "type": "string", "operator": "equals", "value": "open"},
            {"field": "count", "type": "number", "operator": "greater than", "value": "0"},
        ],
    }
    assert _eval_conditions({"status": "open", "count": 5}, cond) is True

def test_eval_conditions_and_one_fails():
    cond = {
        "logic": "AND",
        "conditions": [
            {"field": "status", "type": "string", "operator": "equals", "value": "open"},
            {"field": "count", "type": "number", "operator": "greater than", "value": "10"},
        ],
    }
    assert _eval_conditions({"status": "open", "count": 5}, cond) is False

def test_eval_conditions_or_one_passes():
    cond = {
        "logic": "OR",
        "conditions": [
            {"field": "status", "type": "string", "operator": "equals", "value": "closed"},
            {"field": "count", "type": "number", "operator": "greater than", "value": "0"},
        ],
    }
    assert _eval_conditions({"status": "open", "count": 5}, cond) is True

def test_eval_conditions_or_all_fail():
    cond = {
        "logic": "OR",
        "conditions": [
            {"field": "status", "type": "string", "operator": "equals", "value": "closed"},
            {"field": "count", "type": "number", "operator": "greater than", "value": "100"},
        ],
    }
    assert _eval_conditions({"status": "open", "count": 5}, cond) is False

def test_eval_conditions_blank_field_tests_whole_input():
    cond = {
        "logic": "AND",
        "conditions": [
            {"field": "", "type": "any", "operator": "exists"},
        ],
    }
    assert _eval_conditions("hello", cond) is True
    assert _eval_conditions(None, cond) is False

def test_eval_conditions_skips_non_dict_entries():
    cond = {
        "logic": "AND",
        "conditions": ["not-a-dict", None, 42],
    }
    # All entries skipped → vacuously true
    assert _eval_conditions({"x": 1}, cond) is True
