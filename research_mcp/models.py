"""Typed domain objects shared by sources, persistence, and MCP contracts.

The objects in this module have no I/O side effects. Persistent entities use
immutable dataclasses so callers cannot accidentally mutate repository state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


def _aware(value: datetime | None, name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be timezone-aware")


def _digest(value: str | None, name: str) -> None:
    if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} must be a 64 lowercase hexadecimal character digest")


@dataclass(frozen=True, slots=True)
class Work:
    work_id: str
    title: str
    authors: tuple[str, ...] = ()
    abstract: str | None = None
    published_at: datetime | None = None
    work_type: str | None = None
    venue: str | None = None
    language: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.work_id or not self.title:
            raise ValueError("work_id and title are required")
        for name in ("published_at", "created_at", "updated_at"):
            _aware(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class Identifier:
    work_id: str
    scheme: str
    value: str
    provider: str


@dataclass(frozen=True, slots=True)
class Document:
    document_id: str
    work_id: str | None
    canonical_url: str | None = None
    media_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    local_object_key: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _digest(self.sha256, "sha256")
        _aware(self.created_at, "created_at")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")


@dataclass(frozen=True, slots=True)
class Provenance:
    source: str
    source_record_id: str
    retrieved_at: datetime
    canonical_url: str | None = None
    cache_status: Literal["hit", "miss", "stale"] = "miss"
    untrusted_content: bool = True

    def __post_init__(self) -> None:
        _aware(self.retrieved_at, "retrieved_at")


@dataclass(frozen=True, slots=True)
class Retrieval:
    retrieval_id: str
    document_id: str
    provider: str
    retrieved_at: datetime
    final_url: str
    http_status: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    license: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _aware(self.retrieved_at, "retrieved_at")


@dataclass(frozen=True, slots=True)
class Extraction:
    extraction_id: str
    document_id: str
    parser: str
    parser_version: str
    extracted_at: datetime
    page_count: int | None = None
    quality: float | None = None
    ocr_used: bool = False


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    extraction_id: str
    chunk_index: int
    content: str
    page_start: int | None = None
    page_end: int | None = None
    heading: str | None = None
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_record_id: str
    provider: str
    provider_id: str
    payload_digest: str
    retrieved_at: datetime
    adapter_version: str
    work_id: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _digest(self.payload_digest, "payload_digest")
        _aware(self.retrieved_at, "retrieved_at")


@dataclass(frozen=True, slots=True)
class Collection:
    collection_id: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CollectionItem:
    collection_id: str
    work_id: str
    position: int = 0
    note: str | None = None
    added_at: datetime | None = None


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    kind: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    parameters: dict[str, Any] = field(default_factory=dict)
    progress: float = 0.0
    cursor: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if not 0 <= self.progress <= 1:
            raise ValueError("progress must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class WorkSummary:
    document_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    identifiers: dict[str, str] = field(default_factory=dict)
    abstract_excerpt: str | None = None
    provenance: Provenance | None = None
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    sources: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    cursor: str | None = None
    page_size: int = 10
    detail_level: Literal["compact", "standard"] = "compact"

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query cannot be empty")
        if not 1 <= self.page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class SearchPage:
    items: list[WorkSummary] = field(default_factory=list)
    next_cursor: str | None = None
    source: str | None = None
    partial: bool = False
    warnings: list[str] = field(default_factory=list)
    request_id: str | None = None
    retrieved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DocumentCandidate:
    source: str
    source_id: str
    url: str
    media_type: str | None = None
    expected_bytes: int | None = None
    license: str | None = None
    credential_scope: Literal["none", "source_origin"] = "none"

