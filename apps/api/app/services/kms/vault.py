"""VaultKMSProvider — HashiCorp Vault Transit Engine.

Uses the Vault Transit Secrets Engine to encrypt/decrypt org KEKs.
Static token authentication is acceptable for V1; production deployments
should use Vault AppRole or Kubernetes auth for automatic token rotation.
"""

from __future__ import annotations

import base64

import httpx

from app.services.kms.base import KMSProvider


class VaultKMSProvider(KMSProvider):
    """Encrypt/decrypt org KEKs via HashiCorp Vault Transit.

    Parameters
    ----------
    vault_url : str
        Base URL of the Vault server (e.g. ``http://vault:8200``).
    token : str
        Static Vault token with ``write`` capability on the Transit mount.
    mount : str
        Transit engine mount path (default ``transit``).
    key_name : str
        Name of the encryption key in the Transit engine.
    """

    def __init__(
        self,
        vault_url: str,
        token: str,
        mount: str = "transit",
        key_name: str = "nodyra-master",
    ) -> None:
        self._vault_url = vault_url.rstrip("/")
        self._token = token
        self._mount = mount
        self._key_name = key_name
        # V1: single shared client.  For V2 consider per-request clients
        # with connection-pool sizing via httpx.AsyncClient(limits=...).
        self._client = httpx.AsyncClient(timeout=10.0)

    # -- internal helpers ---------------------------------------------------

    def _encrypt_url(self) -> str:
        return f"{self._vault_url}/v1/{self._mount}/encrypt/{self._key_name}"

    def _decrypt_url(self) -> str:
        return f"{self._vault_url}/v1/{self._mount}/decrypt/{self._key_name}"

    def _headers(self) -> dict[str, str]:
        return {"X-Vault-Token": self._token}

    # -- KMSProvider interface ----------------------------------------------

    async def encrypt(self, plaintext: bytes) -> bytes:
        b64 = base64.b64encode(plaintext).decode("utf-8")
        resp = await self._client.post(
            self._encrypt_url(),
            json={"plaintext": b64},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["data"]["ciphertext"].encode("utf-8")

    async def decrypt(self, ciphertext: bytes) -> bytes:
        resp = await self._client.post(
            self._decrypt_url(),
            json={"ciphertext": ciphertext.decode("utf-8")},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return base64.b64decode(resp.json()["data"]["plaintext"])

    async def aclose(self) -> None:
        """Close the underlying httpx client, releasing connection pool resources."""
        await self._client.aclose()

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self._vault_url}/v1/{self._mount}/health",
                headers=self._headers(),
            )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001 - network errors are not healthy
            return False
