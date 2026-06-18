"""Backend-owned credential type definitions.

These specs describe how credentials should be created, tested, and eventually
connected through OAuth. They do not contain stored user secrets.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CredentialFieldSpec:
    key: str
    label: str
    secret: bool = True
    required: bool = True
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class OAuthCredentialSpec:
    auth_url: str
    token_url: str
    scopes: list[str] = field(default_factory=list)
    authorization_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CredentialTypeSpec:
    id: str
    name: str
    provider: str
    auth_method: str
    fields: list[CredentialFieldSpec] = field(default_factory=list)
    oauth: OAuthCredentialSpec | None = None
    test_service: str | None = None
    documentation_url: str = ""
    default_scopes: list[str] = field(default_factory=list)


_API_KEY_FIELD = CredentialFieldSpec(
    key="api_key",
    label="API key",
    placeholder="Paste API key",
)

_TOKEN_FIELD = CredentialFieldSpec(
    key="token",
    label="Token",
    placeholder="Paste access token",
)


_TYPES: tuple[CredentialTypeSpec, ...] = (
    CredentialTypeSpec(
        id="openai",
        name="OpenAI API Key",
        provider="OpenAI",
        auth_method="api_key",
        fields=[_API_KEY_FIELD],
        test_service="openai",
        documentation_url="https://platform.openai.com/api-keys",
    ),
    CredentialTypeSpec(
        id="anthropic",
        name="Anthropic API Key",
        provider="Anthropic",
        auth_method="api_key",
        fields=[_API_KEY_FIELD],
        test_service="anthropic",
        documentation_url="https://console.anthropic.com/settings/keys",
    ),
    CredentialTypeSpec(
        id="openrouter",
        name="OpenRouter API Key",
        provider="OpenRouter",
        auth_method="api_key",
        fields=[_API_KEY_FIELD],
        test_service="openrouter",
        documentation_url="https://openrouter.ai/settings/keys",
    ),
    CredentialTypeSpec(
        id="pinecone",
        name="Pinecone",
        provider="Pinecone",
        auth_method="api_key",
        fields=[
            _API_KEY_FIELD,
            CredentialFieldSpec(
                key="index_host",
                label="Index host",
                secret=False,
                placeholder="https://example-index.svc.region.pinecone.io",
            ),
        ],
        test_service="pinecone",
        documentation_url="https://docs.pinecone.io/guides/projects/manage-api-keys",
    ),
    CredentialTypeSpec(
        id="qdrant",
        name="Qdrant",
        provider="Qdrant",
        auth_method="api_key",
        fields=[
            CredentialFieldSpec(
                key="url",
                label="Cluster URL",
                secret=False,
                placeholder="https://cluster-id.region.cloud.qdrant.io",
            ),
            _API_KEY_FIELD,
        ],
        test_service="qdrant",
        documentation_url="https://qdrant.tech/documentation/cloud/authentication/",
    ),
    CredentialTypeSpec(
        id="slack_bot",
        name="Slack Bot Token",
        provider="Slack",
        auth_method="api_key",
        fields=[
            CredentialFieldSpec(
                key="bot_token",
                label="Bot token",
                placeholder="xoxb-...",
            )
        ],
        test_service="slack_bot",
        documentation_url="https://api.slack.com/authentication/token-types",
    ),
    CredentialTypeSpec(
        id="github",
        name="GitHub Token",
        provider="GitHub",
        auth_method="api_key",
        fields=[_TOKEN_FIELD],
        test_service="github",
        documentation_url="https://docs.github.com/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens",
    ),
    CredentialTypeSpec(
        id="github_oauth2",
        name="GitHub OAuth2",
        provider="GitHub",
        auth_method="oauth2",
        oauth=OAuthCredentialSpec(
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            scopes=["repo", "read:user"],
        ),
        test_service="github",
        documentation_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps",
        default_scopes=["repo", "read:user"],
    ),
    CredentialTypeSpec(
        id="slack_oauth2",
        name="Slack OAuth2",
        provider="Slack",
        auth_method="oauth2",
        oauth=OAuthCredentialSpec(
            auth_url="https://slack.com/oauth/v2/authorize",
            token_url="https://slack.com/api/oauth.v2.access",
            scopes=["chat:write", "channels:read", "users:read"],
        ),
        test_service="slack_bot",
        documentation_url="https://api.slack.com/authentication/oauth-v2",
        default_scopes=["chat:write", "channels:read"],
    ),
    CredentialTypeSpec(
        id="google_sheets_oauth2",
        name="Google Sheets OAuth2",
        provider="Google",
        auth_method="oauth2",
        oauth=OAuthCredentialSpec(
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
            authorization_params={
                "access_type": "offline",
                "prompt": "consent",
            },
        ),
        test_service="google_sheets",
        documentation_url="https://developers.google.com/identity/protocols/oauth2/web-server",
        default_scopes=["https://www.googleapis.com/auth/spreadsheets"],
    ),
    CredentialTypeSpec(
        id="google_service_account",
        name="Google Service Account",
        provider="Google",
        auth_method="service_account",
        fields=[
            CredentialFieldSpec(
                key="service_account_json",
                label="Service account JSON",
                placeholder='{"type":"service_account", ...}',
            )
        ],
        test_service="google_sheets",
        documentation_url="https://cloud.google.com/iam/docs/service-account-creds",
    ),
    CredentialTypeSpec(
        id="microsoft_outlook_oauth2",
        name="Microsoft Outlook OAuth2",
        provider="Microsoft",
        auth_method="oauth2",
        oauth=OAuthCredentialSpec(
            auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            scopes=[
                "offline_access",
                "https://graph.microsoft.com/Mail.ReadWrite",
                "https://graph.microsoft.com/Mail.Send",
                "https://graph.microsoft.com/Calendars.ReadWrite",
            ],
        ),
        test_service="microsoft_outlook",
        documentation_url="https://learn.microsoft.com/entra/identity-platform/v2-oauth2-auth-code-flow",
        default_scopes=[
            "offline_access",
            "https://graph.microsoft.com/Mail.ReadWrite",
            "https://graph.microsoft.com/Mail.Send",
        ],
    ),
    CredentialTypeSpec(
        id="mcp_server",
        name="MCP Server",
        provider="MCP",
        auth_method="api_key",
        fields=[
            CredentialFieldSpec(
                key="url",
                label="Server URL",
                secret=False,
                placeholder="https://example.com/mcp",
                help="Streamable-HTTP MCP endpoint.",
            ),
            CredentialFieldSpec(
                key="auth_token",
                label="Bearer token",
                required=False,
                placeholder="Optional Authorization bearer token",
            ),
            CredentialFieldSpec(
                key="headers_json",
                label="Extra headers (JSON object)",
                required=False,
                placeholder='{"X-Custom": "value"}',
            ),
        ],
        documentation_url="https://modelcontextprotocol.io",
    ),
    CredentialTypeSpec(
        id="s3_compatible",
        name="S3-Compatible Storage",
        provider="Storage",
        auth_method="api_key",
        fields=[
            CredentialFieldSpec(
                key="access_key_id",
                label="Access Key ID",
                secret=False,
                placeholder="AKIAIOSFODNN7EXAMPLE",
            ),
            CredentialFieldSpec(
                key="secret_access_key",
                label="Secret Access Key",
                placeholder="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            ),
            CredentialFieldSpec(
                key="endpoint_url",
                label="Endpoint URL",
                secret=False,
                required=False,
                placeholder="https://s3.amazonaws.com",
                help="Leave blank for AWS S3. Set to e.g. http://minio:9000 for MinIO.",
            ),
            CredentialFieldSpec(
                key="region",
                label="Region",
                secret=False,
                required=False,
                placeholder="us-east-1",
            ),
        ],
    ),
)

_BY_ID: dict[str, CredentialTypeSpec] = {spec.id: spec for spec in _TYPES}


def list_credential_types() -> list[CredentialTypeSpec]:
    return sorted(_TYPES, key=lambda spec: (spec.provider.lower(), spec.name.lower()))


def get_credential_type(type_id: str) -> CredentialTypeSpec | None:
    return _BY_ID.get(type_id)
