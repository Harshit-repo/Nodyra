"""Offline license-key minting tool. Run only by the vendor.

Generate a keypair once:
    python -m tools.mint_license keygen --out license_key
  -> writes license_key (private, KEEP SECRET) + license_key.pub (PEM public).
  Paste the .pub contents into _BAKED_PUBLIC_KEY_PEM in app/services/licensing.py.

Mint a key:
    python -m tools.mint_license sign --private license_key --tier pro \
        --customer "Acme Inc" --seats 10 --days 365
    python -m tools.mint_license sign --private license_key --tier enterprise \
        --customer "Big Co" --feature sso --feature audit_logs
"""
from __future__ import annotations

import argparse
import base64
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _keygen(out: str) -> None:
    priv = Ed25519PrivateKey.generate()
    with open(out, "wb") as f:
        f.write(
            priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    with open(out + ".pub", "wb") as f:
        f.write(
            priv.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
    print(f"wrote {out} (private — keep secret) and {out}.pub (public — bake into app)")


def _sign(args: argparse.Namespace) -> None:
    with open(args.private, "rb") as f:
        priv = serialization.load_pem_private_key(f.read(), password=None)
    payload: dict = {
        "tier": args.tier,
        "customer": args.customer,
        "issued_at": int(time.time()),
    }
    if args.seats is not None:
        payload["seats"] = args.seats
    if args.days:
        payload["expires_at"] = int(time.time()) + args.days * 86400
    if args.feature:
        payload["features"] = args.feature
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(priv.sign(body)).rstrip(b"=")
    print(f"{body.decode()}.{sig.decode()}")


def _nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def main() -> None:
    p = argparse.ArgumentParser(description="Nodyra license minting (vendor-only).")
    sub = p.add_subparsers(dest="cmd", required=True)

    kg = sub.add_parser("keygen", help="generate a signing keypair")
    kg.add_argument("--out", default="license_key")

    sg = sub.add_parser("sign", help="mint a signed license key")
    sg.add_argument("--private", required=True, help="path to the PEM private key")
    sg.add_argument("--tier", required=True, choices=["pro", "enterprise"])
    sg.add_argument("--customer", required=True)
    sg.add_argument("--seats", type=_nonnegative, default=None, help="seat limit (omit for edition default; 0 = unlimited)")
    sg.add_argument("--days", type=_nonnegative, default=0, help="validity in days (0 = perpetual)")
    sg.add_argument("--feature", action="append", default=[], help="extra feature grant")

    args = p.parse_args()
    if args.cmd == "keygen":
        _keygen(args.out)
    else:
        _sign(args)


if __name__ == "__main__":
    main()
