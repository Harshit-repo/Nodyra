"""Cryptography and security automation nodes.

All optional security/network packages are imported lazily in the nodes that
need them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodyra.artifacts import is_artifact_ref, read_bytes, read_text, write_bytes, write_text
from nodyra.sdk import node

SECURITY_CATEGORY = "Security"


def _bytes_from_input(value: Any, *, encoding: str = "utf-8") -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if is_artifact_ref(value):
        return read_bytes(value)
    if isinstance(value, dict) and "text" in value:
        return str(value["text"]).encode(encoding)
    return str(value).encode(encoding)


def _text_from_input(value: Any, *, encoding: str = "utf-8") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if is_artifact_ref(value):
        try:
            return read_text(value)
        except Exception:  # noqa: BLE001
            return read_bytes(value).decode(encoding, errors="replace")
    return _bytes_from_input(value, encoding=encoding).decode(encoding, errors="replace")


def _ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


@node(
    name="Certificate Inspect",
    id="certificate_inspect",
    category=SECURITY_CATEGORY,
    icon="shield",
    requirements=["cryptography>=42.0"],
    params={
        "host": {
            "description": (
                "Optional TLS host to inspect. "
                "If blank, reads a PEM/DER cert from input."
            ),
        },
        "port": {"description": "TLS port when host is set."},
        "timeout_seconds": {"description": "Network timeout for host inspection."},
    },
)
def certificate_inspect(
    input: Any = None,
    host: str = "",
    port: int = 443,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Inspect a PEM/DER certificate or fetch one from a TLS host."""
    try:
        from cryptography import x509  # type: ignore[import-not-found]
        from cryptography.hazmat.backends import default_backend  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Certificate Inspect requires cryptography>=42.0 in the workflow environment."
        ) from exc

    if host.strip():
        context = ssl.create_default_context()
        with socket.create_connection(
            (host.strip(), int(port or 443)), timeout=float(timeout_seconds or 5)
        ) as sock:
            with context.wrap_socket(sock, server_hostname=host.strip()) as tls:
                cert_bytes = tls.getpeercert(binary_form=True)
        cert = x509.load_der_x509_certificate(cert_bytes, default_backend())
        source = f"{host.strip()}:{int(port or 443)}"
    else:
        raw = _bytes_from_input(input)
        if not raw:
            raise ValueError("Provide a certificate artifact/string or set host.")
        if b"-----BEGIN CERTIFICATE" in raw:
            cert = x509.load_pem_x509_certificate(raw, default_backend())
        else:
            cert = x509.load_der_x509_certificate(raw, default_backend())
        source = "input"

    try:
        sans = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
    except Exception:  # noqa: BLE001
        sans = []

    now = datetime.now(tz=UTC)
    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc
    days_remaining = (not_after - now).days
    return {
        "source": source,
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "serial_number": str(cert.serial_number),
        "not_before": _ts(not_before),
        "not_after": _ts(not_after),
        "days_remaining": days_remaining,
        "is_expired": now > not_after,
        "dns_names": sans,
        "signature_hash_algorithm": (
            cert.signature_hash_algorithm.name if cert.signature_hash_algorithm else None
        ),
    }


