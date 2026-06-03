"""Microsoft Graph transport helpers."""

from __future__ import annotations

from noodle_nodes.integrations_v2.transport import ProviderTransport, RetryPolicy


class MicrosoftGraphTransport(ProviderTransport):
    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = "https://graph.microsoft.com/v1.0",
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        super().__init__(
            provider="microsoft_graph",
            base_url=base_url,
            default_headers={"Authorization": f"Bearer {access_token}"},
            retry_policy=retry_policy,
        )
