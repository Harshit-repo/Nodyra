"""Tests for the s3_compatible credential type registration."""
from __future__ import annotations

from app.services.credential_types import get_credential_type, list_credential_types


def test_s3_compatible_credential_type_exists() -> None:
    spec = get_credential_type("s3_compatible")
    assert spec is not None
    assert spec.provider == "Storage"
    field_keys = {f.key for f in spec.fields}
    assert field_keys == {"access_key_id", "secret_access_key", "endpoint_url", "region"}


def test_s3_compatible_endpoint_url_not_required() -> None:
    spec = get_credential_type("s3_compatible")
    assert spec is not None
    endpoint_field = next(f for f in spec.fields if f.key == "endpoint_url")
    assert not endpoint_field.required


def test_s3_compatible_secret_key_is_secret() -> None:
    spec = get_credential_type("s3_compatible")
    assert spec is not None
    secret_field = next(f for f in spec.fields if f.key == "secret_access_key")
    assert secret_field.secret is True


def test_s3_compatible_access_key_not_secret() -> None:
    spec = get_credential_type("s3_compatible")
    assert spec is not None
    field = next(f for f in spec.fields if f.key == "access_key_id")
    assert field.secret is False


def test_s3_compatible_appears_in_list() -> None:
    ids = {spec.id for spec in list_credential_types()}
    assert "s3_compatible" in ids