@node(
    name="RSA Sign / Verify",
    id="rsa_sign_verify",
    category=SECURITY_CATEGORY,
    icon="key-round",
    requirements=["cryptography>=42.0"],
    params={
        "mode": {"choices": ["sign", "verify"]},
        "private_key_pem": {
            "description": "PEM private key for signing.",
            "multiline": True,
        },
        "public_key_pem": {
            "description": "PEM public key for verification.",
            "multiline": True,
        },
        "signature_b64": {"description": "Base64 signature for verify mode."},
        "hash_algorithm": {"choices": ["sha256", "sha384", "sha512"]},
    },
)
def rsa_sign_verify(
    input: Any = None,
    mode: str = "verify",
    private_key_pem: str = "",
    public_key_pem: str = "",
    signature_b64: str = "",
    hash_algorithm: str = "sha256",
) -> dict[str, Any]:
    """Sign or verify data using RSA-PSS."""
    try:
        from cryptography.exceptions import InvalidSignature  # type: ignore[import-not-found]
        from cryptography.hazmat.primitives import (  # type: ignore[import-not-found]
            hashes,
            serialization,
        )
        from cryptography.hazmat.primitives.asymmetric import (
            padding,  # type: ignore[import-not-found]
        )
    except ImportError as exc:
        raise RuntimeError(
            "RSA Sign / Verify requires cryptography>=42.0 in the workflow environment."
        ) from exc

    data = _bytes_from_input(input)
    if not data:
        raise ValueError("input data is required.")
    hash_obj = {
        "sha384": hashes.SHA384(),
        "sha512": hashes.SHA512(),
    }.get(hash_algorithm, hashes.SHA256())
    pss = padding.PSS(mgf=padding.MGF1(hash_obj), salt_length=padding.PSS.MAX_LENGTH)

    if mode == "sign":
        if not private_key_pem.strip():
            raise ValueError("private_key_pem is required for sign mode.")
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
        signature = private_key.sign(data, pss, hash_obj)
        return {
            "signature_b64": base64.b64encode(signature).decode("ascii"),
            "hash_algorithm": hash_algorithm,
            "bytes_signed": len(data),
        }

    if not public_key_pem.strip() or not signature_b64.strip():
        raise ValueError("public_key_pem and signature_b64 are required for verify mode.")
    public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    try:
        public_key.verify(base64.b64decode(signature_b64), data, pss, hash_obj)
        valid = True
        error = None
    except InvalidSignature:
        valid = False
        error = "invalid signature"
    return {"valid": valid, "error": error, "hash_algorithm": hash_algorithm}


@node(
    name="PGP Encrypt / Decrypt",
    id="pgp_encrypt_decrypt",
    category=SECURITY_CATEGORY,
    icon="file-key",
    requirements=["python-gnupg>=0.5"],
    params={
        "mode": {"choices": ["encrypt", "decrypt"]},
        "recipient": {"description": "Recipient key id or email for encrypt mode."},
        "passphrase": {"description": "Passphrase for decrypt mode."},
        "gnupg_home": {"description": "Optional GnuPG home directory."},
    },
)
def pgp_encrypt_decrypt(
    input: Any = None,
    mode: str = "encrypt",
    recipient: str = "",
    passphrase: str = "",
    gnupg_home: str = "",
) -> dict[str, Any]:
    """Encrypt/decrypt text using a local GnuPG keyring."""
    try:
        import gnupg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "PGP Encrypt / Decrypt requires python-gnupg>=0.5 and a local gpg binary."
        ) from exc

    gpg = gnupg.GPG(gnupghome=gnupg_home or None)
    text = _text_from_input(input)
    if not text:
        raise ValueError("input text is required.")

    if mode == "encrypt":
        if not recipient.strip():
            raise ValueError("recipient is required for encrypt mode.")
        encrypted = gpg.encrypt(text, recipients=[recipient.strip()])
        if not encrypted.ok:
            raise RuntimeError(encrypted.status or "PGP encryption failed.")
        return {"text": str(encrypted), "status": encrypted.status}

    decrypted = gpg.decrypt(text, passphrase=passphrase or None)
    if not decrypted.ok:
        raise RuntimeError(decrypted.status or "PGP decryption failed.")
    return {"text": str(decrypted), "status": decrypted.status}


@node(
    name="JWT Sign / Verify",
    id="jwt_sign_verify",
    category=SECURITY_CATEGORY,
    icon="fingerprint",
    requirements=["PyJWT>=2.8"],
    params={
        "mode": {"choices": ["sign", "verify", "decode"]},
        "payload_json": {
            "description": "Payload JSON for sign mode. Falls back to dict input.",
            "multiline": True,
        },
        "secret": {"description": "HMAC secret or PEM key."},
        "algorithm": {"description": "JWT algorithm such as HS256, RS256."},
        "token": {"description": "JWT string. Falls back to input in verify/decode modes."},
        "verify_exp": {"description": "Verify exp claim in verify mode."},
    },
)
def jwt_sign_verify(
    input: Any = None,
    mode: str = "verify",
    payload_json: str = "{}",
    secret: str = "",
    algorithm: str = "HS256",
    token: str = "",
    verify_exp: bool = True,
) -> dict[str, Any]:
    """Sign, verify, or decode a JSON Web Token."""
    try:
        import jwt  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("JWT Sign / Verify requires PyJWT>=2.8.") from exc

    if mode == "sign":
        if isinstance(input, dict):
            payload = input
        else:
            payload = json.loads(payload_json or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object.")
        if not secret:
            raise ValueError("secret is required for sign mode.")
        return {"token": jwt.encode(payload, secret, algorithm=algorithm)}

    jwt_token = token or _text_from_input(input)
    if not jwt_token:
        raise ValueError("token or input is required.")
    if mode == "decode":
        payload = jwt.decode(jwt_token, options={"verify_signature": False})
        return {"payload": payload, "verified": False}
    if not secret:
        raise ValueError("secret is required for verify mode.")
    try:
        payload = jwt.decode(
            jwt_token,
            secret,
            algorithms=[algorithm],
            options={"verify_exp": bool(verify_exp)},
        )
        return {"valid": True, "payload": payload}
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "error": str(exc), "payload": None}


