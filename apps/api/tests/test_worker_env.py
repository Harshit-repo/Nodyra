"""X1: runtime workers must not inherit API secrets via their environment.

``_RuntimeProcess.spawn`` used to do ``env = dict(os.environ)``, handing every
Code node the master KEK (SECRET_KEY), DATABASE_URL, and OAuth client secrets.
``_worker_env`` builds the subprocess environment from an explicit allowlist
instead.
"""

from app.services.runtime_pool import _worker_env


def test_secrets_never_reach_worker_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "super-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "oauth-secret")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    env = _worker_env()
    for forbidden in (
        "SECRET_KEY",
        "DATABASE_URL",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "INTERNAL_API_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert forbidden not in env


def test_required_os_vars_pass_through(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _worker_env()
    assert env["PATH"] == "/usr/bin"
    # Always set explicitly so the runtime applies the same per-node default
    # as the in-process engine.
    assert "NOODLE_CODE_NODE_TIMEOUT_SECONDS" in env


def test_noodle_prefixed_vars_pass_through(monkeypatch):
    monkeypatch.setenv("NOODLE_CUSTOM_FLAG", "1")
    env = _worker_env()
    assert env["NOODLE_CUSTOM_FLAG"] == "1"


def test_allowlist_is_case_insensitive_for_windows_names(monkeypatch):
    # Windows env var names are case-insensitive; os.environ normalises to
    # upper-case there, but guard against mixed-case entries on POSIX too.
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    env = _worker_env()
    assert "C:\\Windows" in env.values()
