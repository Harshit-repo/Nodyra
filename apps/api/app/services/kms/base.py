"""KMS provider abstract base class.

Each provider handles key selection internally via its own config.
No ``key_id`` in the interface — "which key to use" is a provider-level
concern, not a call-site concern. Vault uses ``key_name`` from config;
AWS uses ``key_id`` from config; GCP uses ``key_name`` from config.
This avoids callers needing to know the provider's key addressing scheme.
"""

from abc import ABC, abstractmethod


class KMSProvider(ABC):
    """Abstract interface for master KEK encryption/decryption.

    All methods are async so that network-bound providers (Vault, AWS, GCP)
    do not block the event loop. The built-in ``EnvKMSProvider`` trivially
    satisfies the interface without real I/O.
    """

    @abstractmethod
    async def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt ``plaintext`` and return ciphertext bytes.

        The returned ciphertext MUST be convertible to/from a Unicode string
        via ``.decode("utf-8")`` / ``.encode("utf-8")`` so it can be stored in
        a ``Text`` database column. Providers whose native ciphertext is binary
        (e.g. AWS KMS) base64-encode at this boundary.
        """
        ...

    @abstractmethod
    async def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt ``ciphertext`` (as produced by ``encrypt``) and return
        the original plaintext."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return ``True`` if the provider is reachable and the key is usable.

        This is a liveness probe — it should complete quickly (a few seconds
        at most). ``EnvKMSProvider`` always returns ``True``.
        """
        ...
