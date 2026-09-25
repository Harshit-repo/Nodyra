"""Detection of how the server process was actually launched."""

import sys

from app.state import proxy_headers_enabled


def test_no_uvicorn_means_nothing_rewrites_the_client(monkeypatch) -> None:
    """Under the test client or another ASGI server, the peer is the peer."""
    monkeypatch.delitem(sys.modules, "uvicorn", raising=False)

    assert proxy_headers_enabled() is False


def test_explicit_no_proxy_headers_is_respected(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", _uvicorn_without_server())
    monkeypatch.setattr(
        sys, "argv", ["uvicorn", "app.main:app", "--no-proxy-headers"]
    )

    assert proxy_headers_enabled() is False


def test_uvicorn_default_is_treated_as_enabled(monkeypatch) -> None:
    """uvicorn turns proxy headers on unless told otherwise."""
    monkeypatch.setitem(sys.modules, "uvicorn", _uvicorn_without_server())
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app", "--port", "8000"])

    assert proxy_headers_enabled() is True


def test_a_live_server_config_wins_over_argv(monkeypatch) -> None:
    """Ask the running server first — argv is only the fallback."""
    module = _uvicorn_without_server()
    module.Server._nodyra_current = type(
        "S", (), {"config": type("C", (), {"proxy_headers": False})()}
    )()
    monkeypatch.setitem(sys.modules, "uvicorn", module)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])

    assert proxy_headers_enabled() is False


def _uvicorn_without_server():
    import types

    module = types.ModuleType("uvicorn")
    module.Server = type("Server", (), {})
    return module
