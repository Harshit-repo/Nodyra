"""Legacy env aliases keep working for one release after the rename."""

from __future__ import annotations


def test_settings_secret_key_honours_legacy_env(monkeypatch) -> None:
    monkeypatch.delenv("NODYRA_SECRET_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("NOODLE_SECRET_KEY", "legacy-secret")

    from app.config import Settings

    assert Settings().secret_key == "legacy-secret"


def test_new_settings_env_wins_over_legacy(monkeypatch) -> None:
    monkeypatch.setenv("NODYRA_LICENSE_KEY", "new-license")
    monkeypatch.setenv("NOODLE_LICENSE_KEY", "legacy-license")

    from app.config import Settings

    assert Settings().license_key == "new-license"


def test_private_egress_honours_legacy_env(monkeypatch) -> None:
    monkeypatch.delenv("NODYRA_ALLOW_PRIVATE_EGRESS", raising=False)
    monkeypatch.setenv("NOODLE_ALLOW_PRIVATE_EGRESS", "1")

    from nodyra_nodes.http_security import private_egress_allowed
    from nodyra_nodes.httpx_security import resolve_pinned

    assert private_egress_allowed() is True
    assert resolve_pinned("http://10.0.0.5:8080/local").url == "http://10.0.0.5:8080/local"


def test_new_private_egress_env_wins_over_legacy(monkeypatch) -> None:
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "0")
    monkeypatch.setenv("NOODLE_ALLOW_PRIVATE_EGRESS", "1")

    from nodyra_nodes.http_security import private_egress_allowed

    assert private_egress_allowed() is False
