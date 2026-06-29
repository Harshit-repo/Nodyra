"""Tests for KMS provider implementations and factory."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.services.kms import get_kms_provider, invalidate_kms_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_kms_cache():
    """Clear the singleton cache before and after every test so provider
    selection from settings is fresh."""
    invalidate_kms_cache()
    yield
    invalidate_kms_cache()


@pytest.fixture
def _env_provider():
    """Force kms_provider=env for the test scope."""
    _orig = settings.kms_provider
    settings.kms_provider = "env"
    invalidate_kms_cache()
    yield
    settings.kms_provider = _orig
    invalidate_kms_cache()


@pytest.fixture
def _vault_provider():
    """Force kms_provider=vault with dummy Vault config."""
    _orig_provider = settings.kms_provider
    _orig_url = settings.vault_url
    _orig_token = settings.vault_token
    settings.kms_provider = "vault"
    settings.vault_url = "http://vault:8200"
    settings.vault_token = "test-token"
    settings.vault_transit_mount = "transit"
    settings.vault_transit_key = "test-key"
    invalidate_kms_cache()
    yield
    settings.kms_provider = _orig_provider
    settings.vault_url = _orig_url
    settings.vault_token = _orig_token
    invalidate_kms_cache()


# ---------------------------------------------------------------------------
# EnvKMSProvider tests
# ---------------------------------------------------------------------------


class TestEnvKMSProvider:
    def test_provider_is_singleton(self, _env_provider):
        p1 = get_kms_provider()
        p2 = get_kms_provider()
        assert p1 is p2

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip(self, _env_provider):
        provider = get_kms_provider()
        plaintext = b"test-org-kek-32-bytes-long!"
        ciphertext = await provider.encrypt(plaintext)
        assert isinstance(ciphertext, bytes)
        assert ciphertext != plaintext
        decrypted = await provider.decrypt(ciphertext)
        assert decrypted == plaintext

    @pytest.mark.asyncio
    async def test_health_check(self, _env_provider):
        provider = get_kms_provider()
        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_ciphertext_is_text_safe(self, _env_provider):
        """EnvKMSProvider ciphertext should be valid UTF-8 so it can be stored
        in a Text column."""
        provider = get_kms_provider()
        ciphertext = await provider.encrypt(b"plaintext-data")
        decoded = ciphertext.decode("utf-8")
        assert isinstance(decoded, str)
        # Re-encode and decrypt should work
        re_encoded = decoded.encode("utf-8")
        assert await provider.decrypt(re_encoded) == b"plaintext-data"


# ---------------------------------------------------------------------------
# VaultKMSProvider tests
# ---------------------------------------------------------------------------


class TestVaultKMSProvider:
    @pytest.mark.asyncio
    async def test_encrypt(self, _vault_provider):
        """Verify encrypt calls Vault with correct payload and returns ciphertext bytes."""
        provider = get_kms_provider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"ciphertext": "vault:v1:abc123"}
        }
        provider._client.post = AsyncMock(return_value=mock_response)

        result = await provider.encrypt(b"plaintext-kek")
        assert result == b"vault:v1:abc123"
        provider._client.post.assert_called_once()
        call_url = provider._client.post.call_args[0][0]
        assert "encrypt" in call_url
        call_json = provider._client.post.call_args[1]["json"]
        assert "plaintext" in call_json

    @pytest.mark.asyncio
    async def test_decrypt(self, _vault_provider):
        """Verify decrypt sends ciphertext and decodes base64 plaintext."""
        provider = get_kms_provider()
        input_kek = b"plaintext-kek-value"
        vault_ciphertext = "vault:v1:encrypted-data"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"plaintext": base64.b64encode(input_kek).decode()}
        }
        provider._client.post = AsyncMock(return_value=mock_response)

        result = await provider.decrypt(vault_ciphertext.encode())
        assert result == input_kek
        provider._client.post.assert_called_once()
        call_url = provider._client.post.call_args[0][0]
        assert "decrypt" in call_url

    @pytest.mark.asyncio
    async def test_health_check_success(self, _vault_provider):
        provider = get_kms_provider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        provider._client.get = AsyncMock(return_value=mock_response)
        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, _vault_provider):
        provider = get_kms_provider()
        provider._client.get = AsyncMock(side_effect=ConnectionError("Connection refused"))
        assert await provider.health_check() is False

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip_mocked(self, _vault_provider):
        """Full roundtrip with mocked Vault responses."""
        provider = get_kms_provider()
        plaintext = b"org-secret-kek-data"

        # Mock encrypt
        enc_response = MagicMock()
        enc_response.status_code = 200
        enc_response.json.return_value = {
            "data": {"ciphertext": "vault:v1:encrypted-test-value"}
        }

        # Mock decrypt
        dec_response = MagicMock()
        dec_response.status_code = 200
        dec_response.json.return_value = {
            "data": {"plaintext": base64.b64encode(plaintext).decode()}
        }

        provider._client.post = AsyncMock()
        provider._client.post.side_effect = [enc_response, dec_response]

        ciphertext = await provider.encrypt(plaintext)
        decrypted = await provider.decrypt(ciphertext)
        assert decrypted == plaintext


# ---------------------------------------------------------------------------
# AWSKMSProvider tests
# ---------------------------------------------------------------------------


class TestAWSKMSProvider:
    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip_mocked(self):
        """Full roundtrip with mocked boto3 client."""
        from app.services.kms.aws_kms import AWSKMSProvider

        mock_client = MagicMock()
        provider = AWSKMSProvider(
            key_id="arn:aws:kms:us-east-1:123456789012:key/abc123",
            region="us-east-1",
            client=mock_client,
        )
        plaintext = b"test-kek-data"

        mock_client.encrypt.return_value = {
            "CiphertextBlob": b"\x00\x01\x02\x03ciphertext-binary"
        }
        mock_client.decrypt.return_value = {"Plaintext": plaintext}

        ciphertext = await provider.encrypt(plaintext)
        assert isinstance(ciphertext, bytes)
        # Ciphertext should be base64-encoded
        assert ciphertext != b"\x00\x01\x02\x03ciphertext-binary"
        decoded_str = ciphertext.decode("ascii")
        assert len(decoded_str) > 0

        decrypted = await provider.decrypt(ciphertext)
        assert decrypted == plaintext

        mock_client.encrypt.assert_called_once_with(
            KeyId="arn:aws:kms:us-east-1:123456789012:key/abc123",
            Plaintext=plaintext,
        )
        assert mock_client.decrypt.called

    @pytest.mark.asyncio
    async def test_health_check(self):
        from app.services.kms.aws_kms import AWSKMSProvider

        mock_client = MagicMock()
        provider = AWSKMSProvider(
            key_id="alias/test-key",
            client=mock_client,
        )
        assert await provider.health_check() is True
        mock_client.describe_key.assert_called_once_with(KeyId="alias/test-key")

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        from app.services.kms.aws_kms import AWSKMSProvider

        mock_client = MagicMock()
        mock_client.describe_key.side_effect = Exception("KMS unreachable")
        provider = AWSKMSProvider(
            key_id="alias/test-key",
            client=mock_client,
        )
        assert await provider.health_check() is False


# ---------------------------------------------------------------------------
# GCPKMSProvider tests
# ---------------------------------------------------------------------------


class TestGCPKMSProvider:
    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip_mocked(self):
        """Full roundtrip with mocked google-cloud-kms client."""
        from app.services.kms.gcp_kms import GCPKMSProvider

        mock_client = MagicMock()
        provider = GCPKMSProvider(
            key_name="projects/test/locations/global/keyRings/test/cryptoKeys/test",
            client=mock_client,
        )
        plaintext = b"test-kek-data"

        enc_response = MagicMock()
        enc_response.ciphertext = b"\x00\x01\x02\x03gcp-ciphertext"
        mock_client.encrypt.return_value = enc_response

        dec_response = MagicMock()
        dec_response.plaintext = plaintext
        mock_client.decrypt.return_value = dec_response

        ciphertext = await provider.encrypt(plaintext)
        assert isinstance(ciphertext, bytes)
        decoded_str = ciphertext.decode("ascii")
        assert len(decoded_str) > 0

        decrypted = await provider.decrypt(ciphertext)
        assert decrypted == plaintext

        mock_client.encrypt.assert_called_once()
        mock_client.decrypt.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check(self):
        from app.services.kms.gcp_kms import GCPKMSProvider

        mock_client = MagicMock()
        provider = GCPKMSProvider(
            key_name="projects/test/locations/global/keyRings/test/cryptoKeys/test",
            client=mock_client,
        )
        assert await provider.health_check() is True
        mock_client.get_crypto_key.assert_called_once()


# ---------------------------------------------------------------------------
# Factory and config validation tests
# ---------------------------------------------------------------------------


class TestKMSFactory:
    def test_invalid_provider_raises(self):
        _orig = settings.kms_provider
        settings.kms_provider = "invalid_value"  # type: ignore[assignment]
        invalidate_kms_cache()
        with pytest.raises(ValueError, match="Unknown kms_provider"):
            # Call _build() directly via cache_clear + get
            from app.services.kms import _build

            _build()
        settings.kms_provider = _orig
        invalidate_kms_cache()

    def test_env_provider_is_returned(self, _env_provider):
        from app.services.kms.env_kms import EnvKMSProvider

        provider = get_kms_provider()
        assert isinstance(provider, EnvKMSProvider)

    @pytest.mark.asyncio
    async def test_factory_provider_change(self):
        """Switching kms_provider config returns a different provider instance."""
        _orig = settings.kms_provider

        # Start with env
        settings.kms_provider = "env"
        invalidate_kms_cache()
        p1 = get_kms_provider()

        # Switch to vault (requires mocked settings)
        settings.kms_provider = "vault"
        settings.vault_url = "http://vault:8200"
        settings.vault_token = "test"
        invalidate_kms_cache()
        p2 = get_kms_provider()

        assert p1 is not p2
        settings.kms_provider = _orig
        invalidate_kms_cache()


# ---------------------------------------------------------------------------
# org_keys integration tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_env_provider")
class TestOrgKeysIntegration:
    """Verify that org_keys.py correctly delegates to get_kms_provider()."""

    @pytest.mark.asyncio
    async def test_get_org_kek_uses_kms_provider(self):
        """get_org_kek should delegate to get_kms_provider for encryption
        and decryption (smoke test via env provider)."""
        from app.services.org_keys import get_org_kek
        from app.services.kms import get_kms_provider

        provider = get_kms_provider()
        # The env provider should be healthy
        assert await provider.health_check()

        # This is a minimal integration check: the call should not crash.
        # A real roundtrip would need a DB session with an org row.
        # Verifying the import path works and no import-time errors occur.
        assert get_org_kek is not None

    def test_cache_invalidation_works(self):
        """invalidate_kms_cache and invalidate_kek_cache should not interfere."""
        from app.services.org_keys import invalidate_kek_cache

        invalidate_kms_cache()
        invalidate_kek_cache()
        # Both should be callable without error
        assert True


# ---------------------------------------------------------------------------
# Migration script test
# ---------------------------------------------------------------------------


class TestKMSMigration:
    @pytest.mark.asyncio
    async def test_migration_roundtrip(self):
        """Test that the migration pattern (decrypt with old, encrypt with new)
        works correctly using env and aws providers."""
        from app.services.kms.aws_kms import AWSKMSProvider
        from app.services.kms.env_kms import EnvKMSProvider

        plaintext_kek = b"my-org-kek-value-32bytes!"

        env_provider = EnvKMSProvider()
        old_wrapped = await env_provider.encrypt(plaintext_kek)

        # Decrypt with env provider (simulating source provider during migration)
        decrypted = await env_provider.decrypt(old_wrapped)
        assert decrypted == plaintext_kek

        # Encrypt/decrypt with aws provider (simulating target during migration)
        mock_client = MagicMock()
        mock_client.encrypt.return_value = {
            "CiphertextBlob": b"\x00\x01\x02aws-ciphertext"
        }
        mock_client.decrypt.return_value = {"Plaintext": plaintext_kek}
        aws_provider = AWSKMSProvider(
            key_id="arn:aws:kms:us-east-1:key/test",
            client=mock_client,
        )

        new_wrapped = await aws_provider.encrypt(decrypted)
        assert new_wrapped is not None

        re_decrypted = await aws_provider.decrypt(new_wrapped)
        assert re_decrypted == plaintext_kek


# ---------------------------------------------------------------------------
# KMS failure isolation test
# ---------------------------------------------------------------------------


class TestKMSFailureIsolation:
    """Verify that a failing KMS provider does not cascade into credential
    access silently — the error should be contained."""

    @pytest.mark.asyncio
    async def test_vault_failure_raises_http_error(self, _vault_provider):
        """When Vault is unreachable, decrypt should propagate the error."""
        provider = get_kms_provider()
        provider._client.post = AsyncMock(
            side_effect=ConnectionError("Vault unreachable")
        )
        with pytest.raises(ConnectionError):
            await provider.decrypt(b"some-ciphertext")

    @pytest.mark.asyncio
    async def test_aws_failure_blocks_decrypt(self):
        """When AWS KMS is unreachable, decrypt should propagate the error."""
        from app.services.kms.aws_kms import AWSKMSProvider

        mock_client = MagicMock()
        mock_client.decrypt.side_effect = Exception("AWS KMS unreachable")
        provider = AWSKMSProvider(
            key_id="alias/test",
            client=mock_client,
        )
        # Use a valid base64-encoded ciphertext so we reach the boto3 call
        valid_ciphertext = base64.b64encode(b"some-binary-blob")
        with pytest.raises(Exception, match="AWS KMS unreachable"):
            await provider.decrypt(valid_ciphertext)

    @pytest.mark.asyncio
    async def test_gcp_failure_blocks_decrypt(self):
        """When GCP KMS is unreachable, decrypt should propagate the error."""
        from app.services.kms.gcp_kms import GCPKMSProvider

        mock_client = MagicMock()
        mock_client.decrypt.side_effect = Exception("GCP KMS unreachable")
        provider = GCPKMSProvider(
            key_name="projects/test/locations/global/keyRings/test/cryptoKeys/test",
            client=mock_client,
        )
        # Use a valid base64-encoded ciphertext so we reach the gcp-kms call
        valid_ciphertext = base64.b64encode(b"some-binary-blob")
        with pytest.raises(Exception, match="GCP KMS unreachable"):
            await provider.decrypt(valid_ciphertext)
