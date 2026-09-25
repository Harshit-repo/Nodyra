"""A presigned artifact URL has to be reachable by the browser, not the server.

``GET /artifacts/{id}/download`` answers 307 with a presigned S3 URL. The URL is
signed against ``ARTIFACT_S3_ENDPOINT``, which in ``deploy/docker-compose.yml``
is ``http://minio:9000`` — a hostname that only resolves *inside* the compose
network.

So the shipped stack redirected every browser to a host it cannot resolve:

    location: http://minio:9000/nodyra-artifacts/default/runs/.../filtered-sales.csv

Downloading an artifact is the third step of the in-app activation checklist
("Inspect output or an artifact"), so this broke the same first-run path
everything else in this suite protects.

``ARTIFACT_S3_PUBLIC_ENDPOINT`` names the address a browser should use. It
defaults to empty, which preserves today's behaviour for deployments where the
signing endpoint is already public (real AWS S3, or MinIO behind a shared
hostname) — nothing changes for them.
"""

from __future__ import annotations

import pytest

from app.config import Settings


def _settings(**kw) -> Settings:
    base = {
        "secret_key": "x" * 48,
        "artifact_storage_backend": "s3",
        "artifact_s3_bucket": "nodyra-artifacts",
        "artifact_s3_endpoint": "http://minio:9000",
    }
    base.update(kw)
    return Settings(**base)


def test_the_public_endpoint_setting_exists():
    assert hasattr(_settings(), "artifact_s3_public_endpoint")


def test_it_defaults_to_empty_so_existing_deployments_are_unchanged():
    """A deployment signing against a already-public endpoint must keep working
    exactly as before."""
    assert _settings().artifact_s3_public_endpoint == ""


def test_the_signing_endpoint_is_used_when_no_public_one_is_set():
    from app.services.s3_artifact_backend import browser_endpoint

    assert browser_endpoint(_settings()) == "http://minio:9000"


def test_the_public_endpoint_wins_when_set():
    """The bug this fixes: the browser must be sent to an address it can
    actually resolve."""
    from app.services.s3_artifact_backend import browser_endpoint

    resolved = browser_endpoint(
        _settings(artifact_s3_public_endpoint="http://localhost:9000")
    )
    assert resolved == "http://localhost:9000"


def test_an_empty_signing_endpoint_stays_empty():
    """Real AWS S3 sets no endpoint at all; boto3 must be left to its default
    rather than handed an empty string."""
    from app.services.s3_artifact_backend import browser_endpoint

    assert browser_endpoint(_settings(artifact_s3_endpoint="")) == ""


def test_the_shipped_compose_points_the_browser_at_a_published_port():
    """The stack must work out of the box. minio publishes 9000 on loopback, so
    that is the address a browser on the host can reach."""
    from pathlib import Path

    compose = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"
    if not compose.exists():
        pytest.skip("deploy/ is not present in this checkout")
    text = compose.read_text(encoding="utf-8")

    assert "ARTIFACT_S3_PUBLIC_ENDPOINT" in text, (
        "compose signs artifact URLs against http://minio:9000, which a browser "
        "on the host cannot resolve; it must also set a public endpoint"
    )
