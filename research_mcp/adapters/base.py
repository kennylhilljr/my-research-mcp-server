"""Shared contracts and safety checks for external source adapters."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from research_mcp.models import Provenance, SearchPage, SearchRequest, WorkSummary

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PAGE_SIZE = 50


class AdapterError(RuntimeError):
    """A bounded, provider-neutral adapter failure."""


class SourceAdapter(Protocol):
    name: str

    async def search(self, request: SearchRequest) -> SearchPage: ...

    async def get_work(self, source_id: str) -> WorkSummary: ...


def encode_cursor(source: str, state: dict[str, Any]) -> str:
    envelope = json.dumps(
        {"source": source, "state": state}, separators=(",", ":"), sort_keys=True
    ).encode()
    return base64.urlsafe_b64encode(envelope).decode().rstrip("=")


def decode_cursor(cursor: str | None, source: str) -> dict[str, Any]:
    if cursor is None:
        return {}
    if not isinstance(cursor, str) or len(cursor) > 2048:
        raise ValueError("invalid cursor")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        envelope = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if (
        not isinstance(envelope, dict)
        or envelope.get("source") != source
        or not isinstance(envelope.get("state"), dict)
    ):
        raise ValueError("cursor does not belong to this source")
    return envelope["state"]


def parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if len(normalized) == 4 and normalized.isdigit():
        normalized += "-01-01"
    elif len(normalized) == 7:
        normalized += "-01"
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def digest_id(source: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{source}:{source_id}".encode()).hexdigest()
    return f"{source}:{digest}"


class BaseAdapter:
    name = "base"
    adapter_version = "1"

    def __init__(
        self,
        client: Any,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if timeout <= 0 or max_response_bytes <= 0:
            raise ValueError("timeout and max_response_bytes must be positive")
        self.client = client
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def validate_request(self, request: SearchRequest) -> dict[str, Any]:
        if not isinstance(request.query, str) or not request.query.strip():
            raise ValueError("query must not be empty")
        if len(request.query) > 500:
            raise ValueError("query must not exceed 500 characters")
        if not 1 <= request.page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if not isinstance(request.filters, dict):
            raise ValueError("filters must be an object")
        return decode_cursor(request.cursor, self.name)

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self.client.get(url, params=params or {}, timeout=self.timeout)
        except Exception as exc:
            raise AdapterError(f"{self.name} request failed: {exc}") from exc
        status = getattr(response, "status_code", 0)
        if not 200 <= status < 300:
            raise AdapterError(f"{self.name} returned status {status}")
        content = getattr(response, "content", b"")
        if len(content) > self.max_response_bytes:
            raise AdapterError(f"{self.name} response exceeded byte limit")
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise AdapterError(f"{self.name} returned invalid JSON") from exc

    def provenance(self, source_id: str, url: str | None = None) -> Provenance:
        return Provenance(
            source=self.name,
            source_record_id=source_id,
            canonical_url=url,
            retrieved_at=datetime.now(timezone.utc),
            cache_status="miss",
            untrusted_content=True,
        )

    def page(
        self,
        items: list[WorkSummary],
        *,
        next_state: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        partial: bool = False,
    ) -> SearchPage:
        return SearchPage(
            items=items,
            next_cursor=encode_cursor(self.name, next_state) if next_state else None,
            source=self.name,
            partial=partial,
            warnings=warnings or [],
            request_id=str(uuid4()),
            retrieved_at=datetime.now(timezone.utc),
        )
