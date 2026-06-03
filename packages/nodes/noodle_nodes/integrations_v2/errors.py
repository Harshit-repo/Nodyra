"""Structured provider errors for v2 integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderError(RuntimeError):
    provider: str
    operation: str
    status_code: int | None
    code: str
    message: str
    retryable: bool = False
    request_id: str = ""
    response_body_summary: str = ""

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "operation": self.operation,
            "status_code": self.status_code,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "response_body_summary": self.response_body_summary,
        }

    def __str__(self) -> str:
        status = f" HTTP {self.status_code}" if self.status_code is not None else ""
        request = f" request_id={self.request_id}" if self.request_id else ""
        return f"{self.provider}.{self.operation}{status}: {self.message}{request}"
