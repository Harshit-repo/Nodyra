"""AWSKMSProvider — AWS Key Management Service.

Uses boto3 which picks up credentials from the standard chain:
environment variables (``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``),
instance IAM role, or credential file.  No explicit AWS auth config is needed
in Nodyra settings — document as "requires AWS credentials in environment or
IAM role attached to the instance."

The provider base64-encodes the raw binary ``CiphertextBlob`` so that the
ciphertext round-trips through a ``Text`` database column (``wrapped_org_kek``).
"""

from __future__ import annotations

import asyncio
import base64

from app.services.kms.base import KMSProvider


class AWSKMSProvider(KMSProvider):
    """Encrypt/decrypt org KEKs via AWS KMS.

    Parameters
    ----------
    key_id : str
        AWS KMS key identifier (key ID, key ARN, alias name, or alias ARN).
    region : str
        AWS region (default ``us-east-1``).
    """

    def __init__(
        self,
        key_id: str,
        region: str = "us-east-1",
        client=None,
    ) -> None:
        """If ``client`` is provided (e.g. a mock for testing), use it directly
        instead of creating a boto3 client."""
        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client("kms", region_name=region)
        self._key_id = key_id

    async def encrypt(self, plaintext: bytes) -> bytes:
        resp = await asyncio.to_thread(
            self._client.encrypt,
            KeyId=self._key_id,
            Plaintext=plaintext,
        )
        # Base64-encode binary CiphertextBlob so it can be stored in a Text column.
        return base64.b64encode(resp["CiphertextBlob"])

    async def decrypt(self, ciphertext: bytes) -> bytes:
        blob = base64.b64decode(ciphertext)
        resp = await asyncio.to_thread(
            self._client.decrypt,
            CiphertextBlob=blob,
        )
        return resp["Plaintext"]

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(
                self._client.describe_key,
                KeyId=self._key_id,
            )
            return True
        except Exception:  # noqa: BLE001 - KMS unreachable / key not found
            return False
