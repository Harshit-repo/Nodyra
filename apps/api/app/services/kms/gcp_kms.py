"""GCPKMSProvider — Google Cloud Key Management Service.

Uses the ``google-cloud-kms`` library.  Application Default Credentials (ADC)
are picked up from the environment (``GOOGLE_APPLICATION_CREDENTIALS`` env var,
GCE metadata server, or Workload Identity).  No explicit GCP auth config is
needed in Nodyra settings.

The key name must point to a symmetric ``CryptoKey`` (resource path format
``projects/*/locations/*/keyRings/*/cryptoKeys/*``).  Do NOT include a version
suffix — let GCP manage automatic key rotation.

The provider base64-encodes the raw binary ciphertext so that it round-trips
through a ``Text`` database column (``wrapped_org_kek``).
"""

from __future__ import annotations

import asyncio
import base64

from app.services.kms.base import KMSProvider


class GCPKMSProvider(KMSProvider):
    """Encrypt/decrypt org KEKs via Google Cloud KMS.

    Parameters
    ----------
    key_name : str
        Full resource name of the symmetric CryptoKey, e.g.
        ``projects/my-project/locations/global/keyRings/my-ring/cryptoKeys/my-key``.
        Do NOT include a version suffix.
    """

    def __init__(self, key_name: str, client=None) -> None:
        """If ``client`` is provided (e.g. a mock for testing), use it directly
        instead of creating a google-cloud-kms client."""
        if client is not None:
            self._client = client
        else:
            from google.cloud import kms_v1

            self._client = kms_v1.KeyManagementServiceClient()
        self._key_name = key_name

    async def encrypt(self, plaintext: bytes) -> bytes:
        resp = await asyncio.to_thread(
            self._client.encrypt,
            request={"name": self._key_name, "plaintext": plaintext},
        )
        return base64.b64encode(resp.ciphertext)

    async def decrypt(self, ciphertext: bytes) -> bytes:
        blob = base64.b64decode(ciphertext)
        resp = await asyncio.to_thread(
            self._client.decrypt,
            request={"name": self._key_name, "ciphertext": blob},
        )
        return resp.plaintext

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(
                self._client.get_crypto_key,
                request={"name": self._key_name},
            )
            return True
        except Exception:  # noqa: BLE001 - GCP unreachable / key not found
            return False
