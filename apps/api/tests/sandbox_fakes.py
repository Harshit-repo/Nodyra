"""Fake docker SDK surface for sandbox tests (no daemon required).

The sandbox code calls the sync SDK via run_in_executor, so these fakes are
plain-sync. FakeRawSock.recv blocks on a queue.Queue exactly like a real
attach socket blocks on the wire; feed events with .feed(dict) and simulate
container death with .feed_eof().
"""

import json
import queue


class FakeRawSock:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self.sent: list[bytes] = []
        self.timeout: float | None = None

    def feed(self, obj: dict) -> None:
        # Real no-TTY attach streams are multiplexed: 8-byte frame header
        # (stream type 1=stdout + big-endian length), then the payload.
        payload = (json.dumps(obj) + "\n").encode()
        self._q.put(bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload)

    def feed_raw(self, data: bytes) -> None:
        self._q.put(data)

    def feed_eof(self) -> None:
        self._q.put(b"")

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _n: int) -> bytes:
        try:
            return self._q.get(timeout=self.timeout if self.timeout else 5.0)
        except queue.Empty:
            raise TimeoutError("fake recv timeout")

    def settimeout(self, t: float) -> None:
        self.timeout = t

    def sent_messages(self) -> list[dict]:
        """Decode every newline-framed JSON message written by the host."""
        blob = b"".join(self.sent)
        return [json.loads(line) for line in blob.split(b"\n") if line.strip()]


class FakeSock:
    """docker's attach_socket return — code reaches the raw socket via _sock."""

    def __init__(self):
        self._sock = FakeRawSock()


class FakeContainer:
    def __init__(self, name: str):
        self.name = name
        self.removed = False
        self.sock = FakeSock()

    def attach_socket(self, params=None):
        return self.sock

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeContainers:
    def __init__(self, client: "FakeDockerClient"):
        self._client = client

    def run(self, image: str, **kwargs) -> FakeContainer:
        self._client.run_calls.append({"image": image, **kwargs})
        c = FakeContainer(kwargs.get("name", f"c{len(self._client.containers_made)}"))
        # A real noodle_runtime emits ready as its first line.
        if self._client.auto_ready:
            c.sock._sock.feed({"type": "ready"})
        self._client.containers_made.append(c)
        return c

    def get(self, name: str) -> FakeContainer:
        for c in self._client.containers_made:
            if c.name == name:
                return c
        raise KeyError(name)


class _FakeImages:
    def __init__(self):
        self.built: list[str] = []
        self.build_calls: list[dict] = []
        self.existing: set[str] = set()

    def get(self, tag: str):
        if tag in self.existing or tag in self.built:
            return object()
        raise KeyError(tag)  # NotFound — triggers build

    def build(self, fileobj=None, tag: str = "", rm: bool = True, **kwargs):
        self.built.append(tag)
        self.build_calls.append({"fileobj": fileobj, "tag": tag, "rm": rm, **kwargs})
        return (object(), iter(()))


class _FakeNetworks:
    def __init__(self):
        self.existing: set[str] = set()
        self.create_error: Exception | None = None

    def get(self, name: str):
        if name in self.existing:
            return name
        raise KeyError(name)

    def create(self, name: str, **kwargs):
        if self.create_error is not None:
            err, self.create_error = self.create_error, None
            raise err
        self.existing.add(name)
        return name


class FakeDockerClient:
    def __init__(self, runtimes: tuple[str, ...] = ("runc",), auto_ready: bool = True):
        self.run_calls: list[dict] = []
        self.containers_made: list[FakeContainer] = []
        self.auto_ready = auto_ready
        self.containers = _FakeContainers(self)
        self.images = _FakeImages()
        self.networks = _FakeNetworks()
        self._runtimes = runtimes

    def info(self) -> dict:
        return {"Runtimes": {r: {"path": r} for r in self._runtimes}}

    def ping(self) -> bool:
        return True
