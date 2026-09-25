"""Cryptographic and encoding nodes."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from typing import Any

from nodyra.sdk import node


def _pad_b64(s: str) -> str:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return s


_ALGORITHMS_COMMON = ["sha256", "sha512", "sha1", "md5"]
_ALGORITHMS_ALL = [*_ALGORITHMS_COMMON, "sha3_256", "sha3_512", "blake2b", "blake2s"]


def _get_digest(algorithm: str, data: bytes) -> hashlib._Hash:
    try:
        if algorithm in ("blake2b", "blake2s"):
            return hashlib.new(algorithm, data, digest_size=64 if algorithm == "blake2b" else 32)
        return hashlib.new(algorithm, data)
    except ValueError as exc:
        raise ValueError(
            f"Unsupported hash algorithm: {algorithm!r}. "
            f"Supported: {', '.join(_ALGORITHMS_ALL)}"
        ) from exc


def _encode(data: bytes, encoding: str) -> str:
    if encoding == "hex":
        return data.hex()
    if encoding == "base64":
        return base64.b64encode(data).decode("ascii")
    raise ValueError(f"Unsupported encoding: {encoding!r}. Use 'hex' or 'base64'.")


@node(
    name="Hash Text",
    id="hash_text",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Text to hash. Falls back to wired input.",
            "multiline": True,
        },
        "algorithm": {
            "description": "Hash algorithm.",
            "choices": _ALGORITHMS_ALL,
            "default": "sha256",
        },
        "encoding": {
            "group": "Options",
            "choices": ["hex", "base64"],
            "default": "hex",
            "description": "Output encoding.",
        },
        "salt": {
            "group": "Options",
            "description": "Optional salt (appended to text before hashing).",
        },
    },
    tool_side_effecting=False,
)
def hash_text(
    input: Any = None,
    text: str = "",
    algorithm: str = "sha256",
    encoding: str = "hex",
    salt: str = "",
) -> dict[str, Any]:
    """Hash text using the specified algorithm."""
    payload = (text or input or "")
    if salt:
        payload += salt
    digest = _get_digest(algorithm, payload.encode("utf-8"))
    return {
        "hash": _encode(digest.digest(), encoding),
        "algorithm": algorithm,
        "encoding": encoding,
    }


@node(
    name="Hash Verify",
    id="hash_verify",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Original text.",
            "multiline": True,
        },
        "hash_value": {
            "description": "Expected hash to verify against.",
        },
        "algorithm": {
            "description": "Hash algorithm.",
            "choices": _ALGORITHMS_ALL,
            "default": "sha256",
        },
        "encoding": {
            "group": "Options",
            "choices": ["hex", "base64"],
            "default": "hex",
        },
        "salt": {
            "group": "Options",
            "description": "Salt used when creating the original hash.",
        },
    },
    tool_side_effecting=False,
)
def hash_verify(
    input: Any = None,
    text: str = "",
    hash_value: str = "",
    algorithm: str = "sha256",
    encoding: str = "hex",
    salt: str = "",
) -> dict[str, Any]:
    """Verify a hash against the original text."""
    if not hash_value:
        raise ValueError("hash_verify: hash_value is required")
    payload = (text or input or "")
    if salt:
        payload += salt
    digest = _get_digest(algorithm, payload.encode("utf-8"))
    computed = _encode(digest.digest(), encoding)
    return {
        "verified": secrets.compare_digest(computed.encode("utf-8"), hash_value.encode("utf-8")),
        "computed_hash": computed,
    }


@node(
    name="HMAC Generate",
    id="hmac_generate",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Message to sign. Falls back to wired input.",
            "multiline": True,
        },
        "secret": {
            "description": "Secret key for HMAC.",
            "placeholder": "your-secret-key",
        },
        "algorithm": {
            "choices": _ALGORITHMS_COMMON,
            "default": "sha256",
            "description": "Hash algorithm.",
        },
        "encoding": {
            "group": "Options",
            "choices": ["hex", "base64"],
            "default": "hex",
        },
    },
    tool_side_effecting=False,
)
def hmac_generate(
    input: Any = None,
    text: str = "",
    secret: str = "",
    algorithm: str = "sha256",
    encoding: str = "hex",
) -> dict[str, Any]:
    """Generate an HMAC signature for a message."""
    if not secret:
        raise ValueError("hmac_generate: secret is required")
    payload = (text or input or "").encode("utf-8")
    key = secret.encode("utf-8")
    h = hmac.new(key, payload, algorithm)
    return {
        "hmac": _encode(h.digest(), encoding),
        "algorithm": algorithm,
        "encoding": encoding,
    }


@node(
    name="Base64 Encode",
    id="base64_encode_tool",
    category="Transform",
    icon="hash",
    params={
        "text": {
            "description": "Text to encode. Falls back to wired input.",
            "multiline": True,
        },
        "urlsafe": {
            "group": "Options",
            "type": bool,
            "default": False,
            "description": "Use URL-safe base64 (replace +/ with -_).",
        },
    },
)
def base64_encode(
    input: Any = None,
    text: str = "",
    urlsafe: bool = False,
) -> dict[str, Any]:
    """Encode UTF-8 text as a base64 string.

    Use when an API expects a base64-wrapped value, such as a Basic auth
    header or an inline file attachment.
    """
    payload = (text or input or "").encode("utf-8")
    if urlsafe:
        encoded = base64.urlsafe_b64encode(payload).decode("ascii")
    else:
        encoded = base64.b64encode(payload).decode("ascii")
    return {"encoded": encoded, "decoded_bytes": len(payload)}


@node(
    name="Base64 Decode",
    id="base64_decode_tool",
    category="Transform",
    icon="hash",
    params={
        "data": {
            "description": "Base64 string to decode. Falls back to wired input.",
            "multiline": True,
        },
        "urlsafe": {
            "group": "Options",
            "type": bool,
            "default": False,
            "description": "Treat as URL-safe base64.",
        },
        "encoding": {
            "group": "Options",
            "default": "utf-8",
            "description": "Output text encoding. Use 'bytes' for raw bytes.",
            "placeholder": "utf-8",
        },
    },
)
def base64_decode(
    input: Any = None,
    data: str = "",
    urlsafe: bool = False,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Decode a base64-encoded string back to UTF-8 text.

    Use for payloads that arrive base64-wrapped, such as webhook bodies or
    embedded file contents. Raises when the input is not valid base64.
    """
    payload = _pad_b64(data or input or "")
    try:
        if urlsafe:
            decoded = base64.urlsafe_b64decode(payload)
        else:
            decoded = base64.b64decode(payload)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"base64_decode: invalid base64 data: {exc}") from exc
    if encoding == "bytes":
        return {"decoded": decoded.hex(), "encoding": "bytes"}
    return {"decoded": decoded.decode(encoding), "encoding": encoding}


@node(
    name="Random Bytes",
    id="random_bytes",
    category="Transform",
    icon="hash",
    params={
        "length": {
            "type": int,
            "default": 16,
            "description": "Number of random bytes.",
        },
        "encoding": {
            "choices": ["hex", "base64"],
            "default": "hex",
            "description": "Output encoding.",
        },
    },
    tool_side_effecting=False,
)
def random_bytes(
    input: Any = None,
    length: int = 16,
    encoding: str = "hex",
) -> dict[str, Any]:
    """Generate cryptographically secure random bytes."""
    n = max(1, min(1024, int(length or 16)))
    data = secrets.token_bytes(n)
    return {
        "data": _encode(data, encoding),
        "encoding": encoding,
        "length": n,
        "entropy_bits": n * 8,
    }


__all__ = [
    "hash_text",
    "hash_verify",
    "hmac_generate",
    "base64_encode",
    "base64_decode",
    "random_bytes",
]
