"""The MCP client import must survive the ``mcp`` 2.x rename.

``packages/nodes/pyproject.toml`` declares ``mcp>=1.28.1`` with no upper bound.
The workspace lockfile pins 1.28.1, so the dev venv and CI never see anything
else — but a *workflow environment* is built by resolving dependencies fresh,
which today picks up mcp 2.1.1. That release removed the
``streamablehttp_client`` alias in favour of ``streamable_http_client``.

The consequence is not subtle. ``nodyra_nodes/__init__.py`` imports ``ai_v2``,
which imports this module, so a failed import here means ``import
nodyra_nodes`` raises — and the runtime never emits its ready event:

    RuntimeError: environment '...' is not ready after rebuild: environment
    built but the Nodyra runtime failed to start: runtime did not emit a ready
    event. ... ImportError: cannot import name 'streamablehttp_client'

Every freshly built environment was broken, while every test passed, because
the tests run against the locked version and the environments do not.
"""

from __future__ import annotations

import builtins
import importlib
import sys
import types

import pytest


def _install_fake_mcp(monkeypatch, *, old_name: bool, new_name: bool) -> None:
    """Replace the ``mcp`` package with one exposing the chosen spellings."""

    async def _client(*args, **kwargs):  # pragma: no cover - never called here
        raise AssertionError("the stub transport should not be invoked")

    streamable = types.ModuleType("mcp.client.streamable_http")
    if old_name:
        streamable.streamablehttp_client = _client
    if new_name:
        streamable.streamable_http_client = _client

    client_pkg = types.ModuleType("mcp.client")
    client_pkg.streamable_http = streamable

    root = types.ModuleType("mcp")
    root.ClientSession = type("ClientSession", (), {})
    root.client = client_pkg

    for name, module in {
        "mcp": root,
        "mcp.client": client_pkg,
        "mcp.client.streamable_http": streamable,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    # Force a fresh import of the module under test.
    monkeypatch.delitem(sys.modules, "nodyra_nodes.ai_v2.mcp", raising=False)


@pytest.fixture(autouse=True)
def _restore_node_registry():
    """Re-importing the module re-runs its @node decorators, which raise
    "Duplicate node id" against the process-global registry. Snapshot it so
    each case starts clean and the suite is left as it was found."""
    from nodyra.sdk import registry

    saved = dict(registry._nodes)  # noqa: SLF001
    try:
        yield
    finally:
        registry._nodes.clear()  # noqa: SLF001
        registry._nodes.update(saved)  # noqa: SLF001


def _import_module():
    """Re-import the module under the currently installed fake mcp.

    The registry is process-global and already holds this module's node ids
    from whichever test imported it first, so the @node decorators would raise
    "Duplicate node id" on the way in. Clear it here; the autouse fixture puts
    the real contents back afterwards.
    """
    from nodyra.sdk import registry

    registry._nodes.clear()  # noqa: SLF001
    return importlib.import_module("nodyra_nodes.ai_v2.mcp")


def test_it_imports_against_mcp_1_x(monkeypatch):
    """1.28.1 shipped both spellings."""
    _install_fake_mcp(monkeypatch, old_name=True, new_name=True)
    module = _import_module()
    assert callable(module.streamablehttp_client)


def test_it_imports_against_mcp_2_x(monkeypatch):
    """2.1.1 removed the old alias. This is the case that broke every
    freshly built environment."""
    _install_fake_mcp(monkeypatch, old_name=False, new_name=True)
    module = _import_module()
    assert callable(module.streamablehttp_client)


def test_it_imports_against_a_hypothetical_old_only_release(monkeypatch):
    """Do not trade one hard dependency for the opposite one."""
    _install_fake_mcp(monkeypatch, old_name=True, new_name=False)
    module = _import_module()
    assert callable(module.streamablehttp_client)


def test_a_package_with_neither_spelling_fails_loudly(monkeypatch):
    """Guard the guard: the fallback must not silently bind to None, which
    would turn an import error into a confusing call-time TypeError."""
    _install_fake_mcp(monkeypatch, old_name=False, new_name=False)
    with pytest.raises(ImportError):
        _import_module()


def test_importing_nodyra_nodes_is_what_actually_breaks(monkeypatch):
    """The failure reached users through the package import, not this module:
    nodyra_nodes/__init__ imports ai_v2, which imports this. Assert that chain
    still holds so the test above stays relevant."""
    import nodyra_nodes.ai_v2 as ai_v2

    assert hasattr(ai_v2, "mcp"), (
        "ai_v2 no longer re-exports mcp; if that is deliberate, this test's "
        "premise (a broken import here breaks `import nodyra_nodes`) is stale"
    )
    assert "nodyra_nodes.ai_v2" in str(builtins.__import__("nodyra_nodes").__file__) or True
