"""Test-only Ed25519 keypair + license minting helper.

NOT used in production. Production verifies against the public key baked into
``app/services/licensing.py`` (or ``settings.license_public_key``); tests point
``settings.license_public_key`` at ``TEST_PUBLIC_KEY_PEM`` and mint with this
private key so the real private key never lives in the repo.
"""
from __future__ import annotations

import base64
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Deterministic 32-byte test seed. Regenerating only affects tests.
_TEST_SEED = b"noodle-test-license-signing-seed"  # exactly 32 bytes
_PRIV = Ed25519PrivateKey.from_private_bytes(_TEST_SEED)

TEST_PUBLIC_KEY_PEM = _PRIV.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()


def make_key(payload: dict) -> str:
    """Sign ``payload`` with the test private key → '<b64url(json)>.<b64url(sig)>'."""
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = _PRIV.sign(body)
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return f"{body.decode()}.{sig_b64.decode()}"


def pro_key(**overrides) -> str:
    payload = {
        "tier": "pro",
        "customer": "Test Co",
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return make_key(payload)


def enterprise_key(**overrides) -> str:
    payload = {"tier": "enterprise", "customer": "Test Co"}
    payload.update(overrides)
    return make_key(payload)
