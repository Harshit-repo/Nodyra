from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_wait_module():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "wait_for_ready.py"
    spec = importlib.util.spec_from_file_location("wait_for_ready", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_wait_for_ready_returns_on_2xx(monkeypatch) -> None:
    wait = _load_wait_module()
    calls: list[str] = []

    def fake_urlopen(url: str, timeout: float):
        calls.append(f"{url}:{timeout}")
        return _Response()

    monkeypatch.setattr(wait.urllib.request, "urlopen", fake_urlopen)

    wait.wait_for_ready("http://example.test/ready", timeout=1, interval=0.1)

    assert calls == ["http://example.test/ready:0.1"]
