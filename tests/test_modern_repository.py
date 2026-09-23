import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from research_mcp.models import (
    Collection,
    Document,
    Identifier,
    Job,
    JobStatus,
    Retrieval,
    SourceRecord,
    Work,
)
from research_mcp.repository import CURRENT_SCHEMA_VERSION, SQLiteRepository


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def make_work(work_id="work-1", title="A paper"):
    return Work(work_id=work_id, title=title, authors=("Ada",), created_at=NOW, updated_at=NOW)


@pytest.fixture
def repository(tmp_path):
    repo = SQLiteRepository(tmp_path / "research.db")
    repo.initialize()
    yield repo
    repo.close()


def test_import_and_constructor_do_not_create_database(tmp_path):
    path = tmp_path / "not-created.db"
    repo = SQLiteRepository(path)
    assert not path.exists()
    repo.close()
    assert not path.exists()


def test_initialize_creates_versioned_schema(repository):
    assert repository.schema_version == CURRENT_SCHEMA_VERSION
    tables = repository.table_names()
    assert {
        "works",
        "identifiers",
        "documents",
        "retrievals",
        "extractions",
        "document_chunks",
        "source_records",
        "collections",
        "collection_items",
        "jobs",
    } <= tables


def test_work_document_identifier_and_provenance_crud(repository):
    work = make_work()
    repository.upsert_work(work)
    repository.add_identifier(Identifier("work-1", "doi", "10.1/example", "crossref"))
    document = Document(
        document_id="doc-1",
        work_id="work-1",
        canonical_url="https://example.test/paper.pdf",
        media_type="application/pdf",
        sha256="a" * 64,
        size_bytes=123,
        local_object_key="objects/a.pdf",
        created_at=NOW,
    )
    repository.upsert_document(document)
    retrieval = Retrieval(
        retrieval_id="ret-1",
        document_id="doc-1",
        provider="crossref",
        retrieved_at=NOW,
        final_url="https://example.test/paper.pdf",
        http_status=200,
        license="CC-BY-4.0",
        provenance={"request_id": "req-1"},
    )
    repository.add_retrieval(retrieval)

    assert repository.get_work("work-1") == work
    assert repository.list_identifiers("work-1") == [
        Identifier("work-1", "doi", "10.1/example", "crossref")
    ]
    assert repository.get_document("doc-1") == document
    assert repository.list_documents("work-1") == [document]
    assert repository.list_retrievals("doc-1") == [retrieval]

    updated = replace(work, title="Revised", updated_at=replace_time(NOW, second=6))
    repository.upsert_work(updated)
    assert repository.get_work("work-1") == updated
    assert repository.delete_work("work-1") is True
    assert repository.get_work("work-1") is None
    assert repository.get_document("doc-1") is None


def replace_time(value, **changes):
    return value.replace(**changes)


def test_source_collection_and_job_crud(repository):
    repository.upsert_work(make_work())
    record = SourceRecord(
        source_record_id="src-1",
        provider="openalex",
        provider_id="W1",
        payload_digest="b" * 64,
        retrieved_at=NOW,
        adapter_version="1",
        work_id="work-1",
        raw_payload={"id": "W1"},
    )
    repository.upsert_source_record(record)
    assert repository.get_source_record("src-1") == record

    collection = Collection("col-1", "Review", "Evidence", NOW, NOW)
    repository.upsert_collection(collection)
    repository.add_collection_item("col-1", "work-1", position=2, note="included")
    assert repository.get_collection("col-1") == collection
    assert repository.list_collection_items("col-1")[0].note == "included"

    job = Job(
        job_id="job-1",
        kind="index",
        status=JobStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
        parameters={"document_id": "doc-1"},
    )
    repository.upsert_job(job)
    running = replace(job, status=JobStatus.RUNNING, progress=0.5)
    repository.upsert_job(running)
    assert repository.get_job("job-1") == running
    assert repository.list_jobs(status=JobStatus.RUNNING) == [running]


def test_transaction_rolls_back_all_writes(repository):
    with pytest.raises(RuntimeError):
        with repository.transaction():
            repository.upsert_work(make_work())
            repository.upsert_collection(Collection("c", "C", None, NOW, NOW))
            raise RuntimeError("fail")

    assert repository.get_work("work-1") is None
    assert repository.get_collection("c") is None


def test_migration_from_legacy_preserves_tables_and_reconciles(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE papers (
            arxiv_id TEXT PRIMARY KEY, title TEXT, authors TEXT, abstract TEXT,
            categories TEXT, published TEXT, pdf_path TEXT, total_pages INTEGER,
            indexed_at TEXT, content_hash TEXT
        );
        CREATE TABLE chunks (
            chunk_id INTEGER PRIMARY KEY, arxiv_id TEXT, page_start INTEGER,
            page_end INTEGER, chunk_index INTEGER, heading TEXT, content TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO papers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2401.00001", "Legacy", '["Grace"]', "Abstract", '["cs.AI"]',
         "2024-01-01", "/tmp/a.pdf", 1, NOW.isoformat(), "oldhash"),
    )
    conn.execute(
        "INSERT INTO chunks VALUES (1, '2401.00001', 1, 1, 0, 'Intro', 'legacy text')"
    )
    conn.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    conn.close()

    # Construction is read-only. Explicit initialization performs the additive migration.
    repo = SQLiteRepository(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    report = repo.initialize()

    assert report.legacy_papers == report.migrated_works == 1
    assert report.legacy_chunks == report.migrated_chunks == 1
    assert repo.get_work("legacy:arxiv:2401.00001").title == "Legacy"
    assert repo.list_identifiers("legacy:arxiv:2401.00001")[0].value == "2401.00001"
    assert repo.get_document("legacy:arxiv:2401.00001").sha256 is None
    # Legacy readers remain valid during the compatibility window.
    assert repo.connection.execute("SELECT title FROM papers").fetchone()[0] == "Legacy"
    assert repo.connection.execute("SELECT content FROM chunks").fetchone()[0] == "legacy text"
    repo.close()


def test_failed_migration_rolls_back_schema_and_data(tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    repo = SQLiteRepository(path)

    def fail(_connection):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(repo, "_migrate_legacy", fail)
    with pytest.raises(RuntimeError, match="migration failed"):
        repo.initialize()

    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='works'"
    ).fetchone()[0] == 0
    conn.close()
    repo.close()


def test_models_validate_security_and_state_invariants():
    with pytest.raises(ValueError, match="64 lowercase"):
        Document("d", None, sha256="short")
    with pytest.raises(ValueError, match="timezone-aware"):
        Work("w", "T", created_at=datetime(2026, 1, 1), updated_at=NOW)
    with pytest.raises(ValueError, match="between 0 and 1"):
        Job("j", "x", JobStatus.PENDING, NOW, NOW, progress=1.1)

