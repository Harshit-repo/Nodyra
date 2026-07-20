"""Validation and signature policy for community registry packages."""

from __future__ import annotations

import base64
import ipaddress
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.config import settings

_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_VERSION = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._+-]{0,126}[A-Za-z0-9])?$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_LIFECYCLES = {"active", "deprecated", "quarantined", "revoked"}
_SIGNED_FIELDS = (
    "id",
    "name",
    "version",
    "pypi_package",
    "distribution_url",
    "distribution_sha256",
    "publisher_key_id",
    "permissions",
    "compatibility",
    "lifecycle",
)


@dataclass(frozen=True)
class RegistryTrust:
    status: str
    installable: bool
    signed: bool
    publisher_key_id: str | None
    immutable: bool
    reason: str
    locked_spec: str | None = None


def _decode_signature(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _load_public_key(value: str) -> Ed25519PublicKey | None:
    try:
        if "BEGIN PUBLIC KEY" in value:
            key = serialization.load_pem_public_key(value.encode())
        else:
            raw = _decode_signature(value)
            key = Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError):
        return None
    return key if isinstance(key, Ed25519PublicKey) else None


def _trusted_publishers() -> dict[str, Ed25519PublicKey]:
    try:
        configured = json.loads(settings.registry_trusted_publishers or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(configured, dict):
        return {}
    trusted: dict[str, Ed25519PublicKey] = {}
    for key_id, raw in configured.items():
        if not isinstance(key_id, str) or not isinstance(raw, str):
            continue
        key = _load_public_key(raw)
        if key is not None:
            trusted[key_id] = key
    return trusted


def canonical_package_manifest(package: dict[str, Any]) -> bytes:
    manifest = {field: package.get(field) for field in _SIGNED_FIELDS}
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def assess_registry_package(package: dict[str, Any]) -> RegistryTrust:
    """Assess a package without trusting status fields supplied by the index."""
    package_id = package.get("id")
    version = package.get("version")
    distribution_url = package.get("distribution_url")
    digest = str(package.get("distribution_sha256") or "").lower()
    package_name = package.get("pypi_package")
    lifecycle = package.get("lifecycle", "active")
    publisher_key_id = package.get("publisher_key_id")
    signature = package.get("signature")
    permissions = package.get("permissions")
    compatibility = package.get("compatibility")

    if lifecycle not in _LIFECYCLES:
        return RegistryTrust("invalid", False, False, None, False, "Unknown lifecycle state")
    if lifecycle in {"quarantined", "revoked"}:
        return RegistryTrust(lifecycle, False, bool(signature), publisher_key_id, False, f"Package is {lifecycle}")
    if not isinstance(package_id, str) or not _PACKAGE_NAME.fullmatch(package_id):
        return RegistryTrust("invalid", False, False, publisher_key_id, False, "Invalid package ID")
    if not isinstance(package_name, str) or not _PACKAGE_NAME.fullmatch(package_name):
        return RegistryTrust("invalid", False, False, publisher_key_id, False, "Invalid PyPI package name")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        return RegistryTrust("invalid", False, False, publisher_key_id, False, "An immutable version is required")
    if not isinstance(distribution_url, str) or not distribution_url.startswith("https://"):
        return RegistryTrust("invalid", False, False, publisher_key_id, False, "An HTTPS distribution URL is required")
    parsed_url = urlsplit(distribution_url)
    if not parsed_url.hostname or parsed_url.username or parsed_url.password or parsed_url.fragment:
        return RegistryTrust("invalid", False, False, publisher_key_id, False, "Distribution URL authority is invalid")
    try:
        address = ipaddress.ip_address(parsed_url.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return RegistryTrust("invalid", False, False, publisher_key_id, False, "Distribution URL cannot use a private IP literal")
    if not _SHA256.fullmatch(digest):
        return RegistryTrust("invalid", False, False, publisher_key_id, False, "A lowercase SHA-256 digest is required")
    if not isinstance(permissions, dict) or not isinstance(compatibility, dict):
        return RegistryTrust("invalid", False, False, publisher_key_id, False, "Permissions and compatibility manifests are required")

    locked_spec = f"{package_name} @ {distribution_url}#sha256={digest}"
    trusted_key = _trusted_publishers().get(str(publisher_key_id or ""))
    if trusted_key is None or not isinstance(signature, str):
        allow = settings.runtime_mode != "production" and settings.registry_allow_unverified_install
        return RegistryTrust(
            "unverified",
            allow,
            False,
            str(publisher_key_id) if publisher_key_id else None,
            True,
            "Publisher signature is absent or the publisher key is not trusted",
            locked_spec if allow else None,
        )
    try:
        trusted_key.verify(_decode_signature(signature), canonical_package_manifest(package))
    except (InvalidSignature, ValueError):
        return RegistryTrust("invalid", False, True, str(publisher_key_id), True, "Publisher signature is invalid")
    return RegistryTrust(
        "deprecated" if lifecycle == "deprecated" else "verified",
        lifecycle == "active",
        True,
        str(publisher_key_id),
        True,
        "Verified publisher signature and SHA-256-pinned distribution",
        locked_spec if lifecycle == "active" else None,
    )


def package_with_trust(package: dict[str, Any]) -> dict[str, Any]:
    return {**package, "trust": asdict(assess_registry_package(package))}
