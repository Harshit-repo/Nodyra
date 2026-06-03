"""OAuth helpers for backend-owned credential types.

The API owns provider client configuration and stores returned tokens inside
the existing encrypted credential payload. Browser state tokens are signed, but
they intentionally do not carry client secrets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

from app.config import settings
from app.models import Credential
from app.services.credential_types import CredentialTypeSpec, get_credential_type
from app.services.crypto import (
    create_payload_token,
    decode_payload_token,
    encrypt_credential,
)

OAUTH_STATE_TTL_SECONDS = 10 * 60
OAUTH_REFRESH_SKEW_SECONDS = 60


class OAuthError(RuntimeError):
    """OAuth error that can be mapped to an HTTP response or run failure."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OAuthClientConfig:
    client_id: str
    client_secret: str


_CLIENT_SETTING_NAMES = {
    "google_sheets_oauth2": (
        "google_oauth_client_id",
        "google_oauth_client_secret",
    ),
    "microsoft_outlook_oauth2": (
        "microsoft_oauth_client_id",
        "microsoft_oauth_client_secret",
    ),
    "slack_oauth2": ("slack_oauth_client_id", "slack_oauth_client_secret"),
    "github_oauth2": ("github_oauth_client_id", "github_oauth_client_secret"),
}


def oauth_type_spec(type_id: str) -> CredentialTypeSpec:
    spec = get_credential_type(type_id)
    if spec is None:
        raise OAuthError(f"Unknown credential type '{type_id}'.", 404)
    if spec.auth_method != "oauth2" or spec.oauth is None:
        raise OAuthError(f"Credential type '{type_id}' is not an OAuth2 type.")
    return spec


def oauth_client_config(type_id: str) -> OAuthClientConfig:
    setting_names = _CLIENT_SETTING_NAMES.get(type_id)
    if setting_names is None:
        raise OAuthError(
            f"Credential type '{type_id}' has no OAuth client configuration mapping."
        )
    client_id = str(getattr(settings, setting_names[0], "") or "").strip()
    client_secret = str(getattr(settings, setting_names[1], "") or "").strip()
    if not client_id or not client_secret:
        raise OAuthError(
            "OAuth client configuration is missing for credential type "
            f"'{type_id}'. Configure {setting_names[0].upper()} and "
            f"{setting_names[1].upper()}.",
            503,
        )
    return OAuthClientConfig(client_id=client_id, client_secret=client_secret)


def resolve_redirect_uri(explicit: str, fallback: str) -> str:
    if explicit.strip():
        return explicit.strip()
    base = settings.oauth_redirect_base_url.strip().rstrip("/")
    if base:
        return f"{base}/credentials/oauth/callback"
    return fallback


def default_scopes(type_id: str, requested: list[str] | None = None) -> list[str]:
    if requested:
        return [scope.strip() for scope in requested if scope.strip()]
    spec = oauth_type_spec(type_id)
    return list(spec.default_scopes or (spec.oauth.scopes if spec.oauth else []))


def create_oauth_state(payload: dict[str, Any]) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
    return create_payload_token(payload, OAUTH_STATE_TTL_SECONDS), expires_at


def decode_oauth_state(state: str) -> dict[str, Any]:
    payload = decode_payload_token(state)
    if payload is None:
        raise OAuthError("Invalid or expired OAuth state.", 400)
    for key in ("credential_type", "name", "scope", "redirect_uri"):
        if not str(payload.get(key) or "").strip():
            raise OAuthError("OAuth state is missing required credential metadata.", 400)
    return payload


def build_authorization_url(
    *,
    type_id: str,
    redirect_uri: str,
    state: str,
    scopes: list[str],
) -> str:
    spec = oauth_type_spec(type_id)
    config = oauth_client_config(type_id)
    assert spec.oauth is not None
    query = {
        **spec.oauth.authorization_params,
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if scopes:
        query["scope"] = " ".join(scopes)
    return f"{spec.oauth.auth_url}?{urlencode(query)}"


def _flatten_form_payload(text: str) -> dict[str, Any]:
    parsed = parse_qs(text, keep_blank_values=True)
    return {
        key: values[-1] if len(values) == 1 else values
        for key, values in parsed.items()
    }


async def _post_token_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, data=data, headers=headers)

    if response.status_code < 200 or response.status_code >= 300:
        detail = response.text[:300].strip()
        suffix = f": {detail}" if detail else ""
        raise OAuthError(
            f"OAuth token request failed with HTTP {response.status_code}{suffix}",
            400,
        )

    try:
        payload = response.json()
    except ValueError:
        payload = _flatten_form_payload(response.text)
    if not isinstance(payload, dict):
        raise OAuthError("OAuth token response was not an object.", 400)
    if payload.get("error") or payload.get("ok") is False:
        message = payload.get("error_description") or payload.get("error") or "OAuth error"
        raise OAuthError(f"OAuth token request failed: {message}", 400)
    return payload


