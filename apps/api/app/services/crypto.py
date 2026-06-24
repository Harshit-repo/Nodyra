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
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings

_logger = logging.getLogger(__name__)

_PBKDF2_ROUNDS = 200_000


class CredentialDecryptError(RuntimeError):
    """Raised when an existing credential's ciphertext cannot be decrypted.

    H1: callers on the execution path must fail loudly here rather than run a
    workflow with silently-empty credentials (the old ``return {}`` behaviour).
    """


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
    # B-08: HKDF-SHA256 with a domain-specific info label is the correct KDF
    # for deriving a sub-key from a high-entropy master secret. A raw sha256
    # hash offers no domain separation and is not a proper KDF. Salt=None is
    # intentional (deterministic derivation — no per-call state to store).
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"noodle-credential-kek",
    ).derive(secret.encode())
    key = base64.urlsafe_b64encode(raw)
    fernet = Fernet(key)
    _fernet_cache = (secret, fernet)
    return fernet


def encrypt_data(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_data(token: str, *, strict: bool = False) -> dict:
    """Decrypt a Fernet-encrypted JSON payload.

    When ``strict=False`` (the default, for back-compat), returns ``{}`` on
    any decrypt failure — callers that consume the result as a dict must
    interpret ``{}`` as either "valid empty data" or "decrypt failure"
    depending on context.

    When ``strict=True``, raises :class:`CredentialDecryptError` so the
    caller can distinguish a corrupt/wrong-key token from a legitimate
    empty payload.
    """
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except (InvalidToken, ValueError) as exc:
        if strict:
            raise CredentialDecryptError(
                "decrypt_data: invalid token or wrong key"
            ) from exc
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


# ---------------------------------------------------------------------------
# Per-org KEK (Phase E of the multi-tenancy plan)
#
# Envelope chain: data <- DEK <- org KEK <- master KEK (or external KMS).
# Each organization gets its own KEK; the DEK of every credential in that org
# is wrapped by it, so one tenant's bug or key exposure never unlocks
# another's secrets, and rotation per org only rewraps that org's DEKs.
# ---------------------------------------------------------------------------

def generate_org_kek() -> bytes:
    """Return a fresh random Fernet key suitable as an org KEK."""
    return Fernet.generate_key()


def wrap_org_kek(org_kek: bytes) -> str:
    """Encrypt an org KEK with the master KEK (env/KMS provider)."""
    return _fernet().encrypt(org_kek).decode()


def unwrap_org_kek(wrapped: str) -> bytes:
    """Decrypt a wrapped org KEK using the master KEK."""
    return _fernet().decrypt(wrapped.encode())


def rewrap_dek(encrypted_dek: str, org_kek: bytes) -> str:
    """Move a master-wrapped DEK under an org KEK (the Phase E migration).

    Raises InvalidToken when the input isn't a master-wrapped DEK — callers
    decide whether that means "already org-wrapped" (skip) or corruption.
    """
    dek = unwrap_dek(encrypted_dek)
    return Fernet(org_kek).encrypt(dek).decode()


def encrypt_credential(data: dict, org_kek: bytes | None = None) -> tuple[str, str]:
    """Encrypt credential data with a fresh DEK.

    The DEK is wrapped by ``org_kek`` when given (Phase E envelope), else by
    the master KEK (legacy/single-tenant path). Returns
    (encrypted_data, wrapped_dek) — both stored on the Credential row.
    """
    dek = generate_dek()
    if org_kek is not None:
        wrapped = Fernet(org_kek).encrypt(dek).decode()
    else:
        wrapped = wrap_dek(dek)
    return encrypt_with_dek(data, dek), wrapped


def _decrypt_with_dek_strict(token: str, dek: bytes) -> dict:
    """Decrypt with a DEK, letting InvalidToken/ValueError propagate."""
    return json.loads(Fernet(dek).decrypt(token.encode()).decode())


def decrypt_credential(
    encrypted_data: str,
    encrypted_dek: str | None,
    org_kek: bytes | None = None,
    *,
    strict: bool = False,
) -> dict:
    """Decrypt credential data.

    Unwrap chain (newest first): org KEK -> master KEK -> legacy KEK-direct
    ciphertext (``encrypted_dek is None``). Fernet's HMAC guarantees only the
    right key succeeds, so trying in order is safe.

    ``strict`` (H1): raise :class:`CredentialDecryptError` when every unwrap
    path fails for a credential that *does* hold ciphertext, instead of
    returning ``{}``. The execution path uses this so a corrupt or
    wrong-key credential aborts the run rather than degrading to no auth. A
    credential that decrypts cleanly to an empty ``{}`` is a valid value and
    never raises.
    """
    try:
        if encrypted_dek is None:
            # Legacy path: data was encrypted directly with the master KEK.
            return json.loads(_fernet().decrypt(encrypted_data.encode()).decode())
        if org_kek is not None:
            # The org KEK only applies if it can unwrap the DEK; a failure here
            # just means the row is still master-wrapped, so fall through.
            try:
                dek = Fernet(org_kek).decrypt(encrypted_dek.encode())
            except (InvalidToken, ValueError):
                dek = None
            if dek is not None:
                return _decrypt_with_dek_strict(encrypted_data, dek)
        dek = unwrap_dek(encrypted_dek)
        return _decrypt_with_dek_strict(encrypted_data, dek)
    except (InvalidToken, ValueError) as exc:
        if strict:
            raise CredentialDecryptError(
                "credential could not be decrypted (invalid token or wrong key)"
            ) from exc
        _logger.warning(
            "decrypt_credential: failed to decrypt credential data "
            "(invalid token or key)"
        )
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
    now = time.time()
    # ``iat`` (issued-at, float epoch seconds) lets revocation invalidate every
    # token minted before a per-user cutoff (User.sessions_valid_after) — see
    # decode_session_token / current_user (C1). Sub-second resolution means a
    # re-login moments after a "log out everywhere" reliably outlives the
    # cutoff while the revoked token does not.
    payload = {"sub": user_id, "iat": now, "exp": now + ttl, "typ": "session"}
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


def decode_session_token(token: str) -> dict | None:
    """Verify a session token and return its full payload (``sub``, ``iat``).

    Like :func:`verify_token` but returns the whole payload so callers can
    enforce ``iat``-based revocation (C1). Returns ``None`` for tampered,
    expired, or non-session tokens.
    """
    try:
        body, signature = token.split(".", maxsplit=1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(body)):
        return None
    payload = _decode_body(body)
    if payload is None or payload.get("exp", 0) < time.time():
        return None
    if payload.get("typ") != "session":
        return None
    return payload


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
