"""Customer-side licence renewal.

A licence key expires. Before this, renewing meant a human pasting a new key
into Settings before the old one lapsed — and if nobody did, a paying customer's
production instance quietly dropped to Community mid-run: sandbox caps, seat
limits, features gone.

This loop closes that. It wakes periodically, and when the installed key is
inside its refresh window it asks the vendor's licence server for a new one and
stores it. Everything about it is opt-in: with no ``license_server_url`` the
loop never starts, and an air-gapped deployment keeps verifying offline exactly
as it always did.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.db import SessionLocal
from app.services.license_issuer import should_refresh
from app.services.licensing import current_license, invalidate_license_cache, verify_license_key

logger = logging.getLogger(__name__)

# Checked once an hour. The refresh window is measured in days, so polling any
# faster only adds load; slower risks missing the window on a short-lived pod.
POLL_INTERVAL_SECONDS = 3600.0

_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def refresh_enabled() -> bool:
    return bool(settings.license_server_url and settings.license_refresh_token)


async def _store_key(key: str) -> None:
    """Persist a renewed key to ``system_settings``.

    Written to the database rather than the environment because that is the
    layer this process can actually change, and because it survives a restart.
    A deployment that pins ``NODYRA_LICENSE_KEY`` in its environment keeps
    winning — that precedence is checked before the loop ever runs.
    """
    from app.models import SystemSetting

    async with SessionLocal() as session:
        row = await session.get(SystemSetting, "singleton")
        if row is None:
            row = SystemSetting(id="singleton")
            session.add(row)
        row.license_key = key
        await session.commit()
    invalidate_license_cache()


async def refresh_once() -> bool:
    """Renew the licence if it is close enough to expiry. True when renewed.

    Never raises: a licence server that is down, slow or wrong must not take
    down the instance it serves. The installed key stays valid until its own
    expiry, which is why issuance grants a window comfortably longer than the
    billing period.
    """
    if not refresh_enabled():
        return False
    if settings.license_key:
        # An environment-pinned key is the operator's explicit choice and takes
        # precedence over anything stored; renewing the DB row would be a no-op
        # the operator could not see.
        return False

    lic = await current_license()
    if not should_refresh(lic.expires_at):
        return False

    url = settings.license_server_url.rstrip("/") + "/billing/license"
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname or (
        parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
    ):
        logger.error("licence refresh: license_server_url requires HTTPS without embedded credentials (loopback HTTP is allowed for testing)")
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                url, json={"refresh_token": settings.license_refresh_token}
            )
    except httpx.HTTPError as exc:
        logger.warning("licence refresh: could not reach %s: %s", url, exc)
        return False

    if response.status_code == 402:
        # The subscription is no longer entitled. Log loudly and leave the
        # current key alone: it lapses on its own schedule, which gives an
        # operator days to notice rather than cutting them off now.
        logger.error(
            "licence refresh: the subscription is no longer active. The current "
            "licence remains valid until it expires; renew billing to restore it."
        )
        return False
    if response.status_code >= 400:
        logger.warning(
            "licence refresh: server returned HTTP %s",
            response.status_code,
        )
        return False

    try:
        payload = response.json()
    except ValueError:
        logger.warning("licence refresh: response was not JSON")
        return False
    key = payload.get("license_key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not key or len(key) > 16384:
        logger.warning("licence refresh: response carried no licence key")
        return False

    renewed = verify_license_key(key)
    if not renewed.valid or renewed.edition.value == "community":
        # A key that verifies to Community means the server signed something
        # this build cannot honour — a key rotation, most likely. Say so
        # plainly rather than leaving an operator to discover it as missing
        # features.
        logger.error(
            "licence refresh: the renewed key did not verify against this "
            "build's public key. Check for a signing-key rotation."
        )
        return False

    await _store_key(key)

    logger.info(
        "licence refreshed: edition=%s expires_at=%s",
        renewed.edition.value,
        renewed.expires_at,
    )
    return True


async def license_refresh_loop() -> None:  # pragma: no cover - background task
    """Background task: renew the licence before it lapses."""
    if not refresh_enabled():
        logger.debug("licence refresh disabled (no license_server_url)")
        return
    logger.info("licence refresh loop started (window=%sd)", settings.license_refresh_window_days)
    while True:
        try:
            await refresh_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let one tick kill the loop
            logger.exception("licence refresh tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
