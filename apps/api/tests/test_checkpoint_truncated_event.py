"""Oversized checkpoints report whether the payload was truncated."""

from app.services import run_checkpoints
from app.services.run_checkpoints import _save_checkpoint


async def test_save_checkpoint_reports_truncation(monkeypatch):
    saved = {}

    class _FakeSession:
        async def execute(self, *a, **k):
            saved["hit"] = True

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(run_checkpoints, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(run_checkpoints, "_MAX_CHECKPOINT_BYTES", 64)
    truncated = await _save_checkpoint(
        "run-1",
        {"n1": {"main": {"blob": "x" * 500}}},
        {"n1"},
        "n1",
    )
    assert truncated is True
    assert saved["hit"] is True


async def test_save_checkpoint_small_not_truncated(monkeypatch):
    class _FakeSession:
        async def execute(self, *a, **k):
            pass

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(run_checkpoints, "SessionLocal", lambda: _FakeSession())
    truncated = await _save_checkpoint(
        "run-1",
        {"n1": {"main": {"a": 1}}},
        {"n1"},
        "n1",
    )
    assert truncated is False
