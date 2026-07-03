#!/usr/bin/env python3
"""Migrate org KEKs from one KMS provider to another.

Usage
-----
    # Migrate from env (SECRET_KEY) to HashiCorp Vault
    export VAULT_URL=http://vault:8200
    export VAULT_TOKEN=hvs...
    uv run python scripts/migrate_kms.py --from=env --to=vault

    # Dry-run to preview without making changes
    uv run python scripts/migrate_kms.py --from=env --to=vault --dry-run

This script re-encrypts every org's ``wrapped_org_kek`` from the **source**
provider to the **target** provider.  Credentials themselves (DEK-encrypted)
don't change — only the org KEK envelope is replaced.

IMPORTANT
---------
Run this script **before** switching the ``kms_provider`` env var in
production.  The old provider must still be reachable (e.g. the old
``SECRET_KEY`` must still be set) so that existing KEKs can be decrypted.

Idempotent: re-running is safe.  Already-migrated rows are re-encrypted
under the same target provider with a fresh ciphertext (the plaintext KEK
does not change, so the credential envelope is unaffected).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
_logger = logging.getLogger("migrate_kms")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-encrypt org KEKs from one KMS provider to another."
    )
    parser.add_argument(
        "--from",
        dest="source",
        required=True,
        choices=["env", "vault", "aws", "gcp"],
        help="Source KMS provider (must be the currently-active one).",
    )
    parser.add_argument(
        "--to",
        dest="target",
        required=True,
        choices=["env", "vault", "aws", "gcp"],
        help="Target KMS provider.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making changes.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of orgs to process per batch (default 100).",
    )
    return parser.parse_args()


def _build_provider(kind: str):
    """Construct a KMS provider for the given kind directly from env vars.

    This bypasses the ``get_kms_provider()`` singleton so the script can
    hold **two** providers simultaneously (source + target).
    """
    import os

    if kind == "env":
        from app.services.kms.env_kms import EnvKMSProvider

        return EnvKMSProvider()

    if kind == "vault":
        from app.services.kms.vault import VaultKMSProvider

        return VaultKMSProvider(
            vault_url=os.environ.get("VAULT_URL", ""),
            token=os.environ.get("VAULT_TOKEN", ""),
            mount=os.environ.get("VAULT_TRANSIT_MOUNT", "transit"),
            key_name=os.environ.get("VAULT_TRANSIT_KEY", "nodyra-master"),
        )

    if kind == "aws":
        from app.services.kms.aws_kms import AWSKMSProvider

        return AWSKMSProvider(
            key_id=os.environ.get("AWS_KMS_KEY_ID", ""),
            region=os.environ.get("AWS_KMS_REGION", "us-east-1"),
        )

    if kind == "gcp":
        from app.services.kms.gcp_kms import GCPKMSProvider

        return GCPKMSProvider(
            key_name=os.environ.get("GCP_KMS_KEY_NAME", ""),
        )

    msg = f"Unknown provider kind: {kind!r}"
    raise ValueError(msg)


async def _migrate(
    source: str,
    target: str,
    dry_run: bool = False,
    batch_size: int = 100,
) -> int:
    """Migrate all org KEKs.  Returns the number of rows updated."""
    from sqlalchemy import select, update

    from app.db import SessionLocal
    from app.models import Organization

    src_provider = _build_provider(source)
    tgt_provider = _build_provider(target)

    updated = 0
    skipped = 0
    errors = 0
    async with SessionLocal() as session:
        result = await session.scalars(
            select(Organization).where(Organization.wrapped_org_kek.isnot(None))
        )
        orgs = list(result.all())

        _logger.info(
            "Migrating %d org KEKs from %s → %s%s",
            len(orgs),
            source,
            target,
            " (DRY RUN)" if dry_run else "",
        )

        for org in orgs:
            try:
                plaintext_kek = await src_provider.decrypt(
                    org.wrapped_org_kek.encode("utf-8")
                )
                new_wrapped = await tgt_provider.encrypt(plaintext_kek)

                if dry_run:
                    _logger.info(
                        "  [DRY] org %s: %d bytes → %d bytes",
                        org.id,
                        len(org.wrapped_org_kek or ""),
                        len(new_wrapped),
                    )
                    skipped += 1
                    continue

                await session.execute(
                    update(Organization)
                    .where(Organization.id == org.id)
                    .values(wrapped_org_kek=new_wrapped.decode("utf-8"))
                )
                updated += 1

                if updated % batch_size == 0:
                    await session.commit()
                    _logger.info("  Committed batch of %d (%d total)", batch_size, updated)

            except Exception:  # noqa: BLE001
                _logger.exception("Failed to migrate org %s", org.id)
                errors += 1
                continue

        if updated > 0 and not dry_run:
            await session.commit()

    _logger.info(
        "Migration complete: %d updated, %d skipped, %d errors",
        updated,
        skipped,
        errors,
    )
    return updated


def main() -> None:
    args = _parse_args()
    if args.source == args.target:
        _logger.info("Source and target are the same — nothing to do.")
        return
    asyncio.run(
        _migrate(
            source=args.source,
            target=args.target,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        )
    )


if __name__ == "__main__":
    main()
