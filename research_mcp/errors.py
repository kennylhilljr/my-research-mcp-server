"""Stable public error taxonomy for tools and resources."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"


_RETRYABLE = {
    ErrorCode.RATE_LIMITED,
    ErrorCode.UPSTREAM_TIMEOUT,
    ErrorCode.UPSTREAM_ERROR,
}


class ResearchError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after_ms: int | None = None,
        field: str | None = None,
        provider: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = code in _RETRYABLE if retryable is None else retryable
        self.retry_after_ms = retry_after_ms
        self.field = field
        self.provider = provider
        self.correlation_id = correlation_id or str(uuid.uuid4())

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "correlation_id": self.correlation_id,
        }
        for name in ("retry_after_ms", "field", "provider"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result

