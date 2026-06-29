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

import jwt as _jwt  # PyJWT
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings

_logger = logging.getLogger(__name__)

_PBKDF2_ROUNDS = 600_000  # OWASP 2025 recommendation for PBKDF2-HMAC-SHA256


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
    """Hash a password with PBKDF2-HMAC-SHA256.

    Format: ``rounds:salt_b64:digest_b64`` — rounds are stored in-band so
    ``_PBKDF2_ROUNDS`` can be increased without breaking existing hashes.
    """
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return (
        f"{_PBKDF2_ROUNDS}:"
        f"{base64.b64encode(salt).decode()}:"
        f"{base64.b64encode(digest).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored PBKDF2 hash.

    Handles both the legacy ``salt:digest`` format (200k rounds implied) and the
    current ``rounds:salt:digest`` format so existing passwords survive a rounds
    increase.
    """
    try:
        parts = stored.split(":")
        if len(parts) == 3:
            rounds_str, salt_b64, digest_b64 = parts
            rounds = int(rounds_str)
        else:
            # Legacy format: salt:digest (200k rounds)
            salt_b64, digest_b64 = parts
            rounds = 200_000
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return hmac.compare_digest(digest, expected)


# ── JWT token functions (P1-2: standard JWT replaces self-rolled HMAC) ──────
#
# New tokens are standard HS256 JWTs with these claims:
#   iss  = "noodle"
#   aud  = "noodle-api"
#   sub  = user_id (session) or purpose-specific (payload)
#   iat  = issued-at (epoch seconds, float for sub-second revocation)
#   exp  = expiry (epoch seconds)
#   typ  = "session" | "oauth_state" | "runner_registration" | "k8s_run" | ...
#
# Legacy tokens use the format ``base64url(json).hex(HMAC)`` and are still
# accepted during the transition period.  Remove the legacy path in a future
# release once all tokens have cycled through their TTL.

def _jwt_encode(payload: dict, ttl_seconds: float) -> str:
    """Encode a payload as a standard HS256 JWT."""
    now = time.time()
    claims = {
        **payload,
        "iss": "noodle",
        "aud": "noodle-api",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return _jwt.encode(
        claims, settings.secret_key, algorithm="HS256", headers={"typ": "JWT"}
    )


def _jwt_decode(token: str, *, require_sub: bool = False) -> dict | None:
    """Decode and verify a standard HS256 JWT.  Returns None on any failure.

    When ``require_sub`` is True, the ``sub`` claim must be present.
    Session tokens require it; payload tokens (OAuth state, runner
    registration, etc.) do not.
    """
    required = ["exp", "iss"]
    if require_sub:
        required.append("sub")
    try:
        return _jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            issuer="noodle",
            audience="noodle-api",
            options={"require": required},
        )
    except _jwt.PyJWTError:
        return None


# ── Legacy token support (transition period) ────────────────────────────────


def _legacy_sign(body: str) -> str:
    return hmac.new(
        settings.secret_key.encode(), body.encode(), hashlib.sha256
    ).hexdigest()


def _legacy_encode_body(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _legacy_decode_body(body: str) -> dict | None:
    try:
        padded = body + "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None


def _legacy_verify_and_decode(body: str, signature: str) -> dict | None:
    if not hmac.compare_digest(signature, _legacy_sign(body)):
        return None
    payload = _legacy_decode_body(body)
    if payload is None or payload.get("exp", 0) < time.time():
        return None
    return payload


def _is_legacy_token(token: str) -> bool:
    """Heuristic: a legacy token has exactly one '.' with a 64-char hex suffix.
    A JWT has two '.' separators."""
    return token.count(".") == 1 and len(token.rsplit(".", 1)[1]) == 64


def _try_legacy_decode_session(token: str) -> dict | None:
    try:
        body, signature = token.split(".", maxsplit=1)
    except ValueError:
        return None
    payload = _legacy_verify_and_decode(body, signature)
    if payload is None or payload.get("typ") != "session":
        return None
    return payload


# ── Public API (unchanged signatures) ───────────────────────────────────────


def create_token(
    user_id: str, ttl_seconds: int | None = None, *, client_ip: str = ""
) -> str:
    """Create a standard HS256 JWT session token (P1-2).

    When ``client_ip`` is provided and ``auth_bind_token_to_ip`` is enabled,
    the token carries an ``ip`` claim so it is only valid from that address
    (P1-6).
    """
    ttl = float(
        ttl_seconds if ttl_seconds is not None else settings.auth_token_ttl_seconds
    )
    claims: dict = {"sub": user_id, "typ": "session"}
    if client_ip and settings.auth_bind_token_to_ip:
        claims["ip"] = client_ip
    return _jwt_encode(claims, ttl)


def create_payload_token(payload: dict, ttl_seconds: int) -> str:
    """Create a standard HS256 JWT carrying arbitrary claims (P1-2)."""
    return _jwt_encode({**payload}, float(ttl_seconds))


def verify_token(token: str, *, client_ip: str = "") -> str | None:
    """Verify a session token and return ``sub`` (user_id), or None.

    When ``client_ip`` is provided and the token carries an ``ip`` claim
    (P1-6), the addresses must match.

    Accepts both standard JWT and legacy-format tokens (P1-2).
    """
    if _is_legacy_token(token):
        payload = _try_legacy_decode_session(token)
        return payload.get("sub") if payload else None
    payload = _jwt_decode(token, require_sub=True)
    if payload is None:
        return None
    if payload.get("typ") != "session":
        return None
    if client_ip and payload.get("ip") and payload["ip"] != client_ip:
        return None
    return payload.get("sub")


def decode_session_token(
    token: str, *, client_ip: str = ""
) -> dict | None:
    """Verify a session token and return its full payload, or None.

    Accepts both standard JWT and legacy-format tokens (P1-2).
    """
    if _is_legacy_token(token):
        return _try_legacy_decode_session(token)
    payload = _jwt_decode(token, require_sub=True)
    if payload is None or payload.get("typ") != "session":
        return None
    if client_ip and payload.get("ip") and payload["ip"] != client_ip:
        return None
    return payload


def decode_payload_token(token: str) -> dict | None:
    """Verify a purpose-specific token and return its payload, or None.

    Accepts both standard JWT and legacy-format tokens (P1-2).
    """
    if _is_legacy_token(token):
        try:
            body, signature = token.split(".", maxsplit=1)
        except ValueError:
            return None
        return _legacy_verify_and_decode(body, signature)
    return _jwt_decode(token, require_sub=False)
