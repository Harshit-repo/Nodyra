import pytest

from nodyra.sdk import (
    NodeRegistry,
    discover_module_function_manifests,
    discover_module_nodes,
    node,
    register_module_functions,
)
from nodyra.sdk import (
    registry as global_registry,
)


def test_manifest_separates_inputs_and_config() -> None:
    reg = NodeRegistry()

    @node(name="Greet", category="Demo", registry=reg)
    def greet(input=None, prefix: str = "hi") -> str:
        return f"{prefix} {input}"

    manifest = reg.get("greet").manifest
    assert [p.name for p in manifest.inputs] == ["input"]
    assert [p.name for p in manifest.params] == ["prefix"]
    assert manifest.params[0].default == "hi"
    assert [o.name for o in manifest.outputs] == ["main"]


def test_trigger_has_no_input_port() -> None:
    reg = NodeRegistry()

    @node(name="Start", inputs=[], registry=reg)
    def start(data: dict | None = None) -> dict:
        return data or {}

    manifest = reg.get("start").manifest
    assert manifest.inputs == []
    assert [p.name for p in manifest.params] == ["data"]


def test_param_metadata_is_captured() -> None:
    reg = NodeRegistry()

    @node(
        name="Picker",
        params={
            "mode": {
                "choices": ["x", "y"],
                "description": "pick one",
                "placeholder": "x or y",
            }
        },
        registry=reg,
    )
    def picker(input=None, mode: str = "x") -> str:
        return mode

    spec = reg.get("picker").manifest.params[0]
    assert spec.type == "string"
    assert spec.choices == ["x", "y"]
    assert spec.description == "pick one"
    assert spec.placeholder == "x or y"


def test_param_group_is_captured() -> None:
    """A param's optional `group` flows onto the manifest spec.

    `group` marks an optional parameter that the inspector tucks behind an
    "Add option" chip; ungrouped params are core and always shown.
    """
    reg = NodeRegistry()

    @node(
        name="Grouped",
        params={
            "timeout_seconds": {"group": "Options"},
        },
        registry=reg,
    )
    def grouped(input=None, timeout_seconds: int = 30, core: str = "x") -> str:
        return core

    params = {p.name: p for p in reg.get("grouped").manifest.params}
    assert params["timeout_seconds"].group == "Options"
    assert params["core"].group is None


def test_param_groups_arg_assigns_groups() -> None:
    """`param_groups={"Options": [...]}` tags those params in one declaration.

    A convenience for nodes with many optional params; per-param `group` still
    wins if both are set.
    """
    reg = NodeRegistry()

    @node(
        name="Bulk",
        param_groups={"Options": ["temperature", "max_tokens"]},
        registry=reg,
    )
    def bulk(
        input=None, model: str = "x", temperature: float = 0.7, max_tokens: int = 256
    ) -> str:
        return model

    params = {p.name: p for p in reg.get("bulk").manifest.params}
    assert params["temperature"].group == "Options"
    assert params["max_tokens"].group == "Options"
    assert params["model"].group is None


