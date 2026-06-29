#!/usr/bin/env python3
"""Migrate org KEKs from one KMS provider to another.

Usage
-----
    uv run python scripts/migrate_kms.py --from=env --to=vault

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
import sys

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


async def _migrate(
    source_provider: str,
    target_provider: str,
    dry_run: bool = False,
    batch_size: int = 100,
) -> int:
    """Migrate all org KEKs.  Returns the number of rows updated."""
    # Import here so the script can be run without the full app context.
    import os

    # Force the source provider so we can decrypt existing KEKs.
    os.environ["KMS_PROVIDER"] = source_provider
    # Re-import config to pick up the overridden env var.
    # In practice the caller sets the env before running the script.
    from app.config import settings

    # Validate that required config is present for both providers.
    if source_provider != "env":
        _logger.error("Source provider %s is not yet supported as source; only env is supported.", source_provider)
        sys.exit(1)

    from app.db import SessionLocal
    from app.models import Organization
    from app.services.kms import get_kms_provider, invalidate_kms_cache
    from sqlalchemy import select, update

    # Build source and target providers.
    invalidate_kms_cache()
    src_provider = get_kms_provider()

    os.environ["KMS_PROVIDER"] = target_provider
    # Re-initialize settings to pick up the new provider env.
    settings.__init__(_env_file=None)  # type: ignore[misc]
    invalidate_kms_cache()
    tgt_provider = get_kms_provider()

    updated = 0
    skipped = 0
    errors = 0
    async with SessionLocal() as session:
        # Fetch all orgs that have a wrapped KEK.
        result = await session.scalars(
            select(Organization).where(Organization.wrapped_org_kek.isnot(None))
        )
        orgs = list(result.all())

        _logger.info(
            "Migrating %d org KEKs from %s → %s%s",
            len(orgs),
            source_provider,
            target_provider,
            " (DRY RUN)" if dry_run else "",
        )

        for org in orgs:
            try:
                # Decrypt with source provider.
                plaintext_kek = await src_provider.decrypt(
                    org.wrapped_org_kek.encode("utf-8")
                )
                # Re-encrypt with target provider.
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

        # Final commit.
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
    if args.source != "env":
        _logger.error(
            "Only --from=env is supported in V1. "
            "For other source providers, the old provider must still be "
            "reachable and configured at the env level."
        )
        sys.exit(1)
    asyncio.run(
        _migrate(
            source_provider=args.source,
            target_provider=args.target,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        )
    )


if __name__ == "__main__":
    main()
