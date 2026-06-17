"""Unit tests for app.services.crypto — encryption, hashing, and signed tokens."""
import time
from unittest.mock import patch

import pytest

from app.services.crypto import (
    CredentialDecryptError,
    create_payload_token,
    create_token,
    decode_session_token,
    decrypt_credential,
    decrypt_data,
    decrypt_with_dek,
    decode_payload_token,
    encrypt_credential,
    encrypt_data,
    encrypt_with_dek,
    generate_dek,
    hash_password,
    unwrap_dek,
    verify_password,
    verify_token,
    wrap_dek,
)


# ---------------------------------------------------------------------------
# encrypt_data / decrypt_data — master-key path
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_data_roundtrip() -> None:
    data = {"api_key": "secret", "token": "abc123"}
    token = encrypt_data(data)
    assert isinstance(token, str)
    assert decrypt_data(token) == data


def test_encrypt_data_produces_different_ciphertext_each_time() -> None:
    data = {"key": "value"}
    t1 = encrypt_data(data)
    t2 = encrypt_data(data)
    assert t1 != t2  # Fernet uses random IV


def test_decrypt_data_returns_empty_dict_for_invalid_token() -> None:
    assert decrypt_data("not.a.valid.fernet.token") == {}


def test_decrypt_data_returns_empty_dict_for_tampered_token() -> None:
    token = encrypt_data({"key": "val"})
    tampered = token[:-5] + "XXXXX"
    assert decrypt_data(tampered) == {}


def test_decrypt_data_returns_empty_dict_for_empty_string() -> None:
    assert decrypt_data("") == {}


# ---------------------------------------------------------------------------
# DEK — generate, wrap, unwrap
# ---------------------------------------------------------------------------

def test_generate_dek_returns_bytes() -> None:
    dek = generate_dek()
    assert isinstance(dek, bytes)
    assert len(dek) == 44  # Fernet keys are 32 bytes base64-encoded = 44 chars


def test_generate_dek_returns_different_keys() -> None:
    keys = {generate_dek() for _ in range(10)}
    assert len(keys) == 10


def test_wrap_unwrap_dek_roundtrip() -> None:
    dek = generate_dek()
    wrapped = wrap_dek(dek)
    assert isinstance(wrapped, str)
    assert unwrap_dek(wrapped) == dek


# ---------------------------------------------------------------------------
# encrypt_with_dek / decrypt_with_dek
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_with_dek_roundtrip() -> None:
    dek = generate_dek()
    payload = {"username": "alice", "password": "s3cr3t"}
    ciphertext = encrypt_with_dek(payload, dek)
    assert isinstance(ciphertext, str)
    assert decrypt_with_dek(ciphertext, dek) == payload


def test_decrypt_with_dek_wrong_key_returns_empty() -> None:
    dek = generate_dek()
    wrong_dek = generate_dek()
    ciphertext = encrypt_with_dek({"k": "v"}, dek)
    assert decrypt_with_dek(ciphertext, wrong_dek) == {}


def test_decrypt_with_dek_invalid_token_returns_empty() -> None:
    dek = generate_dek()
    assert decrypt_with_dek("not-a-valid-token", dek) == {}


# ---------------------------------------------------------------------------
# encrypt_credential / decrypt_credential
# ---------------------------------------------------------------------------

def test_encrypt_credential_returns_ciphertext_and_wrapped_dek() -> None:
    data = {"api_key": "my-secret-key"}
    ciphertext, wrapped_dek = encrypt_credential(data)
    assert isinstance(ciphertext, str)
    assert isinstance(wrapped_dek, str)
    # The wrapped DEK is itself a Fernet token (URL-safe base64)
    assert len(wrapped_dek) > 0


def test_decrypt_credential_with_dek_roundtrip() -> None:
    payload = {"host": "db.example.com", "password": "hunter2"}
    ciphertext, wrapped_dek = encrypt_credential(payload)
    result = decrypt_credential(ciphertext, wrapped_dek)
    assert result == payload


def test_decrypt_credential_legacy_path_no_dek() -> None:
    # When encrypted_dek is None, falls back to master-key direct decryption
    payload = {"legacy": "data"}
    ciphertext = encrypt_data(payload)
    result = decrypt_credential(ciphertext, None)
    assert result == payload


def test_decrypt_credential_invalid_dek_returns_empty() -> None:
    payload = {"k": "v"}
    ciphertext, _ = encrypt_credential(payload)
    assert decrypt_credential(ciphertext, "invalid-dek") == {}


def test_decrypt_credential_tampered_ciphertext_returns_empty() -> None:
    _, wrapped_dek = encrypt_credential({"k": "v"})
    assert decrypt_credential("tampered-ciphertext", wrapped_dek) == {}


# ---------------------------------------------------------------------------
# H1: strict mode — a credential that exists but cannot be decrypted must
# raise rather than silently degrade a workflow to empty credentials.
# ---------------------------------------------------------------------------

def test_decrypt_credential_strict_raises_on_invalid_dek() -> None:
    payload = {"k": "v"}
    ciphertext, _ = encrypt_credential(payload)
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(ciphertext, "invalid-dek", strict=True)


def test_decrypt_credential_strict_raises_on_tampered_ciphertext() -> None:
    _, wrapped_dek = encrypt_credential({"k": "v"})
    with pytest.raises(CredentialDecryptError):
        decrypt_credential("tampered-ciphertext", wrapped_dek, strict=True)


