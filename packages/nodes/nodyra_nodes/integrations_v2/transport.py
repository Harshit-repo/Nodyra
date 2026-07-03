"""Shared HTTP transport for provider integration nodes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from nodyra.context import node_debug
from nodyra_nodes.http_security import safe_request
from nodyra_nodes.integrations_v2.errors import ProviderError

_logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Maximum pages the paginate() helper will follow to avoid unbounded loops.
DEFAULT_MAX_PAGES = 50
# Maximum items paginate() will collect before truncating (E-12).
# At 10,000 items/page and 50 pages the old cap allowed 500,000 in-memory rows.
DEFAULT_MAX_ITEMS = 10_000
MAX_DEBUG_REQUEST_EVENTS = 100


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0
    retry_status_codes: frozenset[int] = field(
        default_factory=lambda: RETRYABLE_STATUS_CODES
    )


def json_or_text(response: Any) -> Any:
    content = getattr(response, "content", b"")
    if content in (b"", ""):
        return {"status_code": getattr(response, "status_code", None)}
    try:
        return response.json()
    except ValueError:
        return getattr(response, "text", "")


def _summary(response: Any, limit: int = 500) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = str(getattr(response, "text", "") or "")
        return text[:limit]
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value[:limit]
            if isinstance(value, dict):
                nested = value.get("message") or value.get("code")
                if nested:
                    return str(nested)[:limit]
        return str(
            {
                key: payload[key]
                for key in payload.keys()
                if key in {"code", "status", "type", "title"}
            }
        )[:limit]
    return str(payload)[:limit]


def _error_code(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(getattr(response, "status_code", "") or "provider_error")
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error:
            return error
        if isinstance(error, dict):
            return str(error.get("code") or error.get("type") or "provider_error")
        for key in ("code", "type", "status"):
            if payload.get(key):
                return str(payload[key])
    return str(getattr(response, "status_code", "") or "provider_error")


def _request_id(headers: Any) -> str:
    if not headers:
        return ""
    for key in (
        "x-request-id",
        "x-ms-request-id",
        "x-ms-ags-diagnostic",
        "x-goog-request-id",
        "request-id",
    ):
        value = headers.get(key) if hasattr(headers, "get") else None
        if value:
            return str(value)
    return ""


def _safe_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _record_provider_request_event(
    *,
    provider: str,
    operation: str,
    method: str,
    url: str,
    attempt: int,
    max_attempts: int,
    latency_ms: int,
    outcome: str,
    status_code: int | None = None,
    retryable: bool = False,
    retry_scheduled: bool = False,
    request_id: str = "",
    error_code: str = "",
) -> None:
    debug = node_debug.get()
    if debug is None:
        return
    events = debug.setdefault("provider_requests", [])
    if not isinstance(events, list) or len(events) >= MAX_DEBUG_REQUEST_EVENTS:
        return
    payload: dict[str, Any] = {
        "provider": provider,
        "operation": operation,
        "method": method.upper(),
        "url": _safe_url(url),
        "attempt": attempt,
        "max_attempts": max_attempts,
        "latency_ms": latency_ms,
        "outcome": outcome,
        "retryable": retryable,
        "retry_scheduled": retry_scheduled,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    if request_id:
        payload["request_id"] = request_id
    if error_code:
        payload["error_code"] = error_code
    events.append(payload)


class ProviderTransport:
    """Small sync HTTP transport used by generated v2 integration nodes."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str = "",
        default_headers: dict[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.default_headers = dict(default_headers or {})
        self.retry_policy = retry_policy or RetryPolicy()

    def url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        if not self.base_url:
            return path_or_url
        return urljoin(f"{self.base_url}/", path_or_url.lstrip("/"))

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        merged = dict(self.default_headers)
        merged.update(extra or {})
        return merged

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        operation: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        timeout: float = 30,
        retry_policy: RetryPolicy | None = None,
    ) -> Any:
        import requests

        policy = retry_policy or self.retry_policy
        attempts = max(1, policy.max_attempts)
        url = self.url(path_or_url)
        for attempt in range(1, attempts + 1):
            attempt_start = time.perf_counter()
            try:
                # SEC-1/SEC-3: every provider request — including ones whose host
                # is built from user-supplied credentials (self-hosted GitLab,
                # Supabase, WooCommerce, …) — goes through the shared SSRF guard,
                # which validates the target and re-validates every redirect hop.
                # UnsafeHttpTargetError is intentionally NOT caught here: an SSRF
                # attempt must fail fast, never silently retry.
                response = safe_request(
                    method,
                    url,
                    headers=self.headers(headers),
                    params=params or None,
                    json=json_body,
                    data=data,
                    timeout=timeout,
                    context=f"{self.provider} {operation}",
                )
            except requests.RequestException as exc:
                latency_ms = max(0, int((time.perf_counter() - attempt_start) * 1000))
                retry_scheduled = attempt < attempts
                _record_provider_request_event(
                    provider=self.provider,
                    operation=operation,
                    method=method,
                    url=url,
                    attempt=attempt,
                    max_attempts=attempts,
                    latency_ms=latency_ms,
                    outcome="exception",
                    retryable=True,
                    retry_scheduled=retry_scheduled,
                    error_code=type(exc).__name__,
                )
                if attempt >= attempts:
                    raise ProviderError(
                        provider=self.provider,
                        operation=operation,
                        status_code=None,
                        code=type(exc).__name__,
                        message=str(exc),
                        retryable=True,
                    ) from exc
                self._sleep_before_retry(policy, attempt)
                continue

            latency_ms = max(0, int((time.perf_counter() - attempt_start) * 1000))
            status_code = int(getattr(response, "status_code", 0) or 0)
            retryable = status_code in policy.retry_status_codes
            if 200 <= status_code < 300:
                _record_provider_request_event(
                    provider=self.provider,
                    operation=operation,
                    method=method,
                    url=url,
                    attempt=attempt,
                    max_attempts=attempts,
                    latency_ms=latency_ms,
                    outcome="success",
                    status_code=status_code,
                    retryable=retryable,
                    request_id=_request_id(getattr(response, "headers", {})),
                )
                return json_or_text(response)
            if retryable and attempt < attempts:
                _record_provider_request_event(
                    provider=self.provider,
                    operation=operation,
                    method=method,
                    url=url,
                    attempt=attempt,
                    max_attempts=attempts,
                    latency_ms=latency_ms,
                    outcome="retry_scheduled",
                    status_code=status_code,
                    retryable=True,
                    retry_scheduled=True,
                    request_id=_request_id(getattr(response, "headers", {})),
                    error_code=_error_code(response),
                )
                self._sleep_before_retry(policy, attempt, response)
                continue
            _record_provider_request_event(
                provider=self.provider,
                operation=operation,
                method=method,
                url=url,
                attempt=attempt,
                max_attempts=attempts,
                latency_ms=latency_ms,
                outcome="error",
                status_code=status_code or None,
                retryable=retryable,
                request_id=_request_id(getattr(response, "headers", {})),
                error_code=_error_code(response),
            )
            raise self.error_from_response(
                response,
                operation=operation,
                retryable=retryable,
            )
        raise AssertionError("unreachable provider transport state")

    def error_from_response(
        self,
        response: Any,
        *,
        operation: str,
        retryable: bool,
    ) -> ProviderError:
        status_code = int(getattr(response, "status_code", 0) or 0)
        return ProviderError(
            provider=self.provider,
            operation=operation,
            status_code=status_code or None,
            code=_error_code(response),
            message=_summary(response),
            retryable=retryable,
            request_id=_request_id(getattr(response, "headers", {})),
            response_body_summary=_summary(response),
        )

    def _sleep_before_retry(
        self,
        policy: RetryPolicy,
        attempt: int,
        response: Any = None,
    ) -> None:
        """Sleep before a retry, honouring Retry-After / x-ratelimit-reset when present."""
        delay = min(
            policy.max_backoff_seconds,
            policy.backoff_seconds * (2 ** max(0, attempt - 1)),
        )
        if response is not None:
            headers = getattr(response, "headers", {}) or {}
            for header in ("retry-after", "x-ratelimit-reset-after", "x-ratelimit-reset"):
                raw = headers.get(header) if hasattr(headers, "get") else None
                if raw:
                    try:
                        hint = float(raw)
                        # Some providers send an epoch timestamp instead of seconds.
                        # If the value looks like an epoch (> 1e9) convert to a delta.
                        if hint > 1_000_000_000:
                            hint = max(0.0, hint - time.time())
                        delay = min(policy.max_backoff_seconds, max(delay, hint))
                    except ValueError:
                        pass
                    break
        if delay > 0:
            time.sleep(delay)

    def paginate(
        self,
        method: str,
        path_or_url: str,
        *,
        operation: str,
        items_key: str = "items",
        next_token_key: str = "nextPageToken",
        next_token_param: str = "pageToken",
        next_link_key: str = "@odata.nextLink",
        max_pages: int = DEFAULT_MAX_PAGES,
        max_items: int = DEFAULT_MAX_ITEMS,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float = 30,
        retry_policy: RetryPolicy | None = None,
    ) -> list[Any]:
        """Collect all pages from a paginated provider endpoint.

        Supports both token-based pagination (Google style) and next-link
        pagination (Microsoft Graph style).  Returns all collected items up to
        ``max_items`` (default 10,000) — a warning is logged when truncated.

        ``items_key`` names the list field inside each response payload.
        When the response is itself a list, it is used directly.  When
        ``items_key`` is empty or ``"*"``, the entire response dict is
        appended rather than unpacking a nested list.
        """
        collected: list[Any] = []
        current_params = dict(params or {})
        current_url = path_or_url

        for _ in range(max_pages):
            # E-12: stop early if we've already hit the item cap.
            if len(collected) >= max_items:
                _logger.warning(
                    "paginate(%s): truncated at %d items (max_items=%d) — "
                    "use a filter or increase max_items to retrieve more (E-12)",
                    operation,
                    len(collected),
                    max_items,
                )
                break

            page = self.request(
                method,
                current_url,
                operation=operation,
                headers=headers,
                params=current_params,
                json_body=json_body,
                timeout=timeout,
                retry_policy=retry_policy,
            )

            if isinstance(page, list):
                collected.extend(page)
                break
            elif isinstance(page, dict):
                if items_key and items_key != "*":
                    page_items = page.get(items_key) or []
                    if isinstance(page_items, list):
                        collected.extend(page_items)
                    else:
                        collected.append(page_items)
                else:
                    collected.append(page)

                # Microsoft Graph: @odata.nextLink is a full URL
                next_link = page.get(next_link_key) if next_link_key else None
                if next_link and isinstance(next_link, str):
                    current_url = next_link
                    current_params = {}
                    continue

                # Google / token-based pagination
                next_token = page.get(next_token_key) if next_token_key else None
                if next_token and isinstance(next_token, str):
                    current_params = {**current_params, next_token_param: next_token}
                    continue

                # No continuation token → done
                break
            else:
                # Unexpected response shape; stop paging
                collected.append(page)
                break

        return collected[:max_items]
