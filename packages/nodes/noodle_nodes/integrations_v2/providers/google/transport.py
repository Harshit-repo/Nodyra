"""Google API transport helpers."""

from __future__ import annotations

from noodle_nodes.integrations_v2.transport import ProviderTransport, RetryPolicy


class GoogleTransport(ProviderTransport):
    def __init__(
        self,
        *,
        access_token: str = "",
        api_key: str = "",
        base_url: str = "https://sheets.googleapis.com/v4",
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        super().__init__(
            provider="google",
            base_url=base_url,
            default_headers=headers,
            retry_policy=retry_policy,
        )
        self.api_key = api_key

    def request(self, method: str, path_or_url: str, **kwargs):
        params = dict(kwargs.pop("params", {}) or {})
        if self.api_key and "key" not in params:
            params["key"] = self.api_key
        return super().request(method, path_or_url, params=params, **kwargs)
