from noodle.expr import (
    build_context,
    contains_expression,
    evaluate,
    from_ai_binding,
)


def test_plain_string_is_unchanged() -> None:
    assert evaluate("hello", build_context()) == "hello"


def test_whole_value_expression_returns_raw_type() -> None:
    ctx = build_context(first_input={"count": 7})
    assert evaluate("{{ $json.count }}", ctx) == 7
    assert evaluate("{{ $json.count * 3 }}", ctx) == 21


def test_interpolation_stringifies() -> None:
    ctx = build_context(first_input={"name": "world"})
    assert evaluate("Hello {{ $json.name }}!", ctx) == "Hello world!"


def test_missing_keys_return_none() -> None:
    ctx = build_context(first_input={"a": 1})
    assert evaluate("{{ $json.missing }}", ctx) is None


def test_node_outputs_access() -> None:
    ctx = build_context(
        node_outputs={"fetch": {"main": {"status": "ok", "count": 3}}}
    )
    assert evaluate('{{ $node["fetch"]["main"]["status"] }}', ctx) == "ok"
    assert evaluate('{{ $node["fetch"].main.count + 1 }}', ctx) == 4


def test_now_is_a_datetime() -> None:
    ctx = build_context()
    year = evaluate("{{ $now.year }}", ctx)
    assert isinstance(year, int)
    assert year >= 2026


def test_dicts_and_lists_walk_recursively() -> None:
    ctx = build_context(first_input={"x": 10})
    out = evaluate(
        {"a": "{{ $json.x }}", "b": [1, "{{ $json.x * 2 }}"]},
        ctx,
    )
    assert out == {"a": 10, "b": [1, 20]}


def test_eval_errors_become_friendly_strings() -> None:
    ctx = build_context()
    result = evaluate("{{ 1/0 }}", ctx)
    assert isinstance(result, str) and "expr error" in result


def test_safe_builtins_block_dangerous_calls() -> None:
    ctx = build_context()
    # `open` is not exposed; the eval falls back to the friendly error.
    result = evaluate('{{ open("/etc/passwd") }}', ctx)
    assert isinstance(result, str) and "expr error" in result


def test_contains_expression_detects_braces() -> None:
    assert contains_expression("hello {{ $json.x }}") is True
    assert contains_expression({"a": "x", "b": "{{ y }}"}) is True
    assert contains_expression("plain") is False


def test_from_ai_schema_collection_records_args() -> None:
    collector: dict = {}
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(collector=collector)
    # In schema mode the call returns the default and records the arg.
    result = evaluate("{{ $fromAI('city', 'City name', 'string') }}", ctx)
    assert result is None
    assert collector == {
        "city": {"name": "city", "description": "City name", "type": "string"}
    }


def test_from_ai_invoke_mode_resolves_from_args() -> None:
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(ai_args={"city": "Berlin"})
    assert evaluate("{{ $fromAI('city') }}", ctx) == "Berlin"


def test_from_ai_invoke_mode_uses_default_when_missing() -> None:
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(ai_args={})
    assert evaluate("{{ $fromAI('city', 'desc', 'string', 'NONE') }}", ctx) == "NONE"


def test_from_ai_requires_a_name() -> None:
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(collector={})
    # A blank name surfaces as a friendly expr error string, not a crash.
    assert "expr error" in evaluate("{{ $fromAI('') }}", ctx)