def test_rich_manifest_metadata_is_captured() -> None:
    reg = NodeRegistry()

    @node(
        name="Chat Model",
        role="supplier",
        hidden=True,
        deprecated=True,
        replacement_id="ai_chat_model_openai",
        outputs=["model"],
        output_kinds={"model": "ai_language_model"},
        params={
            "model": {
                "display_name": "Model",
                "widget": "model_selector",
                "load_options": "openai_models",
                "required_scopes": ["models.read"],
                "documentation_url": "https://example.test/docs",
                "validation": {"min_length": 1},
            },
            "temperature": {
                "group": "Options",
                "display_when": {"provider": "openai"},
                "hide_when": {"mode": "deterministic"},
                "depends_on": ["provider"],
                "advanced": True,
            },
            "columns": {
                "resource_mapper": {"mode": "columns"},
                "fixed_collection": {"multiple": True},
            },
            "credentials": {
                "credential_type": "openai_api_key",
            },
        },
        registry=reg,
    )
    def chat_model(
        model: str = "gpt-4.1-mini",
        temperature: float = 0.2,
        columns: list | None = None,
        credentials: str = "",
    ) -> dict:
        return {}

    manifest = reg.get("chat_model").manifest
    assert manifest.role == "supplier"
    assert manifest.hidden is True
    assert manifest.deprecated is True
    assert manifest.replacement_id == "ai_chat_model_openai"
    assert manifest.outputs[0].data_kind == "ai_language_model"
    params = {p.name: p for p in manifest.params}
    assert params["model"].display_name == "Model"
    assert params["model"].widget == "model_selector"
    assert params["model"].load_options == "openai_models"
    assert params["model"].required_scopes == ["models.read"]
    assert params["model"].documentation_url == "https://example.test/docs"
    assert params["model"].validation == {"min_length": 1}
    assert params["temperature"].display_when == {"provider": "openai"}
    assert params["temperature"].hide_when == {"mode": "deterministic"}
    assert params["temperature"].depends_on == ["provider"]
    assert params["temperature"].advanced is True
    assert params["columns"].resource_mapper == {"mode": "columns"}
    assert params["columns"].fixed_collection == {"multiple": True}
    assert params["credentials"].credential_type == "openai_api_key"


def test_credential_param_metadata_is_captured() -> None:
    reg = NodeRegistry()

    @node(
        name="Needs Secret",
        params={
            "api_key": {
                "credential": {
                    "type": "openai",
                    "key": "api_key",
                    "label": "OpenAI API key",
                    "fields": ["api_key"],
                }
            }
        },
        registry=reg,
    )
    def needs_secret(input=None, api_key: str = "") -> str:  # noqa: ARG001
        return api_key

    spec = reg.get("needs_secret").manifest.params[0]
    assert spec.type == "credential"
    assert spec.credential is not None
    assert spec.credential.type == "openai"
    assert spec.credential.key == "api_key"
    assert spec.credential.fields == ["api_key"]


def test_declared_outputs() -> None:
    reg = NodeRegistry()

    @node(name="Brancher", outputs=["yes", "no"], registry=reg)
    def brancher(input=None) -> dict:
        return {"yes": input}

    assert [o.name for o in reg.get("brancher").manifest.outputs] == ["yes", "no"]


def test_multiple_input_ports() -> None:
    reg = NodeRegistry()

    @node(name="Combine", inputs=["a", "b"], registry=reg)
    def combine(a=None, b=None) -> list:
        return [a, b]

    manifest = reg.get("combine").manifest
    assert [p.name for p in manifest.inputs] == ["a", "b"]
    assert manifest.params == []


def test_duplicate_node_id_rejected() -> None:
    reg = NodeRegistry()

    @node(name="First", id="dup", registry=reg)
    def first(input=None) -> int:
        return 1

    with pytest.raises(ValueError, match="Duplicate node id"):

        @node(name="Second", id="dup", registry=reg)
        def second(input=None) -> int:
            return 2


def test_async_node_detected() -> None:
    reg = NodeRegistry()

    @node(name="Async", inputs=[], registry=reg)
    async def async_node() -> int:
        return 1

    assert reg.get("async_node").is_async is True


def test_ast_discovery_builds_typed_manifest_without_execution() -> None:
    source = """
raise RuntimeError("should not run")

def transform(rows: list[dict], limit: int = 10, active: bool = True,
              ratio: float = 1.5, meta: dict | None = None,
              anything: Any = None, dynamic=get_default()):
    \"\"\"Transform rows.\"\"\"
    return rows
"""

    manifests, skipped = discover_module_function_manifests("mod1", source)

    assert skipped == []
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.id == "user:mod1:transform"
    assert manifest.description == "Transform rows."
    # Single virtual ``input`` port; every parameter appears in the inspector.
    assert [p.name for p in manifest.inputs] == ["input"]
    params = {param.name: param for param in manifest.params}
    assert set(params) == {
        "rows", "limit", "active", "ratio", "meta", "anything", "dynamic",
    }
    assert params["rows"].required is True
    assert params["limit"].type == "integer"
    assert params["limit"].default == 10
    assert params["active"].type == "boolean"
    assert params["ratio"].type == "number"
    assert params["meta"].type == "object"
    assert params["anything"].type == "any"
    assert params["dynamic"].required is False
    assert params["dynamic"].default is None