@node(
    name="LDAP Query",
    id="ldap_query",
    category=SECURITY_CATEGORY,
    icon="network",
    requirements=["ldap3>=2.9"],
    params={
        "server_uri": {"description": "LDAP server URI, e.g. ldaps://ldap.example.com."},
        "bind_dn": {"description": "Bind DN."},
        "password": {"description": "Bind password."},
        "base_dn": {"description": "Search base DN."},
        "search_filter": {"description": "LDAP search filter."},
        "attributes": {"description": "Comma-separated attributes to return."},
        "use_ssl": {"description": "Use SSL/TLS."},
    },
)
def ldap_query(
    input: Any = None,
    server_uri: str = "",
    bind_dn: str = "",
    password: str = "",
    base_dn: str = "",
    search_filter: str = "(objectClass=*)",
    attributes: str = "*",
    use_ssl: bool = True,
) -> dict[str, Any]:
    """Run an LDAP search and return entries."""
    try:
        from ldap3 import ALL, Connection, Server  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("LDAP Query requires ldap3>=2.9.") from exc

    if not server_uri or not base_dn:
        raise ValueError("server_uri and base_dn are required.")
    attrs = [attr.strip() for attr in attributes.split(",") if attr.strip()] or ["*"]
    server = Server(server_uri, get_info=ALL, use_ssl=use_ssl)
    conn = Connection(server, user=bind_dn or None, password=password or None, auto_bind=True)
    try:
        conn.search(base_dn, search_filter or "(objectClass=*)", attributes=attrs)
        entries = [json.loads(entry.entry_to_json()) for entry in conn.entries]
    finally:
        conn.unbind()
    return {"entries": entries, "entry_count": len(entries)}


@node(
    name="SFTP Transfer",
    id="sftp_transfer",
    category=SECURITY_CATEGORY,
    icon="folder-sync",
    requirements=["paramiko>=3.4"],
    params={
        "mode": {"choices": ["list", "upload", "download"]},
        "host": {"description": "SFTP host."},
        "port": {"description": "SFTP port."},
        "username": {"description": "Username."},
        "password": {"description": "Password. Use credentials in production workflows."},
        "remote_path": {"description": "Remote file or directory path."},
        "filename": {"description": "Artifact filename for download."},
    },
)
def sftp_transfer(
    input: Any = None,
    mode: str = "list",
    host: str = "",
    port: int = 22,
    username: str = "",
    password: str = "",
    remote_path: str = ".",
    filename: str = "download.bin",
) -> dict[str, Any]:
    """List, upload, or download via SFTP."""
    try:
        import paramiko  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("SFTP Transfer requires paramiko>=3.4.") from exc
    if not host or not username:
        raise ValueError("host and username are required.")

    transport = paramiko.Transport((host, int(port or 22)))
    transport.connect(username=username, password=password or None)
    sftp = paramiko.SFTPClient.from_transport(transport)
    try:
        if mode == "list":
            entries = []
            for attr in sftp.listdir_attr(remote_path or "."):
                entries.append(
                    {
                        "filename": attr.filename,
                        "size": attr.st_size,
                        "mtime": attr.st_mtime,
                        "mode": attr.st_mode,
                    }
                )
            return {"entries": entries, "entry_count": len(entries)}
        if mode == "upload":
            data = _bytes_from_input(input)
            if not data:
                raise ValueError("input artifact/text is required for upload mode.")
            with sftp.file(remote_path, "wb") as fh:
                fh.write(data)
            return {"uploaded": True, "remote_path": remote_path, "size_bytes": len(data)}

        with sftp.file(remote_path, "rb") as fh:
            data = fh.read()
        artifact = write_bytes(
            data,
            name=filename or Path(remote_path).name or "download.bin",
            content_type="application/octet-stream",
            kind="sftp_download",
        )
        return {"artifact": artifact, "remote_path": remote_path, "size_bytes": len(data)}
    finally:
        sftp.close()
        transport.close()