async def exchange_authorization_code(
    *,
    type_id: str,
    code: str,
    redirect_uri: str,
    scopes: list[str],
) -> dict[str, Any]:
    spec = oauth_type_spec(type_id)
    config = oauth_client_config(type_id)
    assert spec.oauth is not None
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }
    if scopes:
        data["scope"] = " ".join(scopes)
    return await _post_token_form(spec.oauth.token_url, data)


async def refresh_access_token(
    *,
    type_id: str,
    refresh_token: str,
    scopes: list[str],
) -> dict[str, Any]:
    spec = oauth_type_spec(type_id)
    config = oauth_client_config(type_id)
    assert spec.oauth is not None
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }
    if scopes:
        data["scope"] = " ".join(scopes)
    return await _post_token_form(spec.oauth.token_url, data)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_expires_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _expires_at_from_payload(
    token_payload: dict[str, Any],
    previous: dict[str, Any] | None,
) -> str:
    explicit = parse_expires_at(token_payload.get("expires_at"))
    if explicit is not None:
        return _format_datetime(explicit)
    expires_in = token_payload.get("expires_in")
    if expires_in not in (None, ""):
        try:
            seconds = int(float(str(expires_in)))
        except (TypeError, ValueError):
            seconds = 0
        if seconds > 0:
            return _format_datetime(datetime.now(UTC) + timedelta(seconds=seconds))
    prior = parse_expires_at((previous or {}).get("expires_at"))
    return _format_datetime(prior) if prior is not None else ""


def _scope_string(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    return ""


def scopes_from_credential_data(data: dict[str, Any]) -> list[str]:
    scope = _scope_string(data.get("scope"))
    return [item for item in scope.split() if item]


def token_payload_to_credential_data(
    *,
    type_id: str,
    token_payload: dict[str, Any],
    requested_scopes: list[str],
    previous: dict[str, Any] | None = None,
) -> dict[str, str]:
    previous = previous or {}
    access_token = token_payload.get("access_token") or previous.get("access_token")
    if not access_token:
        raise OAuthError("OAuth token response did not include an access token.", 400)

    refresh_token = token_payload.get("refresh_token") or previous.get("refresh_token")
    token_type = token_payload.get("token_type") or previous.get("token_type") or "Bearer"
    scope = (
        _scope_string(token_payload.get("scope"))
        or _scope_string(previous.get("scope"))
        or " ".join(requested_scopes)
    )
    expires_at = _expires_at_from_payload(token_payload, previous)

    data = {
        "access_token": str(access_token),
        "token_type": str(token_type),
    }
    if refresh_token:
        data["refresh_token"] = str(refresh_token)
    if expires_at:
        data["expires_at"] = expires_at
    if scope:
        data["scope"] = scope
    if token_payload.get("id_token"):
        data["id_token"] = str(token_payload["id_token"])
    return data


def should_refresh_credential_data(data: dict[str, Any]) -> bool:
    expires_at = parse_expires_at(data.get("expires_at"))
    if expires_at is None:
        return False
    refresh_before = datetime.now(UTC) + timedelta(seconds=OAUTH_REFRESH_SKEW_SECONDS)
    return expires_at <= refresh_before


async def refresh_stored_credential(
    credential: Credential,
    data: dict[str, Any],
) -> dict[str, str]:
    oauth_type_spec(credential.type)
    refresh_token = str(data.get("refresh_token") or "").strip()
    if not refresh_token:
        raise OAuthError(
            f"OAuth credential '{credential.name}' is expired and has no refresh token.",
            400,
        )
    scopes = scopes_from_credential_data(data)
    token_payload = await refresh_access_token(
        type_id=credential.type,
        refresh_token=refresh_token,
        scopes=scopes,
    )
    updated = token_payload_to_credential_data(
        type_id=credential.type,
        token_payload=token_payload,
        requested_scopes=scopes,
        previous=data,
    )
    credential.encrypted_data, credential.encrypted_dek = encrypt_credential(updated)
    return updated


async def refresh_credential_if_needed(
    credential: Credential,
    data: dict[str, Any],
) -> dict[str, Any]:
    spec = get_credential_type(credential.type)
    if spec is None or spec.auth_method != "oauth2":
        return data
    if not should_refresh_credential_data(data):
        return data
    return await refresh_stored_credential(credential, data)


def redacted_token_payload(payload: dict[str, Any]) -> str:
    """Small debug helper for tests/logs without exposing token values."""
    clean = {
        key: ("***" if "token" in key else value)
        for key, value in payload.items()
    }
    return json.dumps(clean, sort_keys=True)
