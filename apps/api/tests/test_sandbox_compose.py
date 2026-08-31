from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _service_block(compose_text: str, service: str) -> str:
    marker = f"  {service}:\n"
    start = compose_text.index(marker)
    rest = compose_text[start + len(marker):]
    match = re.search(r"\n  [A-Za-z0-9_-]+:\n", rest)
    end = len(rest) if match is None else match.start()
    return rest[:end]


def test_sandbox_compose_uses_socket_proxy_not_raw_worker_socket() -> None:
    base = (REPO_ROOT / "deploy" / "docker-compose.yml").read_text()
    sandbox = (REPO_ROOT / "deploy" / "docker-compose.sandbox.yml").read_text()

    base_worker = _service_block(base, "worker")
    sandbox_worker = _service_block(sandbox, "worker")
    proxy = _service_block(sandbox, "docker-socket-proxy")

    assert "/var/run/docker.sock" not in base_worker
    assert "/var/run/docker.sock" not in sandbox_worker
    assert "SANDBOX_DOCKER_HOST: ${SANDBOX_DOCKER_HOST:-tcp://docker-socket-proxy:2375}" in sandbox_worker

    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in proxy
    assert 'EXEC: "0"' in proxy
    assert 'VOLUMES: "0"' in proxy
    assert 'SECRETS: "0"' in proxy
    assert 'BUILD: "1"' in proxy
    assert 'CONTAINERS: "1"' in proxy
    assert 'IMAGES: "1"' in proxy
    assert 'NETWORKS: "1"' in proxy


def test_base_compose_publishes_api_and_web_on_loopback_by_default() -> None:
    base = (REPO_ROOT / "deploy" / "docker-compose.yml").read_text()

    api = _service_block(base, "api")
    web = _service_block(base, "web")

    # The property, not the literal: the host side must default to loopback,
    # and the container side must still be the service's real port. The host
    # *port* is deliberately overridable (NODYRA_API_PORT / NODYRA_WEB_PORT) so
    # a developer who already runs something on 8000 or 5173 can move it — that
    # must not require editing this file, and must not change the bind host.
    expected_host = "${NODYRA_BIND_HOST:-127.0.0.1}"
    assert f'"{expected_host}:${{NODYRA_API_PORT:-8000}}:8000"' in api
    assert f'"{expected_host}:${{NODYRA_WEB_PORT:-5173}}:5173"' in web

    # Neither may publish on every interface. A mapping with no host part, or
    # an explicit 0.0.0.0, would expose an auth-disabled demo to the network.
    for name, block in (("api", api), ("web", web)):
        for line in block.splitlines():
            stripped = line.strip().lstrip("- ").strip('"')
            if ":" not in stripped or "NODYRA_BIND_HOST" in stripped:
                continue
            if stripped.count(":") >= 1 and stripped[0].isdigit():
                raise AssertionError(
                    f"{name} publishes {stripped!r} without a loopback host part"
                )
            assert not stripped.startswith("0.0.0.0"), (
                f"{name} publishes {stripped!r} on every interface"
            )
