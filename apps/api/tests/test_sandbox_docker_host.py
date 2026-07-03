"""SANDBOX_DOCKER_HOST points the sandbox SDK at a Docker-compatible socket."""

from app.config import settings
from app.services import sandbox_pool


def test_make_docker_client_uses_sandbox_docker_host(monkeypatch):
    captured = {}

    class _FakeDocker:
        @staticmethod
        def DockerClient(base_url):  # noqa: N802 - mirrors the SDK surface
            captured["base_url"] = base_url
            return object()

        @staticmethod
        def from_env():
            captured["base_url"] = "from_env"
            return object()

    monkeypatch.setitem(__import__("sys").modules, "docker", _FakeDocker)
    monkeypatch.setattr(
        settings, "sandbox_docker_host", "unix:///run/user/1000/podman/podman.sock"
    )
    sandbox_pool._make_docker_client()
    assert captured["base_url"] == "unix:///run/user/1000/podman/podman.sock"
