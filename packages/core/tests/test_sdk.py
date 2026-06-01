import pytest

from noodle.sdk import (
    NodeRegistry,
    discover_module_function_manifests,
    discover_module_nodes,
    node,
    register_module_functions,
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
from noodle import node


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

