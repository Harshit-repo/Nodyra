"""The Vault KMS transport must not carry key material in the clear (F-24).

``VaultKMSProvider`` exists to keep org KEKs out of the application's own
storage by handing them to Vault's Transit engine. To do that it sends the
**plaintext KEK** to Vault and receives it back. Over ``http://`` that is the
one secret this component exists to protect, crossing the network unencrypted,
alongside the Vault token in a request header.

Nothing rejected a plaintext URL, and the provider's own docstring used
``http://vault:8200`` as its example — so the documented configuration was the
unsafe one.
"""

from __future__ import annotations

import pytest

from app.config import Settings


def _settings(**overrides) -> tuple[bool, list[str]]:
    """Build settings for a Vault deployment and return (ok, errors)."""
    base = {
        "kms_provider": "vault",
        "vault_url": "https://vault.internal:8200",
        "vault_token": "s.token",
        "secret_key": "x" * 40,
    }
    base.update(overrides)
    settings = Settings(**base)
    errors = settings.security_startup_errors()
    return not errors, errors


def test_https_vault_is_accepted():
    ok, errors = _settings()
    assert ok, errors


def test_plaintext_http_vault_is_refused():
    """The KEK and the Vault token both travel over this connection."""
    ok, errors = _settings(vault_url="http://vault:8200")
    assert not ok
    assert any("VAULT_URL" in e and "https" in e.lower() for e in errors), errors


def test_the_refusal_names_the_escape_hatch():
    """A deployment on a trusted network can accept the trade-off, but it has
    to say so — the error must tell an operator how, or they will reach for a
    worse workaround."""
    _, errors = _settings(vault_url="http://vault:8200")
    joined = " ".join(errors)
    assert "VAULT_ALLOW_INSECURE_TRANSPORT" in joined, joined


def test_an_explicit_opt_in_allows_plaintext():
    ok, errors = _settings(
        vault_url="http://vault:8200", vault_allow_insecure_transport=True
    )
    assert ok, errors


def test_loopback_is_allowed_without_the_opt_in():
    """A Vault on the same host never crosses a network, and requiring the
    opt-in there would just train operators to set it everywhere."""
    for url in ("http://127.0.0.1:8200", "http://localhost:8200", "http://[::1]:8200"):
        ok, errors = _settings(vault_url=url)
        assert ok, (url, errors)


@pytest.mark.parametrize("url", ["vault:8200", "ftp://vault:8200", "not a url"])
def test_a_url_with_no_usable_scheme_is_refused(url):
    ok, errors = _settings(vault_url=url)
    assert not ok, url


def test_the_provider_docstring_does_not_advertise_plaintext():
    """The example in the docstring was ``http://vault:8200``. Documentation is
    configuration for most operators."""
    import inspect

    from app.services.kms.vault import VaultKMSProvider

    doc = inspect.getdoc(VaultKMSProvider) or ""
    assert "http://vault" not in doc, (
        "the provider's own docstring still shows a plaintext Vault URL as its example"
    )


def test_other_providers_are_unaffected():
    """The check must be specific to Vault; AWS and GCP use signed SDK
    transports and have no URL of their own."""
    settings = Settings(
        kms_provider="aws", aws_kms_key_id="alias/nodyra", secret_key="x" * 40
    )
    assert not [e for e in settings.security_startup_errors() if "VAULT" in e]