def test_ast_discovery_skips_variadic_functions() -> None:
    source = """
class Thing:
    pass

def variadic(*args, **kwargs):
    return args

def ok(x: int, y: int = 1):
    return x + y
"""

    manifests, skipped = discover_module_function_manifests("mod2", source)

    assert [manifest.name for manifest in manifests] == ["ok"]
    assert skipped == [("variadic", "*args / **kwargs are not supported")]


def test_ast_discovery_includes_async_functions() -> None:
    source = """
async def fetch(url: str, retries: int = 3):
    return {"url": url}
"""

    manifests, skipped = discover_module_function_manifests("mod3", source)

    assert skipped == []
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.id == "user:mod3:fetch"
    # Single virtual ``input`` port; both params live in the inspector.
    assert [p.name for p in manifest.inputs] == ["input"]
    assert [(p.name, p.type, p.default, p.required) for p in manifest.params] == [
        ("url", "string", None, True),
        ("retries", "integer", 3, False),
    ]


# ---- @node decorators in user modules (explicit mode) ----

DECORATED_SOURCE = '''
from nodyra import node


def _clean(text: str) -> str:
    return text.strip()


@node(name="Ingest", id="ingest", category="ETL", outputs=["rows"])
def ingest(source: str = "db") -> dict:
    """Read rows."""
    return {"rows": _clean(source)}


@node(
    name="Transform",
    inputs=["rows"],
    params={"factor": {"description": "scale"}},
    wires={"rows": "ingest.rows"},
)
def transform(rows=None, factor: int = 2) -> dict:
    return {"rows": rows, "factor": factor}
'''


def test_explicit_mode_only_decorated_become_nodes() -> None:
    discovered, skipped = discover_module_nodes("mod", DECORATED_SOURCE)

    assert skipped == []
    ids = [d.manifest.id for d in discovered]
    # ``_clean`` is undecorated → a helper, not surfaced as a node.
    assert ids == ["user:mod:ingest", "user:mod:transform"]


def test_explicit_mode_reads_decorator_metadata() -> None:
    discovered, _ = discover_module_nodes("mod", DECORATED_SOURCE)
    by_id = {d.manifest.id: d for d in discovered}

    ingest = by_id["user:mod:ingest"]
    assert ingest.manifest.name == "Ingest"
    assert ingest.manifest.category == "ETL"
    assert [o.name for o in ingest.manifest.outputs] == ["rows"]
    assert ingest.manifest.description == "Read rows."

    transform = by_id["user:mod:transform"]
    assert [p.name for p in transform.manifest.inputs] == ["rows"]
    assert [p.name for p in transform.manifest.params] == ["factor"]
    assert transform.manifest.params[0].description == "scale"
    # ``rows`` is a wired input port, not a config param.
    assert transform.wires == {"rows": "ingest.rows"}


def test_ast_decorator_reads_role_param_groups_and_rich_metadata() -> None:
    source = '''
from nodyra import node

@node(
    name="Tool",
    id="tool",
    role="tool",
    hidden=True,
    deprecated=True,
    replacement_id="ai_http_tool",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    param_groups={"Options": ["timeout"]},
    params={
        "query": {
            "display_name": "Search query",
            "widget": "textarea",
            "required_scopes": ["search.read"],
            "validation": {"min_length": 3},
        },
        "timeout": {"advanced": True},
    },
)
def tool(query: str, timeout: int = 30) -> dict:
    return {}
'''
    discovered, skipped = discover_module_nodes("mod", source)

    assert skipped == []
    manifest = discovered[0].manifest
    assert manifest.role == "tool"
    assert manifest.hidden is True
    assert manifest.deprecated is True
    assert manifest.replacement_id == "ai_http_tool"
    assert manifest.outputs[0].data_kind == "ai_tool"
    params = {p.name: p for p in manifest.params}
    assert params["query"].display_name == "Search query"
    assert params["query"].widget == "textarea"
    assert params["query"].required_scopes == ["search.read"]
    assert params["query"].validation == {"min_length": 3}
    assert params["timeout"].group == "Options"
    assert params["timeout"].advanced is True


