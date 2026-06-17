"""C1: expression preview must evaluate in an isolated, secret-free subprocess.

The worker is the trust boundary for editor-typed ``{{ }}`` expressions: a
sandbox escape in ``noodle.expr`` must land in a process with no app secrets
in its environment and no DB access — not in the API process.
"""

import pytest

from app.services import expr_preview


@pytest.fixture(autouse=True)
async def _fresh_worker():
    yield
    await expr_preview.shutdown()


async def test_preview_evaluates_in_subprocess():
    out = await expr_preview.preview(
        value="{{ $json.a + 1 }}", json_value={"a": 41}, inputs={}, nodes={}
    )
    assert out["error"] is None
    assert out["result"] == 42


async def test_worker_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("NOODLE_SECRET_KEY", "super-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
    await expr_preview.shutdown()  # force respawn under the patched env
    assert await expr_preview.probe_env("NOODLE_SECRET_KEY") is None
    assert await expr_preview.probe_env("DATABASE_URL") is None
    assert await expr_preview.probe_env("PATH") is not None


async def test_timeout_kills_and_restarts_worker():
    out = await expr_preview.preview(
        value="{{ sum(1 for _ in range(10**10)) }}",
        json_value=None, inputs={}, nodes={}, timeout=0.5,
    )
    assert out["error"] is not None and "timeout" in out["error"].lower()
    # worker restarted: next request still works
    again = await expr_preview.preview(
        value="{{ 1 + 1 }}", json_value=None, inputs={}, nodes={}
    )
    assert again["result"] == 2
