import pytest

from noodle.sdk import NodeRegistry, discover_module_function_manifests, node


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
    # Every parameter is both a wired input port and an inspector field.
    assert [p.name for p in manifest.inputs] == [
        "rows", "limit", "active", "ratio", "meta", "anything", "dynamic",
    ]
    params = {param.name: param for param in manifest.params}
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
    # Every param appears in both inputs and params (edge wins at runtime).
    assert [p.name for p in manifest.inputs] == ["url", "retries"]
    assert [(p.name, p.type, p.default, p.required) for p in manifest.params] == [
        ("url", "string", None, True),
        ("retries", "integer", 3, False),
    ]