def test_include_undecorated_surfaces_helpers() -> None:
    discovered, _ = discover_module_nodes(
        "mod", DECORATED_SOURCE, include_undecorated=True
    )
    ids = [d.manifest.id for d in discovered]
    assert "user:mod:_clean" in ids


def test_decorator_in_user_module_does_not_touch_global_registry() -> None:
    before = {m.id for m in global_registry.manifests()}
    reg = NodeRegistry()
    register_module_functions("mod", DECORATED_SOURCE, reg)
    after = {m.id for m in global_registry.manifests()}
    # The bare ``@node`` in the user file must not register globally.
    assert before == after
    assert "user:mod:ingest" in reg
    assert "user:mod:transform" in reg


def test_runtime_registration_honours_decorator_and_helpers_callable() -> None:
    reg = NodeRegistry()
    registered, skipped = register_module_functions("mod", DECORATED_SOURCE, reg)

    assert skipped == []
    assert set(registered) == {"ingest", "transform"}
    # Decorated manifest metadata is preserved at runtime.
    ingest = reg.get("user:mod:ingest")
    assert [o.name for o in ingest.manifest.outputs] == ["rows"]
    # The helper is still callable from the node at runtime.
    assert ingest.func("  x  ") == {"rows": "x"}


def test_no_decorators_keeps_auto_behavior() -> None:
    source = "def a(x: int = 1):\n    return x\n\ndef b(y: int = 2):\n    return y\n"
    discovered, _ = discover_module_nodes("mod", source)
    assert [d.manifest.id for d in discovered] == ["user:mod:a", "user:mod:b"]
    assert all(not d.decorated for d in discovered)



def test_node_decorator_carries_requirements():
    reg = NodeRegistry()

    @node(name="Heavy", id="heavy_x", requirements=["duckdb>=0.9"], registry=reg)
    def heavy_x(input=None):
        return input

    manifest = reg.get("heavy_x").manifest
    assert manifest.requirements == ["duckdb>=0.9"]


def test_node_decorator_requirements_default_empty():
    reg = NodeRegistry()

    @node(name="Light", id="light_x", registry=reg)
    def light_x(input=None):
        return input

    assert reg.get("light_x").manifest.requirements == []


# ---------------------------------------------------------------------------
# Security: module code AST validation
# ---------------------------------------------------------------------------


def test_register_module_rejects_exec_call() -> None:
    """Module code using the blocked name 'exec' is rejected before running."""
    reg = NodeRegistry()
    source = "result = exec('import os')"
    with pytest.raises(ValueError, match="[Uu]nsafe"):
        register_module_functions("sec_mod1", source, reg)


def test_register_module_rejects_eval_call() -> None:
    """Module code using the blocked name 'eval' is rejected before running."""
    reg = NodeRegistry()
    # 'eval' here is inside a string literal being validated by the sandbox —
    # it is NOT executed by this test; the test asserts that the sandbox blocks it.
    source = "x = eval('1+1')"
    with pytest.raises(ValueError, match="[Uu]nsafe"):
        register_module_functions("sec_mod2", source, reg)


def test_register_module_rejects_class_definitions() -> None:
    """ClassDef is blocked in module code (same rule as code node)."""
    reg = NodeRegistry()
    source = "class Escape:\n    pass\n\ndef my_func(x):\n    return x\n"
    with pytest.raises(ValueError, match="[Uu]nsafe"):
        register_module_functions("sec_mod3", source, reg)


def test_register_module_rejects_dunder_builtins_access() -> None:
    """Accessing __builtins__ by name in module code is blocked."""
    reg = NodeRegistry()
    source = "b = __builtins__\n\ndef fn(x):\n    return x\n"
    with pytest.raises(ValueError, match="[Uu]nsafe"):
        register_module_functions("sec_mod4", source, reg)