@node(
    name="Password Strength Check",
    id="password_strength_check",
    category=SECURITY_CATEGORY,
    icon="shield-check",
    params={
        "password": {"description": "Password to evaluate. Falls back to input string."},
        "min_length": {"description": "Minimum acceptable length."},
    },
)
def password_strength_check(
    input: Any = None,
    password: str = "",
    min_length: int = 12,
) -> dict[str, Any]:
    """Score password strength without sending the password anywhere."""
    pwd = password or _text_from_input(input)
    if not pwd:
        raise ValueError("password or input is required.")
    checks = {
        "min_length": len(pwd) >= int(min_length or 12),
        "has_lower": any(ch.islower() for ch in pwd),
        "has_upper": any(ch.isupper() for ch in pwd),
        "has_digit": any(ch.isdigit() for ch in pwd),
        "has_symbol": any(not ch.isalnum() for ch in pwd),
    }
    score = sum(1 for ok in checks.values() if ok)
    entropy_hint = len(set(pwd)) * len(pwd)
    if len(pwd) >= 16:
        score += 1
    strength = "weak" if score <= 2 else "medium" if score <= 4 else "strong"
    return {
        "strength": strength,
        "score": score,
        "checks": checks,
        "length": len(pwd),
        "entropy_hint": entropy_hint,
        "passed": strength != "weak",
    }


@node(
    name="Network Port Probe",
    id="network_port_probe",
    category=SECURITY_CATEGORY,
    icon="network",
    outputs=["open", "closed"],
    params={
        "host": {"description": "Host to probe."},
        "port": {"description": "TCP port."},
        "timeout_seconds": {"description": "Connection timeout."},
    },
)
def network_port_probe(
    input: Any = None,
    host: str = "",
    port: int = 443,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    """Probe whether a TCP port is reachable."""
    target = host or _text_from_input(input)
    if not target:
        raise ValueError("host is required.")
    started = datetime.now(tz=UTC)
    try:
        with socket.create_connection(
            (target, int(port or 443)), timeout=float(timeout_seconds or 3)
        ):
            ok = True
            error = None
    except OSError as exc:
        ok = False
        error = str(exc)
    elapsed_ms = (datetime.now(tz=UTC) - started).total_seconds() * 1000
    result = {
        "host": target,
        "port": int(port or 443),
        "open": ok,
        "error": error,
        "elapsed_ms": round(elapsed_ms, 2),
    }
    return {"open": result} if ok else {"closed": result}


@node(
    name="File Integrity Manifest",
    id="file_integrity_manifest",
    category=SECURITY_CATEGORY,
    icon="file-check",
    params={
        "algorithm": {"choices": ["sha256", "sha384", "sha512", "md5"]},
        "filename": {"description": "Manifest artifact filename."},
    },
)
def file_integrity_manifest(
    input: Any = None,
    algorithm: str = "sha256",
    filename: str = "integrity-manifest.json",
) -> dict[str, Any]:
    """Hash one or more artifacts/values and emit an integrity manifest artifact."""
    algo = (algorithm or "sha256").lower()
    if algo not in hashlib.algorithms_available:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    items = input if isinstance(input, list) else [input]
    manifest: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        data = _bytes_from_input(item)
        if not data:
            continue
        digest = hashlib.new(algo, data).hexdigest()
        manifest.append(
            {
                "index": index,
                "name": item.get("name") if isinstance(item, dict) else None,
                "algorithm": algo,
                "digest": digest,
                "size_bytes": len(data),
                "is_artifact": is_artifact_ref(item),
            }
        )
    if not manifest:
        raise ValueError("input must contain at least one artifact/string/bytes value.")

    payload = {
        "created_at": datetime.now(tz=UTC).isoformat(),
        "algorithm": algo,
        "files": manifest,
    }
    artifact = write_text(
        json.dumps(payload, indent=2),
        filename or "integrity-manifest.json",
        "application/json",
        metadata={"kind": "file_integrity_manifest"},
    )
    return {"manifest": payload, "artifact": artifact, "file_count": len(manifest)}
