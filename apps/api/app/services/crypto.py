"""Encryption, password hashing, and signed tokens.

Credentials are encrypted at rest with Fernet. Passwords use PBKDF2-HMAC.
Session tokens are HMAC-signed — no third-party JWT dependency needed.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_logger = logging.getLogger(__name__)

_PBKDF2_ROUNDS = 200_000


# Cache the derived Fernet keyed on the secret it was built from. Deriving the
# key (sha256 + base64) and constructing Fernet on every encrypt/decrypt/wrap
# is pure waste; the cache keys on the current secret so tests that monkeypatch
# ``settings.secret_key`` transparently rebuild it.
_fernet_cache: tuple[str, Fernet] | None = None


def _fernet() -> Fernet:
    global _fernet_cache
    secret = settings.secret_key
    if _fernet_cache is not None and _fernet_cache[0] == secret:
        return _fernet_cache[1]
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    fernet = Fernet(key)
    _fernet_cache = (secret, fernet)
    return fernet


def encrypt_data(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_data(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except (InvalidToken, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Per-credential DEK (Data Encryption Key) helpers
#
# Each credential gets a freshly generated 32-byte Fernet key (the DEK).
# The DEK is encrypted ("wrapped") by the master KEK derived from SECRET_KEY.
# The wrapped DEK is stored alongside the ciphertext so that:
#   - rotating the master key only requires re-wrapping DEKs, not re-encrypting data
#   - a compromised single credential never leaks keys for other credentials
# ---------------------------------------------------------------------------

def generate_dek() -> bytes:
    """Return a fresh random 32-byte key suitable for Fernet."""
    return Fernet.generate_key()


def wrap_dek(dek: bytes) -> str:
    """Encrypt a DEK with the master KEK; return base64url ciphertext string."""
    return _fernet().encrypt(dek).decode()


def unwrap_dek(wrapped_dek: str) -> bytes:
    """Decrypt a wrapped DEK using the master KEK."""
    return _fernet().decrypt(wrapped_dek.encode())


def encrypt_with_dek(data: dict, dek: bytes) -> str:
    """Encrypt credential data using the per-credential DEK."""
    f = Fernet(dek)
    return f.encrypt(json.dumps(data).encode()).decode()


def decrypt_with_dek(token: str, dek: bytes) -> dict:
    """Decrypt credential data using the per-credential DEK."""
    try:
        f = Fernet(dek)
        return json.loads(f.decrypt(token.encode()).decode())
    except (InvalidToken, ValueError):
        return {}


def encrypt_credential(data: dict) -> tuple[str, str]:
    """Encrypt credential data with a fresh DEK.

    Returns (encrypted_data, wrapped_dek) — both should be stored on the
    Credential row.
    """
    dek = generate_dek()
    return encrypt_with_dek(data, dek), wrap_dek(dek)


def decrypt_credential(encrypted_data: str, encrypted_dek: str | None) -> dict:
    """Decrypt credential data.

    Falls back to legacy KEK-direct decryption when ``encrypted_dek`` is None
    (rows created before the DEK migration).
    """
    if encrypted_dek is None:
        # Legacy path: data was encrypted directly with the master KEK.
        return decrypt_data(encrypted_data)
    try:
        dek = unwrap_dek(encrypted_dek)
        return decrypt_with_dek(encrypted_data, dek)
    except (InvalidToken, ValueError):
        _logger.warning("decrypt_credential: failed to decrypt credential data (invalid token or key)")
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
    # ``typ`` discriminates a user *session* token from the other signed tokens
    # minted with the same key (runner registration, OAuth state, k8s run).
    # ``verify_token`` requires typ=="session" so a purpose token can never be
    # replayed as a session credential through the auth gate (see TOK-1).
    payload = {"sub": user_id, "exp": int(time.time()) + ttl, "typ": "session"}
    body = _encode_body(payload)
    return f"{body}.{_sign(body)}"


def create_payload_token(payload: dict, ttl_seconds: int) -> str:
    """Create a signed token carrying an arbitrary JSON payload."""
    full = {**payload, "exp": int(time.time()) + ttl_seconds}
    body = _encode_body(full)
    return f"{body}.{_sign(body)}"


def verify_token(token: str) -> str | None:
    try:
        body, signature = token.split(".", maxsplit=1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(body)):
        return None
    payload = _decode_body(body)
    if payload is None or payload.get("exp", 0) < time.time():
        return None
    # Only genuine session tokens authenticate a user. Tokens minted for other
    # purposes (runner registration, OAuth state, k8s run) carry a different/no
    # ``typ`` and must be decoded via ``decode_payload_token`` by their own
    # handlers — never accepted here (TOK-1).
    if payload.get("typ") != "session":
        return None
    return payload.get("sub")


def decode_payload_token(token: str) -> dict | None:
    """Verify and decode a payload token, returning the full payload dict or None."""
    try:
        body, signature = token.split(".", maxsplit=1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(body)):
        return None
    payload = _decode_body(body)
    if payload is None or payload.get("exp", 0) < time.time():
        return None
    return payload