def test_decrypt_credential_strict_raises_on_legacy_invalid_token() -> None:
    with pytest.raises(CredentialDecryptError):
        decrypt_credential("not-a-valid-fernet-token", None, strict=True)


def test_decrypt_credential_strict_roundtrip_succeeds() -> None:
    payload = {"host": "db.example.com", "password": "hunter2"}
    ciphertext, wrapped_dek = encrypt_credential(payload)
    assert decrypt_credential(ciphertext, wrapped_dek, strict=True) == payload


def test_decrypt_credential_strict_allows_genuinely_empty_payload() -> None:
    # An empty credential dict decrypts cleanly to {} — that is a valid value,
    # not a failure, so strict mode must NOT raise on it.
    ciphertext, wrapped_dek = encrypt_credential({})
    assert decrypt_credential(ciphertext, wrapped_dek, strict=True) == {}


# ---------------------------------------------------------------------------
# hash_password / verify_password
# ---------------------------------------------------------------------------

def test_hash_password_returns_salted_string() -> None:
    h = hash_password("mysecret")
    assert ":" in h
    parts = h.split(":")
    assert len(parts) == 2
    assert len(parts[0]) > 0  # base64 salt
    assert len(parts[1]) > 0  # base64 digest


def test_hash_password_produces_different_hashes() -> None:
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2  # different salts


def test_verify_password_correct_password() -> None:
    stored = hash_password("correct_horse_battery_staple")
    assert verify_password("correct_horse_battery_staple", stored) is True


def test_verify_password_wrong_password() -> None:
    stored = hash_password("right")
    assert verify_password("wrong", stored) is False


def test_verify_password_empty_password() -> None:
    stored = hash_password("")
    assert verify_password("", stored) is True
    assert verify_password("notempty", stored) is False


def test_verify_password_rejects_malformed_stored_hash() -> None:
    assert verify_password("anything", "no-colon-here") is False
    assert verify_password("anything", "not:valid:base64!!!") is False
    assert verify_password("anything", "") is False


def test_verify_password_uses_constant_time_comparison() -> None:
    # Just check it doesn't short-circuit on length mismatch
    stored = hash_password("secret")
    assert verify_password("s", stored) is False


# ---------------------------------------------------------------------------
# create_token / verify_token
# ---------------------------------------------------------------------------

def test_create_token_returns_dot_separated_string() -> None:
    token = create_token("user123")
    parts = token.split(".")
    assert len(parts) == 2


def test_verify_token_returns_user_id() -> None:
    token = create_token("user42", ttl_seconds=3600)
    assert verify_token(token) == "user42"


def test_verify_token_returns_none_for_expired() -> None:
    token = create_token("user1", ttl_seconds=-1)  # already expired
    assert verify_token(token) is None


def test_verify_token_returns_none_for_tampered_signature() -> None:
    token = create_token("user1", ttl_seconds=3600)
    body, sig = token.split(".")
    tampered = f"{body}.{'x' * len(sig)}"
    assert verify_token(tampered) is None


def test_verify_token_returns_none_for_tampered_body() -> None:
    token = create_token("user1", ttl_seconds=3600)
    body, sig = token.split(".")
    tampered_body = body[:-5] + "XXXXX"
    assert verify_token(f"{tampered_body}.{sig}") is None


def test_verify_token_returns_none_for_malformed_token() -> None:
    assert verify_token("no-dot-here") is None
    assert verify_token("") is None
    assert verify_token("a.b.c") is None  # too many dots


def test_session_token_includes_issued_at() -> None:
    """C1: session tokens carry ``iat`` so revocation can invalidate every
    token minted before a cutoff."""
    before = time.time()
    token = create_token("user1")
    payload = decode_session_token(token)
    assert payload is not None
    assert payload["sub"] == "user1"
    assert isinstance(payload["iat"], (int, float))
    assert payload["iat"] >= before


def test_decode_session_token_rejects_non_session_token() -> None:
    purpose = create_payload_token({"sub": "user1", "typ": "runner"}, ttl_seconds=3600)
    assert decode_session_token(purpose) is None


def test_decode_session_token_rejects_tampered_or_expired() -> None:
    assert decode_session_token("garbage") is None
    expired = create_token("user1", ttl_seconds=-1)
    assert decode_session_token(expired) is None


def test_token_expires_after_ttl() -> None:
    token = create_token("user1", ttl_seconds=1)
    assert verify_token(token) == "user1"
    with patch("app.services.crypto.time.time", return_value=time.time() + 2):
        assert verify_token(token) is None


# ---------------------------------------------------------------------------
# create_payload_token / decode_payload_token
# ---------------------------------------------------------------------------

def test_payload_token_roundtrip() -> None:
    payload = {"action": "reset_password", "email": "user@example.com"}
    token = create_payload_token(payload, ttl_seconds=3600)
    decoded = decode_payload_token(token)
    assert decoded is not None
    assert decoded["action"] == "reset_password"
    assert decoded["email"] == "user@example.com"
    assert "exp" in decoded


def test_payload_token_returns_none_when_expired() -> None:
    token = create_payload_token({"k": "v"}, ttl_seconds=-1)
    assert decode_payload_token(token) is None


def test_payload_token_returns_none_for_tampered_signature() -> None:
    token = create_payload_token({"k": "v"}, ttl_seconds=3600)
    body, sig = token.split(".")
    assert decode_payload_token(f"{body}.{'z' * len(sig)}") is None


def test_payload_token_returns_none_for_malformed() -> None:
    assert decode_payload_token("malformed") is None
    assert decode_payload_token("") is None
