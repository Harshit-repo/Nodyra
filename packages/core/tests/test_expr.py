from nodyra.expr import (
    _CodeValidator,
    build_context,
    contains_expression,
    evaluate,
    from_ai_binding,
    set_code_validation_blocked_hook,
)


def test_plain_string_is_unchanged() -> None:
    assert evaluate("hello", build_context()) == "hello"


def test_expression_validator_blocks_sandbox_escape() -> None:
    """EXPR-1: dunder/attribute-walk escapes must be rejected, not executed.

    Regression for the bug where overriding NodeVisitor.visit() disabled
    visit_Name/visit_Attribute dispatch, leaving _BLOCKED_NAMES unenforced.
    """
    import ast

    from nodyra.expr import _ExprValidator

    escapes = [
        "x.__class__",
        "().__class__.__bases__[0].__subclasses__()",
        "__import__",
        "getattr",
        "setattr",
        "globals",
        "[c for c in ().__class__.__bases__[0].__subclasses__()]",
    ]
    for expr in escapes:
        tree = ast.parse(expr, mode="eval")
        try:
            _ExprValidator().visit(tree)
        except ValueError:
            continue
        raise AssertionError(f"escape expression was not blocked: {expr!r}")

    # End-to-end: a templated escape must not execute; evaluate() returns the
    # safe error sentinel string instead of a live Python object.
    out = evaluate("{{ ().__class__ }}", build_context())
    assert isinstance(out, str) and "expr error" in out


def test_expression_validator_blocks_str_format_escape() -> None:
    """EXPR-2: str.format field specs walk attributes the AST never sees.

    ``"{0.__class__.__base__}".format(x)`` reaches ``__class__`` from inside a
    string literal, so visit_Attribute never fires on it and every entry in
    _BLOCKED_NAMES is bypassed. Format specs cannot call, so this is a
    read-only walk rather than RCE — but it still exfiltrates module globals
    off any callable in scope (``"{0.__globals__[SECRET]}".format(fn)``), so
    the ``format``/``format_map`` methods themselves must be unreachable.
    """
    import ast

    from nodyra.expr import _ExprValidator

    escapes = [
        '"{0.__class__}".format(x)',
        '"{0.__class__.__base__.__subclasses__}".format(x)',
        '"{0.__globals__[SECRET]}".format(fn)',
        "x.format",
        "x.format_map",
        "d.format_map(m)",
    ]
    for expr in escapes:
        tree = ast.parse(expr, mode="eval")
        try:
            _ExprValidator().visit(tree)
        except ValueError:
            continue
        raise AssertionError(f"format escape was not blocked: {expr!r}")

    # End-to-end: even given a callable whose module globals hold a secret, the
    # templated walk must not resolve it — evaluate returns the error sentinel.
    def _holder() -> None:  # pragma: no cover - only its __globals__ matters
        pass

    _holder.__globals__["SECRET_TOKEN"] = "sk-live-should-not-leak"
    ctx = build_context(first_input={"s": "abc"})
    ctx["fn"] = _holder
    out = evaluate('{{ "{0.__globals__[SECRET_TOKEN]}".format(fn) }}', ctx)
    assert isinstance(out, str) and "expr error" in out
    assert "sk-live" not in out

    # Ordinary string methods are unaffected.
    assert evaluate("{{ $json.s.upper() }}", ctx) == "ABC"


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


def test_code_validation_metric_hook_records_bounded_reason_and_target() -> None:
    import ast

    seen: list[tuple[str, str]] = []
    previous = set_code_validation_blocked_hook(
        lambda reason, target: seen.append((reason, target))
    )
    try:
        try:
            _CodeValidator().visit(ast.parse("import os\noutput = 1", mode="exec"))
        except ValueError:
            pass
        else:
            raise AssertionError("blocked import was accepted")

        assert seen == [("import", "os")]
    finally:
        set_code_validation_blocked_hook(previous)


def test_code_validation_metric_hook_failure_does_not_bypass_rejection() -> None:
    import ast

    def broken_hook(_reason: str, _target: str) -> None:
        raise RuntimeError("metrics unavailable")

    previous = set_code_validation_blocked_hook(broken_hook)
    try:
        try:
            _CodeValidator().visit(ast.parse("open('secret.txt')", mode="exec"))
        except ValueError as exc:
            assert "blocked name" in str(exc)
        else:
            raise AssertionError("blocked name was accepted")
    finally:
        set_code_validation_blocked_hook(previous)


def test_code_validator_blocks_dynamic_introspection_builtins() -> None:
    import ast

    for source in (
        "output = getattr(object, '__subclasses__')",
        "setattr(target, 'value', 1)",
        "output = globals()",
    ):
        try:
            _CodeValidator().visit(ast.parse(source, mode="exec"))
        except ValueError:
            continue
        raise AssertionError(f"dynamic introspection was not blocked: {source!r}")


def test_alias_inside_string_literal_is_not_rewritten() -> None:
    """H3: the `$json`→`_json` rewrite must not touch string literals.

    Regression for the naive str.replace that corrupted any literal containing
    an alias substring (e.g. ``"$json"`` became ``"_json"``).
    """
    ctx = build_context(first_input={"x": 1})
    assert evaluate('{{ "$json stays literal" }}', ctx) == "$json stays literal"
    assert evaluate('{{ "price is $now" }}', ctx) == "price is $now"
    # And a real alias next to a literal alias still resolves correctly.
    assert evaluate('{{ "$json=" + str($json.x) }}', ctx) == "$json=1"


def test_alias_still_resolves_inside_fstring() -> None:
    ctx = build_context(first_input={"name": "bob"})
    assert evaluate('{{ f"hi {$json.name}" }}', ctx) == "hi bob"


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
