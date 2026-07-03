"""LocalBackend.stats() is cached: repeated calls don't re-walk the tree."""

from app.services import artifact_backends
from app.services.artifact_backends import LocalBackend


def test_stats_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact_backends.settings, "artifacts_dir", str(tmp_path))
    artifact_backends._stats_cache = None
    backend = LocalBackend()
    (tmp_path / "f.bin").write_bytes(b"12345")
    first = backend.stats()
    assert first["bytes"] == 5
    (tmp_path / "g.bin").write_bytes(b"12345")
    assert backend.stats()["bytes"] == 5
    artifact_backends._stats_cache = None
    assert backend.stats()["bytes"] == 10
