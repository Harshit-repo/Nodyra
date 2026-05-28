"""Encryption, password hashing, and signed tokens.

Credentials are encrypted at rest with Fernet. Passwords use PBKDF2-HMAC.
Session tokens are HMAC-signed — no third-party JWT dependency needed.
"""

import base64
import hashlib
import hmac
import json
import os
import time

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_PBKDF2_ROUNDS = 200_000


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.secret_key.encode()).digest()
    )
    return Fernet(key)


def encrypt_data(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_data(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except (InvalidToken, ValueError):
        return {}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"{base64.b64encode(salt).decode()}:{base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, digest_b64 = stored.split(":")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return hmac.compare_digest(digest, expected)


def _sign(body: str) -> str:
    return hmac.new(
        settings.secret_key.encode(), body.encode(), hashlib.sha256
    ).hexdigest()


def _encode_body(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _decode_body(body: str) -> dict | None:
    try:
        padded = body + "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None


def create_token(user_id: str, ttl_seconds: int | None = None) -> str:
    ttl = ttl_seconds if ttl_seconds is not None else settings.auth_token_ttl_seconds
    payload = {"sub": user_id, "exp": int(time.time()) + ttl}
    body = _encode_body(payload)
    return f"{body}.{_sign(body)}"


def create_payload_token(payload: dict, ttl_seconds: int) -> str:
    """Create a signed token carrying an arbitrary JSON payload."""
    full = {**payload, "exp": int(time.time()) + ttl_seconds}
    body = _encode_body(full)
    return f"{body}.{_sign(body)}"


def verify_token(token: str) -> str | None:
    try:
        body, signature = token.split(".")
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(body)):
        return None
    payload = _decode_body(body)
    if payload is None or payload.get("exp", 0) < time.time():
        return None
    return payload.get("sub")


def decode_payload_token(token: str) -> dict | None:
    """Verify and decode a payload token, returning the full payload dict or None."""
    try:
        body, signature = token.split(".")
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(body)):
        return None
    payload = _decode_body(body)
    if payload is None or payload.get("exp", 0) < time.time():
        return None
    return payload
