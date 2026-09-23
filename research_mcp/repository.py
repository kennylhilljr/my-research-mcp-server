"""Versioned SQLite persistence for normalized research domain objects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import (
    Collection,
    CollectionItem,
    Document,
    Identifier,
    Job,
    JobStatus,
    Retrieval,
    SourceRecord,
    Work,
)

CURRENT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class MigrationReport:
    from_version: int
    to_version: int
    legacy_papers: int = 0
    migrated_works: int = 0
    legacy_chunks: int = 0
    migrated_chunks: int = 0


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class SQLiteRepository:
    """Thread-safe repository whose database is opened only by ``initialize``.

    Construction and module import never create or mutate a database. Migrations
    are explicit, additive, and transactional; legacy ``papers`` and ``chunks``
    remain intact for old readers.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._transaction_depth = 0

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("repository is not initialized")
        return self._connection

    @property
    def schema_version(self) -> int:
        row = self.connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def table_names(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
        return {row[0] for row in rows}

    def initialize(self) -> MigrationReport:
        with self._lock:
            if self._connection is not None:
                return MigrationReport(self.schema_version, self.schema_version)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self._connection = conn
            try:
                from_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if from_version > CURRENT_SCHEMA_VERSION:
                    raise RuntimeError(
                        f"database schema {from_version} is newer than supported "
                        f"schema {CURRENT_SCHEMA_VERSION}"
                    )
                conn.execute("BEGIN IMMEDIATE")
                self._create_schema(conn)
                report = self._migrate_legacy(conn)
                conn.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
                conn.execute("COMMIT")
                return MigrationReport(from_version, CURRENT_SCHEMA_VERSION, *report)
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                if self._connection.in_transaction:
                    self._connection.rollback()
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "SQLiteRepository":
        self.initialize()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator["SQLiteRepository"]:
        with self._lock:
            conn = self.connection
            outermost = self._transaction_depth == 0
            savepoint = f"repository_{self._transaction_depth}"
            if outermost:
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute(f"SAVEPOINT {savepoint}")
            self._transaction_depth += 1
            try:
                yield self
            except Exception:
                self._transaction_depth -= 1
                if outermost:
                    conn.execute("ROLLBACK")
                else:
                    conn.execute(f"ROLLBACK TO {savepoint}")
                    conn.execute(f"RELEASE {savepoint}")
                raise
            else:
                self._transaction_depth -= 1
                if outermost:
                    conn.execute("COMMIT")
                else:
                    conn.execute(f"RELEASE {savepoint}")

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS works (
                work_id TEXT PRIMARY KEY, title TEXT NOT NULL, authors_json TEXT NOT NULL,
                abstract TEXT, published_at TEXT, work_type TEXT, venue TEXT, language TEXT,
                created_at TEXT, updated_at TEXT, extensions_json TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS identifiers (
                work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
                scheme TEXT NOT NULL, value TEXT NOT NULL, provider TEXT NOT NULL,
                PRIMARY KEY (scheme, value, provider))""",
            "CREATE INDEX IF NOT EXISTS identifiers_work_idx ON identifiers(work_id)",
            """CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                work_id TEXT REFERENCES works(work_id) ON DELETE CASCADE,
                canonical_url TEXT, media_type TEXT, sha256 TEXT, size_bytes INTEGER,
                local_object_key TEXT, created_at TEXT)""",
            "CREATE INDEX IF NOT EXISTS documents_work_idx ON documents(work_id)",
            """CREATE TABLE IF NOT EXISTS retrievals (
                retrieval_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                provider TEXT NOT NULL, retrieved_at TEXT NOT NULL, http_status INTEGER,
                etag TEXT, last_modified TEXT, license TEXT, final_url TEXT NOT NULL,
                provenance_json TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS extractions (
                extraction_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                parser TEXT NOT NULL, parser_version TEXT NOT NULL, extracted_at TEXT NOT NULL,
                page_count INTEGER, quality REAL, ocr_used INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS document_chunks (
                chunk_id TEXT PRIMARY KEY,
                extraction_id TEXT NOT NULL REFERENCES extractions(extraction_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL, page_start INTEGER, page_end INTEGER,
                heading TEXT, content TEXT NOT NULL, content_sha256 TEXT,
                UNIQUE(extraction_id, chunk_index))""",
            """CREATE TABLE IF NOT EXISTS source_records (
                source_record_id TEXT PRIMARY KEY,
                work_id TEXT REFERENCES works(work_id) ON DELETE SET NULL,
                provider TEXT NOT NULL, provider_id TEXT NOT NULL, payload_digest TEXT NOT NULL,
                retrieved_at TEXT NOT NULL, adapter_version TEXT NOT NULL,
                raw_payload_json TEXT NOT NULL,
                UNIQUE(provider, provider_id, payload_digest))""",
            """CREATE TABLE IF NOT EXISTS collections (
                collection_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS collection_items (
                collection_id TEXT NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
                work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0, note TEXT, added_at TEXT,
                PRIMARY KEY(collection_id, work_id))""",
            """CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                parameters_json TEXT NOT NULL, progress REAL NOT NULL,
                cursor TEXT, result_json TEXT, error TEXT)""",
        )
        for statement in statements:
            conn.execute(statement)

    def _migrate_legacy(self, conn: sqlite3.Connection) -> tuple[int, int, int, int]:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "papers" not in names:
            return (0, 0, 0, 0)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
        if "arxiv_id" not in columns:
            return (0, 0, 0, 0)
        papers = conn.execute("SELECT * FROM papers").fetchall()
        chunk_count = 0
        migrated_chunks = 0
        for paper in papers:
            legacy_id = paper["arxiv_id"]
            scheme, value = self._legacy_identifier(legacy_id)
            work_id = f"legacy:{scheme}:{value}"
            timestamp = paper["indexed_at"] or datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT OR IGNORE INTO works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (work_id, paper["title"] or legacy_id, paper["authors"] or "[]",
                 paper["abstract"], paper["published"], "paper", None, None,
                 timestamp, timestamp, _json({"legacy_categories": _loads(paper["categories"], [])})),
            )
            conn.execute(
                "INSERT OR IGNORE INTO identifiers VALUES (?, ?, ?, ?)",
                (work_id, scheme, value, "legacy"),
            )
            document_id = work_id
            conn.execute(
                """INSERT OR IGNORE INTO documents
                   (document_id, work_id, media_type, local_object_key, created_at)
                   VALUES (?, ?, 'application/pdf', ?, ?)""",
                (document_id, work_id, paper["pdf_path"], timestamp),
            )
            if "chunks" in names:
                rows = conn.execute(
                    "SELECT * FROM chunks WHERE arxiv_id=? ORDER BY chunk_index", (legacy_id,)
                ).fetchall()
                chunk_count += len(rows)
                if rows:
                    extraction_id = f"legacy:{scheme}:{value}:extraction"
                    conn.execute(
                        """INSERT OR IGNORE INTO extractions
                           VALUES (?, ?, 'legacy', '1', ?, ?, NULL, 0)""",
                        (extraction_id, document_id, timestamp, paper["total_pages"]),
                    )
                    for row in rows:
                        digest = hashlib.sha256(row["content"].encode()).hexdigest()
                        conn.execute(
                            """INSERT OR IGNORE INTO document_chunks VALUES
                               (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (f"legacy:{row['chunk_id']}", extraction_id, row["chunk_index"],
                             row["page_start"], row["page_end"], row["heading"],
                             row["content"], digest),
                        )
                        migrated_chunks += 1
        return (len(papers), len(papers), chunk_count, migrated_chunks)

    @staticmethod
    def _legacy_identifier(value: str) -> tuple[str, str]:
        for prefix, scheme in (("doi:", "doi"), ("core:", "core"), ("iam:", "iam"),
                               ("arxiv:", "arxiv")):
            if value.lower().startswith(prefix):
                return scheme, value[len(prefix):]
        return "arxiv", value

    def upsert_work(self, work: Work) -> None:
        with self._lock:
            self.connection.execute(
                """INSERT INTO works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(work_id) DO UPDATE SET title=excluded.title,
                   authors_json=excluded.authors_json, abstract=excluded.abstract,
                   published_at=excluded.published_at, work_type=excluded.work_type,
                   venue=excluded.venue, language=excluded.language,
                   updated_at=excluded.updated_at, extensions_json=excluded.extensions_json""",
                (work.work_id, work.title, _json(work.authors), work.abstract,
                 _iso(work.published_at), work.work_type, work.venue, work.language,
                 _iso(work.created_at), _iso(work.updated_at), _json(work.extensions)),
            )

    def get_work(self, work_id: str) -> Work | None:
        row = self.connection.execute("SELECT * FROM works WHERE work_id=?", (work_id,)).fetchone()
        return self._work(row) if row else None

    def list_works(self, limit: int = 100, offset: int = 0) -> list[Work]:
        rows = self.connection.execute(
            "SELECT * FROM works ORDER BY updated_at DESC, work_id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._work(row) for row in rows]

    @staticmethod
    def _work(row: sqlite3.Row) -> Work:
        return Work(row["work_id"], row["title"], tuple(_loads(row["authors_json"], [])),
                    row["abstract"], _datetime(row["published_at"]), row["work_type"],
                    row["venue"], row["language"], _datetime(row["created_at"]),
                    _datetime(row["updated_at"]), _loads(row["extensions_json"], {}))

    def delete_work(self, work_id: str) -> bool:
        with self._lock:
            return self.connection.execute("DELETE FROM works WHERE work_id=?", (work_id,)).rowcount > 0

    def add_identifier(self, value: Identifier) -> None:
        self.connection.execute(
            """INSERT INTO identifiers VALUES (?, ?, ?, ?)
               ON CONFLICT(scheme,value,provider) DO UPDATE SET work_id=excluded.work_id""",
            (value.work_id, value.scheme, value.value, value.provider),
        )

    def list_identifiers(self, work_id: str) -> list[Identifier]:
        rows = self.connection.execute(
            "SELECT * FROM identifiers WHERE work_id=? ORDER BY scheme,value,provider", (work_id,)
        ).fetchall()
        return [Identifier(r["work_id"], r["scheme"], r["value"], r["provider"]) for r in rows]

    def upsert_document(self, value: Document) -> None:
        self.connection.execute(
            """INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id) DO UPDATE SET work_id=excluded.work_id,
               canonical_url=excluded.canonical_url, media_type=excluded.media_type,
               sha256=excluded.sha256, size_bytes=excluded.size_bytes,
               local_object_key=excluded.local_object_key, created_at=excluded.created_at""",
            (value.document_id, value.work_id, value.canonical_url, value.media_type,
             value.sha256, value.size_bytes, value.local_object_key, _iso(value.created_at)),
        )

    def get_document(self, document_id: str) -> Document | None:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE document_id=?", (document_id,)
        ).fetchone()
        return self._document(row) if row else None

    def list_documents(self, work_id: str | None = None) -> list[Document]:
        if work_id is None:
            rows = self.connection.execute("SELECT * FROM documents ORDER BY document_id").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM documents WHERE work_id=? ORDER BY document_id", (work_id,)
            ).fetchall()
        return [self._document(r) for r in rows]

    @staticmethod
    def _document(row: sqlite3.Row) -> Document:
        return Document(row["document_id"], row["work_id"], row["canonical_url"],
                        row["media_type"], row["sha256"], row["size_bytes"],
                        row["local_object_key"], _datetime(row["created_at"]))

    def add_retrieval(self, value: Retrieval) -> None:
        self.connection.execute(
            "INSERT INTO retrievals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (value.retrieval_id, value.document_id, value.provider, _iso(value.retrieved_at),
             value.http_status, value.etag, value.last_modified, value.license,
             value.final_url, _json(value.provenance)),
        )

    def list_retrievals(self, document_id: str) -> list[Retrieval]:
        rows = self.connection.execute(
            "SELECT * FROM retrievals WHERE document_id=? ORDER BY retrieved_at,retrieval_id",
            (document_id,),
        ).fetchall()
        return [Retrieval(r["retrieval_id"], r["document_id"], r["provider"],
                          _datetime(r["retrieved_at"]), r["final_url"], r["http_status"],
                          r["etag"], r["last_modified"], r["license"],
                          _loads(r["provenance_json"], {})) for r in rows]

    def upsert_source_record(self, value: SourceRecord) -> None:
        self.connection.execute(
            """INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_record_id) DO UPDATE SET work_id=excluded.work_id,
               provider=excluded.provider, provider_id=excluded.provider_id,
               payload_digest=excluded.payload_digest, retrieved_at=excluded.retrieved_at,
               adapter_version=excluded.adapter_version,
               raw_payload_json=excluded.raw_payload_json""",
            (value.source_record_id, value.work_id, value.provider, value.provider_id,
             value.payload_digest, _iso(value.retrieved_at), value.adapter_version,
             _json(value.raw_payload)),
        )

    def get_source_record(self, record_id: str) -> SourceRecord | None:
        r = self.connection.execute(
            "SELECT * FROM source_records WHERE source_record_id=?", (record_id,)
        ).fetchone()
        return (SourceRecord(r["source_record_id"], r["provider"], r["provider_id"],
                             r["payload_digest"], _datetime(r["retrieved_at"]),
                             r["adapter_version"], r["work_id"],
                             _loads(r["raw_payload_json"], {})) if r else None)

    def upsert_collection(self, value: Collection) -> None:
        self.connection.execute(
            """INSERT INTO collections VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(collection_id) DO UPDATE SET name=excluded.name,
               description=excluded.description, updated_at=excluded.updated_at""",
            (value.collection_id, value.name, value.description,
             _iso(value.created_at), _iso(value.updated_at)),
        )

    def get_collection(self, collection_id: str) -> Collection | None:
        r = self.connection.execute(
            "SELECT * FROM collections WHERE collection_id=?", (collection_id,)
        ).fetchone()
        return (Collection(r["collection_id"], r["name"], r["description"],
                           _datetime(r["created_at"]), _datetime(r["updated_at"])) if r else None)

    def list_collections(self) -> list[Collection]:
        rows = self.connection.execute("SELECT * FROM collections ORDER BY name").fetchall()
        return [Collection(r["collection_id"], r["name"], r["description"],
                           _datetime(r["created_at"]), _datetime(r["updated_at"])) for r in rows]

    def add_collection_item(self, collection_id: str, work_id: str, *, position: int = 0,
                            note: str | None = None, added_at: datetime | None = None) -> None:
        self.connection.execute(
            """INSERT INTO collection_items VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(collection_id,work_id) DO UPDATE SET
               position=excluded.position,note=excluded.note,added_at=excluded.added_at""",
            (collection_id, work_id, position, note, _iso(added_at)),
        )

    def list_collection_items(self, collection_id: str) -> list[CollectionItem]:
        rows = self.connection.execute(
            """SELECT * FROM collection_items WHERE collection_id=?
               ORDER BY position,work_id""", (collection_id,)
        ).fetchall()
        return [CollectionItem(r["collection_id"], r["work_id"], r["position"], r["note"],
                               _datetime(r["added_at"])) for r in rows]

    def upsert_job(self, value: Job) -> None:
        self.connection.execute(
            """INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET kind=excluded.kind,status=excluded.status,
               updated_at=excluded.updated_at,parameters_json=excluded.parameters_json,
               progress=excluded.progress,cursor=excluded.cursor,
               result_json=excluded.result_json,error=excluded.error""",
            (value.job_id, value.kind, value.status.value, _iso(value.created_at),
             _iso(value.updated_at), _json(value.parameters), value.progress, value.cursor,
             _json(value.result) if value.result is not None else None, value.error),
        )

    def get_job(self, job_id: str) -> Job | None:
        row = self.connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list_jobs(self, status: JobStatus | None = None) -> list[Job]:
        if status is None:
            rows = self.connection.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC", (status.value,)
            ).fetchall()
        return [self._job(row) for row in rows]

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(row["job_id"], row["kind"], JobStatus(row["status"]),
                   _datetime(row["created_at"]), _datetime(row["updated_at"]),
                   _loads(row["parameters_json"], {}), row["progress"], row["cursor"],
                   _loads(row["result_json"], None), row["error"])

