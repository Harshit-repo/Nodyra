"""EnvKMSProvider — existing SECRET_KEY-based encryption wrapped as KMSProvider.

Wraps the existing ``crypto.wrap_org_kek`` / ``crypto.unwrap_org_kek`` helpers
which use a Fernet key derived from ``settings.secret_key``.  No network calls;
zero config change for existing deployments.
"""

from app.services.kms.base import KMSProvider


class EnvKMSProvider(KMSProvider):
    """Legacy environment-variable-based master KEK provider.

    Delegates to ``crypto.wrap_org_kek`` / ``crypto.unwrap_org_kek`` which
    implement Fernet envelope encryption keyed from ``SECRET_KEY``.
    """

    async def encrypt(self, plaintext: bytes) -> bytes:
        from app.services.crypto import wrap_org_kek

        return wrap_org_kek(plaintext).encode("utf-8")

    async def decrypt(self, ciphertext: bytes) -> bytes:
        from app.services.crypto import unwrap_org_kek

        return unwrap_org_kek(ciphertext.decode("utf-8"))

    async def health_check(self) -> bool:
        return True