def test_register_module_allows_safe_code() -> None:
    """Well-formed module code without blocked constructs registers normally."""
    reg = NodeRegistry()
    source = (
        "import json\n"
        "\n"
        "def transform(data: dict) -> dict:\n"
        "    return json.loads(json.dumps(data))\n"
    )
    registered, skipped = register_module_functions("sec_mod5", source, reg)
    assert "transform" in registered
    assert not skipped


MULTILINE_DOCSTRING_SOURCE = (
    "def documented(x: int) -> int:\n"
    '    """Do a thing.\n'
    "\n"
    "    Requires: something installed\n"
    "    (a second line).\n"
    '    """\n'
    "    return x\n"
)


def test_docstring_indentation_does_not_leak_into_descriptions() -> None:
    """A node's description is user-facing text, not Python source.

    Docstring continuation lines carry the function's indentation. Four-space
    indented lines are a code block in Markdown, so a plain sentence like
    "Requires: playwright install chromium" renders as code wherever the
    description is shown as Markdown - which is how an MCP client presents it
    to a model.
    """
    reg = NodeRegistry()
    register_module_functions("docs_mod", MULTILINE_DOCSTRING_SOURCE, reg)
    manifest = next(m for m in reg.manifests() if m.name == "documented")

    indented = [
        line
        for line in manifest.description.split("\n")[1:]
        if line.startswith("    ") and line.strip()
    ]
    assert not indented, (
        f"description keeps the docstring's own indentation: {indented!r}"
    )


def test_static_and_runtime_extraction_agree() -> None:
    """The same function must be described identically however it was read.

    ``ast.get_docstring`` dedents (``clean=True`` by default); ``__doc__.strip()``
    does not. Nodyra reads docstrings both ways - statically when listing what a
    module offers, at runtime when registering it - so the same function was
    described two different ways depending on which surface asked.
    """
    manifests, _skipped = discover_module_function_manifests(
        "agree_static", MULTILINE_DOCSTRING_SOURCE, include_undecorated=True
    )
    static = manifests[0].description

    reg = NodeRegistry()
    register_module_functions("agree_runtime", MULTILINE_DOCSTRING_SOURCE, reg)
    runtime = reg.manifests()[0].description

    assert static == runtime, (
        f"static extraction gave {static!r} but runtime extraction gave "
        f"{runtime!r}; the same function is described differently by surface"
    )

def test_manifest_hides_engine_reserved_ctx() -> None:
    """``ctx`` is the engine-injected RuntimeContext, never a config param.

    Rendering it in the manifest forced authors to supply a meaningless value
    (``mcp_tool`` validated only after users set the hidden param) and hid the
    node's real parameters.
    """
    reg = NodeRegistry()

    @node(
        name="MCP-ish",
        category="MCP",
        registry=reg,
        params={
            "connection_id": {"required": True},
            "tool_name": {"required": True},
            "arguments": {},
        },
    )
    async def mcpish(input=None, *, ctx=None):  # noqa: ANN001
        return {"ok": True}

    manifest = reg.get("mcpish").manifest
    assert [p.name for p in manifest.params] == ["connection_id", "tool_name", "arguments"]
    assert manifest.params[0].required is True
    assert manifest.params[2].required is False


def test_decorator_only_params_appear_in_manifest() -> None:
    """Params declared in the decorator but absent from the signature must
    still reach the inspector (mcp_tool reads them from ctx.node_params)."""
    reg = NodeRegistry()

    @node(
        name="Inspector",
        category="Demo",
        registry=reg,
        params={
            "api_key": {"type": "string", "required": True, "description": "Key."},
            "verbose": {"default": False},
        },
    )
    async def inspector(input=None, *, ctx=None):  # noqa: ANN001
        return {"ok": True}

    manifest = reg.get("inspector").manifest
    specs = {p.name: p for p in manifest.params}
    assert set(specs) == {"api_key", "verbose"}
    assert specs["api_key"].required is True
    assert specs["api_key"].description == "Key."
    assert specs["verbose"].default is False
