"""Tests for the My Research MCP server."""

import os
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

# Import from the server module at repo root
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import (
    PaperIndex,
    _clamp,
    _dspace8_parse_item,
    _dspace_parse_item,
    _ensure_read_only_sql,
    _extract_links,
    _extract_main_text,
    _get_download_dir,
    _iam_docs_sites_query,
    _index_downloaded_pdf,
    _make_rate_limiter,
    _openalex_abstract,
    _parse_core_work,
    _parse_crossref,
    _parse_feed,
    _parse_openalex_work,
    _parse_sitemap,
    _safe_json_loads,
    chunk_pages,
    compute_content_hash,
    detect_heading,
    extract_text_from_pdf,
    fetch_cloud_doc_page,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """Fresh PaperIndex for each test."""
    return PaperIndex(str(tmp_path / "test.db"))


@pytest.fixture
def sample_meta():
    return {
        "arxiv_id": "2301.12345v1",
        "title": "Attention Is All You Need",
        "authors": ["Vaswani", "Shazeer", "Parmar"],
        "summary": "We propose the Transformer architecture.",
        "categories": ["cs.CL", "cs.LG"],
        "published": "2017-06-12T00:00:00Z",
    }


@pytest.fixture
def sample_chunks():
    return [
        {
            "content": "The dominant sequence transduction models are based on "
                       "complex recurrent neural networks.",
            "page_start": 1,
            "page_end": 1,
            "heading": "1 Introduction",
        },
        {
            "content": "Multi-head attention allows the model to jointly attend "
                       "to information from different subspaces.",
            "page_start": 3,
            "page_end": 3,
            "heading": "3.2 Multi-Head Attention",
        },
        {
            "content": "BLEU score of 28.4 on WMT 2014 English-German translation.",
            "page_start": 5,
            "page_end": 5,
            "heading": "5 Results",
        },
        {
            "content": "Policy gradient methods and reinforcement learning "
                       "provide optimization strategies.",
            "page_start": 6,
            "page_end": 6,
            "heading": "6 Discussion",
        },
    ]


@pytest.fixture
def indexed_db(db, sample_meta, sample_chunks):
    """DB with one paper already indexed."""
    db.upsert_paper(sample_meta, sample_chunks, "/tmp/fake.pdf", 8, "abc123")
    return db


# ── PaperIndex: Schema & Indexing ────────────────────────────────────────

class TestPaperIndexing:
    def test_empty_db_stats(self, db):
        stats = db.get_stats()
        assert stats["total_papers"] == 0
        assert stats["total_chunks"] == 0

    def test_upsert_paper(self, indexed_db):
        stats = indexed_db.get_stats()
        assert stats["total_papers"] == 1
        assert stats["total_chunks"] == 4
        assert stats["total_pages"] == 8

    def test_is_indexed(self, indexed_db):
        assert indexed_db.is_indexed("2301.12345v1")
        assert not indexed_db.is_indexed("9999.99999v1")

    def test_needs_reindex_new_paper(self, db):
        assert db.needs_reindex("2301.12345v1", "anyhash")

    def test_needs_reindex_same_hash(self, indexed_db):
        assert not indexed_db.needs_reindex("2301.12345v1", "abc123")

    def test_needs_reindex_different_hash(self, indexed_db):
        assert indexed_db.needs_reindex("2301.12345v1", "different")

    def test_upsert_replaces_existing(self, indexed_db, sample_meta):
        new_chunks = [
            {"content": "Replaced content.", "page_start": 1,
             "page_end": 1, "heading": ""}
        ]
        indexed_db.upsert_paper(
            sample_meta, new_chunks, "/tmp/new.pdf", 1, "new_hash"
        )
        stats = indexed_db.get_stats()
        assert stats["total_chunks"] == 1

    def test_remove_paper(self, indexed_db):
        indexed_db.remove_paper("2301.12345v1")
        assert not indexed_db.is_indexed("2301.12345v1")
        assert indexed_db.get_stats()["total_papers"] == 0

    def test_list_papers(self, indexed_db):
        papers = indexed_db.list_papers()
        assert len(papers) == 1
        assert papers[0]["arxiv_id"] == "2301.12345v1"
        assert papers[0]["chunk_count"] == 4

    def test_remove_nonexistent_paper(self, db):
        """Removing a paper that doesn't exist should not raise."""
        db.remove_paper("0000.00000v1")
        assert db.get_stats()["total_papers"] == 0

    def test_upsert_updates_pdf_path(self, indexed_db, sample_meta, sample_chunks):
        indexed_db.upsert_paper(
            sample_meta, sample_chunks, "/tmp/updated.pdf", 8, "newhash"
        )
        papers = indexed_db.list_papers()
        assert papers[0]["pdf_path"] == "/tmp/updated.pdf"

    def test_upsert_preserves_content_hash(self, indexed_db, sample_meta, sample_chunks):
        indexed_db.upsert_paper(
            sample_meta, sample_chunks, "/tmp/fake.pdf", 8, "new_hash"
        )
        assert not indexed_db.needs_reindex("2301.12345v1", "new_hash")

    def test_multiple_papers(self, db, sample_meta, sample_chunks):
        db.upsert_paper(sample_meta, sample_chunks, "/tmp/a.pdf", 8, "h1")
        meta2 = dict(sample_meta, arxiv_id="2402.00001v1", title="Second Paper")
        db.upsert_paper(meta2, sample_chunks, "/tmp/b.pdf", 8, "h2")
        stats = db.get_stats()
        assert stats["total_papers"] == 2
        assert stats["total_chunks"] == 8

    def test_list_papers_returns_all(self, db, sample_meta, sample_chunks):
        db.upsert_paper(sample_meta, sample_chunks, "/tmp/a.pdf", 8, "h1")
        meta2 = dict(sample_meta, arxiv_id="2402.00001v1")
        db.upsert_paper(meta2, sample_chunks[:1], "/tmp/b.pdf", 1, "h2")
        papers = db.list_papers()
        assert len(papers) == 2
        ids = {p["arxiv_id"] for p in papers}
        assert ids == {"2301.12345v1", "2402.00001v1"}

    def test_stats_total_pages(self, db, sample_meta, sample_chunks):
        db.upsert_paper(sample_meta, sample_chunks, "/tmp/a.pdf", 10, "h1")
        stats = db.get_stats()
        assert stats["total_pages"] == 10


# ── PaperIndex: Full-Text Search ────────────────────────────────────────

class TestFullTextSearch:
    def test_simple_keyword(self, indexed_db):
        results = indexed_db.search("attention")
        assert len(results) >= 1
        assert any("attention" in r["content"].lower() for r in results)

    def test_boolean_and(self, indexed_db):
        results = indexed_db.search("attention AND subspaces")
        assert len(results) >= 1

    def test_no_results(self, indexed_db):
        results = indexed_db.search("quantum entanglement")
        assert len(results) == 0

    def test_prefix_match(self, indexed_db):
        results = indexed_db.search("recur*")
        assert len(results) >= 1

    def test_scoped_search(self, indexed_db):
        results = indexed_db.search("BLEU", arxiv_ids=["2301.12345v1"])
        assert len(results) == 1

    def test_scoped_search_wrong_paper(self, indexed_db):
        results = indexed_db.search("BLEU", arxiv_ids=["9999.99999v1"])
        assert len(results) == 0

    def test_limit(self, indexed_db):
        results = indexed_db.search("the", limit=2)
        assert len(results) <= 2

    def test_cross_paper_search(self, indexed_db, sample_meta):
        """Index a second paper and search across both."""
        meta2 = dict(sample_meta, arxiv_id="2402.99999v1", title="RL Survey")
        chunks2 = [
            {"content": "Policy gradient estimation in reinforcement learning.",
             "page_start": 1, "page_end": 1, "heading": "Abstract"},
        ]
        indexed_db.upsert_paper(meta2, chunks2, "/tmp/fake2.pdf", 4, "xyz789")

        results = indexed_db.search("policy gradient")
        paper_ids = {r["arxiv_id"] for r in results}
        assert len(paper_ids) == 2

    def test_results_include_metadata(self, indexed_db):
        results = indexed_db.search("attention")
        r = results[0]
        assert "arxiv_id" in r
        assert "title" in r
        assert "authors" in r
        assert "page_start" in r
        assert "heading" in r
        assert "content" in r

    def test_boolean_or(self, indexed_db):
        results = indexed_db.search("BLEU OR reinforcement")
        assert len(results) >= 2

    def test_exact_phrase(self, indexed_db):
        results = indexed_db.search('"recurrent neural networks"')
        assert len(results) >= 1

    def test_search_multiple_arxiv_ids(self, indexed_db, sample_meta):
        meta2 = dict(sample_meta, arxiv_id="2402.99999v1", title="RL Survey")
        chunks2 = [
            {"content": "Deep reinforcement learning methods.",
             "page_start": 1, "page_end": 1, "heading": "Abstract"},
        ]
        indexed_db.upsert_paper(meta2, chunks2, "/tmp/fake2.pdf", 1, "h2")
        results = indexed_db.search(
            "the", arxiv_ids=["2301.12345v1", "2402.99999v1"]
        )
        assert len(results) >= 1


# ── PaperIndex: Text Retrieval ───────────────────────────────────────────

class TestTextRetrieval:
    def test_full_text(self, indexed_db):
        result = indexed_db.get_paper_text("2301.12345v1")
        assert result["title"] == "Attention Is All You Need"
        assert len(result["chunks"]) == 4

    def test_page_range(self, indexed_db):
        result = indexed_db.get_paper_text(
            "2301.12345v1", page_start=3, page_end=5
        )
        assert len(result["chunks"]) == 2
        for c in result["chunks"]:
            assert c["page_start"] >= 3 or c["page_end"] >= 3
            assert c["page_start"] <= 5 or c["page_end"] <= 5

    def test_nonexistent_paper(self, db):
        result = db.get_paper_text("0000.00000v1")
        assert "error" in result

    def test_page_range_no_match(self, indexed_db):
        result = indexed_db.get_paper_text(
            "2301.12345v1", page_start=99, page_end=100
        )
        assert len(result["chunks"]) == 0

    def test_single_page(self, indexed_db):
        result = indexed_db.get_paper_text(
            "2301.12345v1", page_start=1, page_end=1
        )
        assert len(result["chunks"]) == 1
        assert result["chunks"][0]["page_start"] == 1


# ── Heading Detection ────────────────────────────────────────────────────

class TestHeadingDetection:
    @pytest.mark.parametrize("line", [
        "1. Introduction",
        "1 Introduction",
        "3.2 Multi-Head Attention",
        "A. Appendix",
        "RELATED WORK",
        "METHODOLOGY",
        "Conclusion",
        "Abstract",
        "References",
    ])
    def test_detects_headings(self, line):
        assert detect_heading(line) is not None

    @pytest.mark.parametrize("line", [
        "This is a normal sentence about transformers.",
        "",
        "x",
        "AB",  # too short for all-caps
        "a" * 130,  # too long
    ])
    def test_rejects_non_headings(self, line):
        assert detect_heading(line) is None

    @pytest.mark.parametrize("line", [
        "4.1 Experimental Setup",
        "B.2 Detailed Analysis",
        "Acknowledgments",
        "CONCLUSIONS AND FUTURE WORK",
        "Discussion",
    ])
    def test_additional_headings(self, line):
        assert detect_heading(line) is not None

    @pytest.mark.parametrize("line", [
        "the model performs well on benchmarks",
        "1234567890",
        "fig. 3: results are shown below",
    ])
    def test_additional_non_headings(self, line):
        assert detect_heading(line) is None


# ── Chunking ─────────────────────────────────────────────────────────────

class TestChunking:
    def test_basic_chunking(self):
        pages = [
            {"page": 1, "text": "Word " * 300},
            {"page": 2, "text": "Data " * 300},
        ]
        chunks = chunk_pages(pages, chunk_size=1000, overlap=100)
        assert len(chunks) >= 2
        assert all("content" in c for c in chunks)
        assert all("page_start" in c for c in chunks)

    def test_single_page_under_limit(self):
        pages = [{"page": 1, "text": "Short text."}]
        chunks = chunk_pages(pages, chunk_size=1000, overlap=100)
        assert len(chunks) == 1
        assert chunks[0]["content"] == "Short text."

    def test_overlap_present(self):
        pages = [{"page": 1, "text": "AAAA " * 400}]
        chunks = chunk_pages(pages, chunk_size=500, overlap=100)
        if len(chunks) >= 2:
            tail = chunks[0]["content"][-50:]
            assert any(word in chunks[1]["content"] for word in tail.split())

    def test_page_tracking(self):
        pages = [
            {"page": 1, "text": "A " * 100},
            {"page": 2, "text": "B " * 100},
            {"page": 3, "text": "C " * 100},
        ]
        chunks = chunk_pages(pages, chunk_size=10000, overlap=0)
        assert chunks[0]["page_start"] == 1
        assert chunks[-1]["page_end"] == 3

    def test_empty_pages_skipped(self):
        chunks = chunk_pages(
            [{"page": 1, "text": "Content."}], chunk_size=10000
        )
        assert len(chunks) == 1

    def test_empty_input(self):
        chunks = chunk_pages([], chunk_size=1000, overlap=100)
        assert chunks == []

    def test_all_empty_text(self):
        pages = [
            {"page": 1, "text": ""},
            {"page": 2, "text": "   "},
        ]
        chunks = chunk_pages(pages, chunk_size=1000, overlap=100)
        assert len(chunks) == 0

    def test_chunk_has_heading(self):
        """Chunks should carry heading metadata when available."""
        pages = [
            {"page": 1, "text": "1 Introduction\nSome content here."},
        ]
        chunks = chunk_pages(pages, chunk_size=10000, overlap=0)
        assert len(chunks) >= 1
        assert "heading" in chunks[0]

    def test_large_page_splits_correctly(self):
        """A single large page should be split into multiple chunks."""
        pages = [{"page": 1, "text": "word " * 1000}]
        chunks = chunk_pages(pages, chunk_size=500, overlap=50)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert chunk["page_start"] == 1
            assert chunk["page_end"] == 1

    def test_multiple_chunks_from_large_input(self):
        """Confirm multiple chunks are produced from large input."""
        pages = [{"page": 1, "text": "word " * 600}]
        chunks = chunk_pages(pages, chunk_size=500, overlap=50)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk["content"]) > 0


# ── Atom XML Parsing ─────────────────────────────────────────────────────

class TestAtomParsing:
    MOCK_FEED = '''<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <opensearch:totalResults>42</opensearch:totalResults>
      <opensearch:startIndex>0</opensearch:startIndex>
      <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
      <entry>
        <id>http://arxiv.org/abs/1706.03762v7</id>
        <title>  Attention Is
          All You Need  </title>
        <published>2017-06-12T17:57:34Z</published>
        <updated>2023-08-02T00:41:18Z</updated>
        <summary>We propose a new architecture.</summary>
        <author><name>Ashish Vaswani</name></author>
        <author><name>Noam Shazeer</name></author>
        <category term="cs.CL" />
        <category term="cs.LG" />
        <link href="http://arxiv.org/abs/1706.03762v7"
              rel="alternate" type="text/html" />
        <link href="http://arxiv.org/pdf/1706.03762v7"
              title="pdf" type="application/pdf" />
        <arxiv:primary_category term="cs.CL" />
        <arxiv:comment>15 pages, 5 figures</arxiv:comment>
        <arxiv:doi>10.5555/12345</arxiv:doi>
      </entry>
    </feed>'''

    def test_parse_total_results(self):
        result = _parse_feed(self.MOCK_FEED)
        assert result["total_results"] == 42

    def test_parse_arxiv_id(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        assert entry["arxiv_id"] == "1706.03762v7"

    def test_parse_title_whitespace_collapsed(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        assert entry["title"] == "Attention Is All You Need"

    def test_parse_authors(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        assert entry["authors"] == ["Ashish Vaswani", "Noam Shazeer"]

    def test_parse_categories(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        assert "cs.CL" in entry["categories"]
        assert "cs.LG" in entry["categories"]

    def test_parse_links(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        assert "pdf" in entry["pdf_url"]
        assert "abs" in entry["abs_url"]

    def test_parse_extensions(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        assert entry["primary_category"] == "cs.CL"
        assert entry["comment"] == "15 pages, 5 figures"
        assert entry["doi"] == "10.5555/12345"

    def test_empty_feed(self):
        xml = '''<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
          <opensearch:totalResults>0</opensearch:totalResults>
          <opensearch:startIndex>0</opensearch:startIndex>
          <opensearch:itemsPerPage>0</opensearch:itemsPerPage>
        </feed>'''
        result = _parse_feed(xml)
        assert result["total_results"] == 0
        assert result["entries"] == []

    def test_parse_published_and_updated(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        assert entry["published"] == "2017-06-12T17:57:34Z"
        assert entry["updated"] == "2023-08-02T00:41:18Z"

    def test_parse_summary(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        assert entry["summary"] == "We propose a new architecture."

    def test_entry_has_all_keys(self):
        entry = _parse_feed(self.MOCK_FEED)["entries"][0]
        expected_keys = {
            "arxiv_id", "title", "authors", "summary", "published",
            "updated", "categories", "primary_category", "comment",
            "journal_ref", "doi", "pdf_url", "abs_url",
        }
        assert expected_keys.issubset(set(entry.keys()))

    def test_missing_optional_fields(self):
        xml = '''<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <opensearch:totalResults>1</opensearch:totalResults>
          <opensearch:startIndex>0</opensearch:startIndex>
          <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>Minimal Entry</title>
            <published>2024-01-01T00:00:00Z</published>
            <updated>2024-01-01T00:00:00Z</updated>
            <summary>Minimal summary.</summary>
          </entry>
        </feed>'''
        result = _parse_feed(xml)
        entry = result["entries"][0]
        assert entry["arxiv_id"] == "2401.00001v1"
        assert entry["comment"] == ""
        assert entry["journal_ref"] == ""
        assert entry["doi"] == ""
        assert entry["primary_category"] == ""
        assert entry["authors"] == []
        assert entry["categories"] == []


# ── FTS Query Builder ────────────────────────────────────────────────────

class TestFTSQueryBuilder:
    def test_single_word(self):
        assert PaperIndex.to_fts_query("attention") == "attention"

    def test_multi_word_becomes_and(self):
        result = PaperIndex.to_fts_query("attention mechanism")
        assert "AND" in result

    def test_passthrough_boolean(self):
        q = "attention AND NOT recurrence"
        assert PaperIndex.to_fts_query(q) == q

    def test_passthrough_phrase(self):
        q = '"self-supervised learning"'
        assert PaperIndex.to_fts_query(q) == q

    def test_passthrough_wildcard(self):
        q = "transform*"
        assert PaperIndex.to_fts_query(q) == q

    def test_or_passthrough(self):
        q = "attention OR transformer"
        assert PaperIndex.to_fts_query(q) == q

    def test_near_is_preserved(self):
        """NEAR syntax should keep the NEAR keyword."""
        q = "NEAR(policy gradient, 10)"
        result = PaperIndex.to_fts_query(q)
        assert "NEAR" in result


# ── Content Hash ─────────────────────────────────────────────────────────

class TestContentHash:
    def test_hash_deterministic(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"hello world")
        h1 = compute_content_hash(str(f))
        h2 = compute_content_hash(str(f))
        assert h1 == h2

    def test_hash_changes_with_content(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"version 1")
        h1 = compute_content_hash(str(f))
        f.write_bytes(b"version 2")
        h2 = compute_content_hash(str(f))
        assert h1 != h2

    def test_hash_length(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"data")
        assert len(compute_content_hash(str(f))) == 16

    def test_hash_is_hex(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"hex test")
        h = compute_content_hash(str(f))
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.pdf"
        f.write_bytes(b"")
        h = compute_content_hash(str(f))
        assert len(h) == 16


# ═══════════════════════════════════════════════════════════════════════════
# NEW TESTS — helpers, parsers, and consolidation
# ═══════════════════════════════════════════════════════════════════════════


# ── _clamp() ─────────────────────────────────────────────────────────────

class TestClamp:
    def test_within_range(self):
        assert _clamp(50, 1, 100) == 50

    def test_below_minimum(self):
        assert _clamp(-5, 1, 100) == 1

    def test_above_maximum(self):
        assert _clamp(200, 1, 100) == 100

    def test_at_minimum(self):
        assert _clamp(1, 1, 100) == 1

    def test_at_maximum(self):
        assert _clamp(100, 1, 100) == 100

    def test_zero_minimum(self):
        assert _clamp(-1, 0, 10) == 0

    def test_string_input(self):
        assert _clamp("50", 1, 100) == 50

    def test_float_input(self):
        assert _clamp(50.9, 1, 100) == 50

    def test_custom_range(self):
        assert _clamp(5, 10, 20) == 10
        assert _clamp(25, 10, 20) == 20
        assert _clamp(15, 10, 20) == 15

    def test_default_range(self):
        assert _clamp(50) == 50
        assert _clamp(0) == 1
        assert _clamp(200) == 100


# ── _make_rate_limiter() ─────────────────────────────────────────────────

class TestMakeRateLimiter:
    def test_returns_callable(self):
        limiter = _make_rate_limiter(0.01)
        assert callable(limiter)

    def test_first_call_no_delay(self):
        limiter = _make_rate_limiter(1.0)
        start = time.time()
        limiter()
        elapsed = time.time() - start
        assert elapsed < 0.1

    def test_enforces_delay(self):
        limiter = _make_rate_limiter(0.1)
        limiter()
        start = time.time()
        limiter()
        elapsed = time.time() - start
        assert elapsed >= 0.09  # small tolerance

    def test_no_delay_after_wait(self):
        limiter = _make_rate_limiter(0.05)
        limiter()
        time.sleep(0.06)
        start = time.time()
        limiter()
        elapsed = time.time() - start
        assert elapsed < 0.03

    def test_independent_limiters(self):
        limiter_a = _make_rate_limiter(0.1)
        limiter_b = _make_rate_limiter(0.1)
        limiter_a()
        # limiter_b should not be affected by limiter_a
        start = time.time()
        limiter_b()
        elapsed = time.time() - start
        assert elapsed < 0.05


# ── _index_downloaded_pdf() ──────────────────────────────────────────────

class TestIndexDownloadedPdf:
    def test_success(self, tmp_path):
        result = {"indexed": False}
        paper_meta = {
            "arxiv_id": "test:001",
            "title": "Test Paper",
            "authors": ["Author"],
            "summary": "",
            "categories": [],
            "published": "2024-01-01",
        }
        db = PaperIndex(str(tmp_path / "test.db"))

        with patch("server._get_index", return_value=db), \
             patch("server.extract_text_from_pdf", return_value=[
                 {"page": 1, "text": "Hello world"}
             ]), \
             patch("server.chunk_pages", return_value=[
                 {"content": "Hello world", "page_start": 1,
                  "page_end": 1, "heading": "", "chunk_index": 0}
             ]), \
             patch("server.compute_content_hash", return_value="abc123"):
            _index_downloaded_pdf(
                tmp_path / "test.pdf", paper_meta, result, "test:001"
            )

        assert result["indexed"] is True
        assert result["total_pages"] == 1
        assert result["total_chunks"] == 1

    def test_failure_records_error(self, tmp_path):
        result = {"indexed": False}
        paper_meta = {"arxiv_id": "test:fail"}

        with patch("server._get_index", side_effect=Exception("DB error")):
            _index_downloaded_pdf(
                tmp_path / "bad.pdf", paper_meta, result, "test:fail"
            )

        assert result["indexed"] is False
        assert "index_error" in result
        assert "DB error" in result["index_error"]


# ── _parse_crossref() ───────────────────────────────────────────────────

class TestParseCrossref:
    def test_full_item(self):
        item = {
            "DOI": "10.1145/1234",
            "title": ["A Great Paper"],
            "author": [
                {"given": "Alice", "family": "Smith"},
                {"given": "Bob", "family": "Jones"},
            ],
            "abstract": "<p>Abstract with <b>tags</b>.</p>",
            "container-title": ["Nature"],
            "publisher": "Springer",
            "type": "journal-article",
            "published-print": {"date-parts": [[2023, 6, 15]]},
            "URL": "https://doi.org/10.1145/1234",
            "is-referenced-by-count": 42,
            "references-count": 30,
            "ISSN": ["1234-5678"],
            "subject": ["Computer Science"],
            "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
        }
        result = _parse_crossref(item)
        assert result["doi"] == "10.1145/1234"
        assert result["title"] == "A Great Paper"
        assert result["authors"] == ["Alice Smith", "Bob Jones"]
        assert result["abstract"] == "Abstract with tags."
        assert result["journal"] == "Nature"
        assert result["publisher"] == "Springer"
        assert result["type"] == "journal-article"
        assert result["published"] == "2023-6-15"
        assert result["citation_count"] == 42
        assert result["references_count"] == 30
        assert len(result["license"]) == 1

    def test_empty_item(self):
        result = _parse_crossref({})
        assert result["doi"] == ""
        assert result["title"] == ""
        assert result["authors"] == []
        assert result["abstract"] == ""
        assert result["journal"] == ""
        assert result["published"] == ""
        assert result["citation_count"] == 0

    def test_abstract_html_stripped(self):
        item = {"abstract": "<jats:p>Transformer models use <jats:italic>attention</jats:italic>.</jats:p>"}
        result = _parse_crossref(item)
        assert "<" not in result["abstract"]
        assert "attention" in result["abstract"]

    def test_missing_given_name(self):
        item = {"author": [{"family": "Doe"}]}
        result = _parse_crossref(item)
        assert result["authors"] == ["Doe"]

    def test_date_year_only(self):
        item = {"published-online": {"date-parts": [[2021]]}}
        result = _parse_crossref(item)
        assert result["published"] == "2021"

    def test_empty_author_skipped(self):
        item = {"author": [{"given": "", "family": ""}]}
        result = _parse_crossref(item)
        assert result["authors"] == []


# ── _parse_openalex_work() ───────────────────────────────────────────────

class TestParseOpenAlexWork:
    def test_full_work(self):
        work = {
            "id": "https://openalex.org/W12345",
            "doi": "https://doi.org/10.1234/test",
            "title": "OpenAlex Paper",
            "display_name": "OpenAlex Paper",
            "authorships": [
                {
                    "author": {"display_name": "Alice"},
                    "institutions": [{"display_name": "MIT"}],
                },
                {
                    "author": {"display_name": "Bob"},
                    "institutions": [{"display_name": "MIT"}],
                },
            ],
            "publication_year": 2023,
            "publication_date": "2023-06-15",
            "type": "article",
            "cited_by_count": 10,
            "concepts": [{"display_name": "ML"}, {"display_name": "AI"}],
            "topics": [{"display_name": "NLP"}],
            "open_access": {"is_oa": True, "oa_status": "gold"},
            "best_oa_location": {"pdf_url": "https://example.com/paper.pdf"},
            "primary_location": {
                "source": {"display_name": "Nature"},
                "landing_page_url": "https://example.com/landing",
            },
            "abstract_inverted_index": None,
        }
        result = _parse_openalex_work(work)
        assert result["openalex_id"] == "W12345"
        assert result["doi"] == "10.1234/test"
        assert result["title"] == "OpenAlex Paper"
        assert result["authors"] == ["Alice", "Bob"]
        assert result["institutions"] == ["MIT"]  # deduplicated
        assert result["year"] == 2023
        assert result["citation_count"] == 10
        assert result["is_oa"] is True
        assert result["oa_status"] == "gold"
        assert result["pdf_url"] == "https://example.com/paper.pdf"
        assert result["venue"] == "Nature"
        assert result["concepts"] == ["ML", "AI"]
        assert result["topics"] == ["NLP"]

    def test_empty_work(self):
        result = _parse_openalex_work({})
        assert result["openalex_id"] == ""
        assert result["doi"] == ""
        assert result["title"] == ""
        assert result["authors"] == []
        assert result["institutions"] == []
        assert result["abstract"] == ""

    def test_no_primary_location(self):
        result = _parse_openalex_work({"primary_location": None})
        assert result["venue"] == ""
        assert result["url"] == ""

    def test_concepts_limited_to_5(self):
        concepts = [{"display_name": f"C{i}"} for i in range(10)]
        result = _parse_openalex_work({"concepts": concepts})
        assert len(result["concepts"]) == 5


# ── _openalex_abstract() ─────────────────────────────────────────────────

class TestOpenAlexAbstract:
    def test_reconstruct(self):
        inverted_index = {"Hello": [0], "world": [1], "!": [2]}
        assert _openalex_abstract(inverted_index) == "Hello world !"

    def test_empty(self):
        assert _openalex_abstract(None) == ""
        assert _openalex_abstract({}) == ""

    def test_word_at_multiple_positions(self):
        inverted_index = {"the": [0, 3], "cat": [1], "sat": [2], "mat": [4]}
        result = _openalex_abstract(inverted_index)
        assert result == "the cat sat the mat"

    def test_out_of_order_positions(self):
        inverted_index = {"B": [1], "A": [0], "C": [2]}
        assert _openalex_abstract(inverted_index) == "A B C"


# ── _parse_core_work() ──────────────────────────────────────────────────

class TestParseCoreWork:
    def test_full_work(self):
        work = {
            "id": 12345,
            "doi": "10.1234/core",
            "title": "CORE Paper",
            "authors": [{"name": "Alice"}, {"name": "Bob"}],
            "abstract": "This is the abstract.",
            "yearPublished": 2023,
            "publishedDate": "2023-06-15",
            "documentType": "research",
            "publisher": "Publisher A",
            "downloadUrl": "https://core.ac.uk/download/12345.pdf",
            "links": [
                {"type": "download", "url": "https://alt.com/paper.pdf"},
            ],
            "dataProviders": [{"name": "University Repo"}],
            "language": {"name": "English"},
            "sourceFulltextUrls": ["https://example.com/full"],
        }
        result = _parse_core_work(work)
        assert result["core_id"] == "12345"
        assert result["doi"] == "10.1234/core"
        assert result["title"] == "CORE Paper"
        assert result["authors"] == ["Alice", "Bob"]
        assert result["abstract"] == "This is the abstract."
        assert result["year"] == 2023
        assert result["type"] == "research"
        assert result["repository"] == "University Repo"
        assert result["language"] == "English"
        assert result["download_url"] == "https://core.ac.uk/download/12345.pdf"
        assert len(result["fulltext_urls"]) == 1

    def test_empty_work(self):
        result = _parse_core_work({})
        assert result["core_id"] == ""
        assert result["doi"] == ""
        assert result["title"] == ""
        assert result["authors"] == []
        assert result["abstract"] == ""
        assert result["year"] is None
        assert result["repository"] == ""

    def test_string_authors(self):
        """CORE sometimes returns authors as plain strings."""
        work = {"authors": ["Alice", "Bob"]}
        result = _parse_core_work(work)
        assert result["authors"] == ["Alice", "Bob"]

    def test_none_authors(self):
        work = {"authors": None}
        result = _parse_core_work(work)
        assert result["authors"] == []

    def test_no_data_providers(self):
        work = {"dataProviders": None}
        result = _parse_core_work(work)
        assert result["repository"] == ""

    def test_no_language(self):
        work = {"language": None}
        result = _parse_core_work(work)
        assert result["language"] == ""


# ── _dspace_parse_item() ────────────────────────────────────────────────

class TestDspaceParseItem:
    def test_full_item(self):
        item = {
            "uuid": "abc-123",
            "name": "MIT Paper",
            "handle": "1721.1/12345",
            "metadata": [
                {"key": "dc.title", "value": "MIT Paper Title"},
                {"key": "dc.contributor.author", "value": "Alice Smith"},
                {"key": "dc.contributor.author", "value": "Bob Jones"},
                {"key": "dc.description.abstract", "value": "Abstract text"},
                {"key": "dc.date.issued", "value": "2023-06"},
                {"key": "dc.type", "value": "Thesis"},
                {"key": "dc.contributor.department", "value": "CSAIL"},
                {"key": "dc.publisher", "value": "MIT"},
                {"key": "dc.relation.journal", "value": "Nature"},
                {"key": "dc.relation.isversionof", "value": "10.1234/test"},
            ],
        }
        result = _dspace_parse_item(item)
        assert result["uuid"] == "abc-123"
        assert result["title"] == "MIT Paper Title"
        assert result["authors"] == ["Alice Smith", "Bob Jones"]
        assert result["abstract"] == "Abstract text"
        assert result["date_issued"] == "2023-06"
        assert result["type"] == "Thesis"
        assert result["department"] == "CSAIL"
        assert result["handle_url"] == "https://hdl.handle.net/1721.1/12345"
        assert result["doi"] == "10.1234/test"

    def test_empty_item(self):
        result = _dspace_parse_item({})
        assert result["uuid"] == ""
        assert result["title"] == ""
        assert result["authors"] == []
        assert result["abstract"] == ""

    def test_fallback_to_name(self):
        item = {"name": "Fallback Name", "metadata": []}
        result = _dspace_parse_item(item)
        assert result["title"] == "Fallback Name"


# ── _dspace8_parse_item() ───────────────────────────────────────────────

class TestDspace8ParseItem:
    def test_full_item(self):
        item = {
            "uuid": "xyz-789",
            "name": "Harvard Paper",
            "handle": "1/12345",
            "metadata": {
                "dc.title": [{"value": "Harvard Paper Title"}],
                "dc.contributor.author": [
                    {"value": "Alice"},
                    {"value": "Bob"},
                ],
                "dc.description.abstract": [{"value": "Abstract here"}],
                "dc.date.issued": [{"value": "2023"}],
                "dc.type": [{"value": "Article"}],
                "dc.contributor.department": [{"value": "Physics"}],
                "dc.publisher": [{"value": "Springer"}],
                "dc.identifier.doi": [{"value": "10.5678/test"}],
                "dc.subject": [
                    {"value": "Machine Learning"},
                    {"value": "NLP"},
                ],
            },
        }
        result = _dspace8_parse_item(item, "https://dash.harvard.edu")
        assert result["uuid"] == "xyz-789"
        assert result["title"] == "Harvard Paper Title"
        assert result["authors"] == ["Alice", "Bob"]
        assert result["abstract"] == "Abstract here"
        assert result["date_issued"] == "2023"
        assert result["type"] == "Article"
        assert result["department"] == "Physics"
        assert result["doi"] == "10.5678/test"
        assert result["subjects"] == ["Machine Learning", "NLP"]
        assert "dash.harvard.edu" in result["handle_url"]

    def test_empty_item(self):
        result = _dspace8_parse_item({}, "https://example.com")
        assert result["uuid"] == ""
        assert result["title"] == ""
        assert result["authors"] == []
        assert result["subjects"] == []

    def test_fallback_to_name(self):
        item = {"name": "Name Only", "metadata": {}}
        result = _dspace8_parse_item(item, "https://example.com")
        assert result["title"] == "Name Only"

    def test_doi_fallback_to_isversionof(self):
        item = {
            "metadata": {
                "dc.relation.isversionof": [{"value": "10.9999/fallback"}],
            },
        }
        result = _dspace8_parse_item(item, "https://example.com")
        assert result["doi"] == "10.9999/fallback"

    def test_department_fallback_to_other(self):
        item = {
            "metadata": {
                "dc.contributor.other": [{"value": "Other Dept"}],
            },
        }
        result = _dspace8_parse_item(item, "https://example.com")
        assert result["department"] == "Other Dept"


# ── _ensure_read_only_sql() ─────────────────────────────────────────────

class TestEnsureReadOnlySql:
    def test_select_allowed(self):
        assert _ensure_read_only_sql("SELECT * FROM papers") is None

    def test_select_with_whitespace(self):
        assert _ensure_read_only_sql("  SELECT count(*) FROM papers  ") is None

    def test_with_cte_allowed(self):
        assert _ensure_read_only_sql(
            "WITH t AS (SELECT 1) SELECT * FROM t"
        ) is None

    def test_pragma_allowed(self):
        assert _ensure_read_only_sql("PRAGMA table_info('papers')") is None

    def test_describe_allowed(self):
        assert _ensure_read_only_sql("DESCRIBE papers") is None

    def test_show_allowed(self):
        assert _ensure_read_only_sql("SHOW TABLES") is None

    def test_insert_blocked(self):
        result = _ensure_read_only_sql("INSERT INTO papers VALUES (1)")
        assert result is not None
        assert "forbidden" in result.lower() or "SELECT" in result

    def test_update_blocked(self):
        result = _ensure_read_only_sql("UPDATE papers SET title='x'")
        assert result is not None

    def test_delete_blocked(self):
        result = _ensure_read_only_sql("DELETE FROM papers")
        assert result is not None

    def test_drop_blocked(self):
        result = _ensure_read_only_sql("DROP TABLE papers")
        assert result is not None

    def test_create_blocked(self):
        result = _ensure_read_only_sql("CREATE TABLE t (id INT)")
        assert result is not None

    def test_multiple_statements_blocked(self):
        result = _ensure_read_only_sql("SELECT 1; DROP TABLE papers")
        assert result is not None
        assert "single" in result.lower()

    def test_trailing_semicolon_ok(self):
        assert _ensure_read_only_sql("SELECT 1;") is None

    def test_attach_blocked(self):
        result = _ensure_read_only_sql(
            "SELECT 1; ATTACH 'evil.db' AS evil"
        )
        assert result is not None

    def test_copy_to_blocked(self):
        result = _ensure_read_only_sql(
            "COPY papers TO '/tmp/stolen.csv'"
        )
        assert result is not None

    def test_alter_blocked(self):
        result = _ensure_read_only_sql("ALTER TABLE papers ADD col INT")
        assert result is not None

    def test_empty_query(self):
        result = _ensure_read_only_sql("")
        assert result is not None


# ── _extract_main_text() ────────────────────────────────────────────────

class TestExtractMainText:
    def test_simple_html(self):
        html = "<html><head><title>My Page</title></head><body>Hello World</body></html>"
        title, body = _extract_main_text(html)
        assert title == "My Page"
        assert "Hello World" in body

    def test_strips_scripts(self):
        html = "<body>Before<script>alert('xss')</script>After</body>"
        _, body = _extract_main_text(html)
        assert "alert" not in body
        assert "Before" in body
        assert "After" in body

    def test_strips_styles(self):
        html = "<body>Text<style>.red{color:red}</style>More</body>"
        _, body = _extract_main_text(html)
        assert "color" not in body
        assert "Text" in body
        assert "More" in body

    def test_strips_nav_and_footer(self):
        html = "<body><nav>Nav</nav>Content<footer>Foot</footer></body>"
        _, body = _extract_main_text(html)
        assert "Nav" not in body
        assert "Foot" not in body
        assert "Content" in body

    def test_no_title(self):
        html = "<body>Just body</body>"
        title, body = _extract_main_text(html)
        assert title == ""
        assert "Just body" in body

    def test_collapses_whitespace(self):
        html = "<body>  A   B    C  </body>"
        _, body = _extract_main_text(html)
        assert "A B C" in body


# ── _parse_sitemap() ────────────────────────────────────────────────────

class TestParseSitemap:
    def test_basic(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/page1</loc></url>
          <url><loc>https://example.com/page2</loc></url>
        </urlset>"""
        urls = _parse_sitemap(xml)
        assert urls == [
            "https://example.com/page1",
            "https://example.com/page2",
        ]

    def test_empty_sitemap(self):
        xml = '<?xml version="1.0"?><urlset></urlset>'
        assert _parse_sitemap(xml) == []

    def test_sitemap_index(self):
        xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
          <sitemap><loc>https://example.com/sitemap2.xml</loc></sitemap>
        </sitemapindex>"""
        urls = _parse_sitemap(xml)
        assert len(urls) == 2
        assert all(u.endswith(".xml") for u in urls)

    def test_whitespace_around_loc(self):
        xml = '<urlset><url><loc>  https://example.com/page  </loc></url></urlset>'
        urls = _parse_sitemap(xml)
        assert urls == ["https://example.com/page"]


# ── _extract_links() ────────────────────────────────────────────────────

class TestExtractLinks:
    def test_absolute_links(self):
        html = '<a href="https://example.com/page">Link</a>'
        links = _extract_links(html, "https://example.com")
        assert "https://example.com/page" in links

    def test_relative_links(self):
        html = '<a href="/docs/intro">Link</a>'
        links = _extract_links(html, "https://example.com")
        assert "https://example.com/docs/intro" in links

    def test_ignores_fragments(self):
        html = '<a href="#section">Anchor</a><a href="/page">Page</a>'
        links = _extract_links(html, "https://example.com")
        assert len(links) == 1  # fragment is excluded by regex

    def test_empty_html(self):
        assert _extract_links("", "https://example.com") == []

    def test_multiple_links(self):
        html = '<a href="/a">A</a><a href="/b">B</a><a href="/c">C</a>'
        links = _extract_links(html, "https://example.com")
        assert len(links) == 3


# ── _iam_docs_sites_query() ─────────────────────────────────────────────

class TestIamDocsSitesQuery:
    def test_wraps_query_with_sites(self):
        result = _iam_docs_sites_query("keycloak OIDC")
        assert result.startswith("(keycloak OIDC)")
        assert "site:keycloak.org" in result
        assert "site:openpolicyagent.org" in result

    def test_contains_all_domains(self):
        result = _iam_docs_sites_query("test")
        assert "site:authelia.com" in result
        assert "site:casbin.org" in result
        assert "site:jwt.io" in result

    def test_or_between_sites(self):
        result = _iam_docs_sites_query("test")
        assert " OR " in result


# ── _get_download_dir() ─────────────────────────────────────────────────

class TestGetDownloadDir:
    def test_default_creates_dir(self, tmp_path, monkeypatch):
        test_dir = str(tmp_path / "papers")
        monkeypatch.setattr("server.DEFAULT_DOWNLOAD_DIR", test_dir)
        result = _get_download_dir()
        assert result.exists()
        assert str(result) == test_dir

    def test_custom_dir(self, tmp_path):
        custom = str(tmp_path / "custom")
        result = _get_download_dir(custom)
        assert result.exists()
        assert str(result) == custom

    def test_nested_dir_created(self, tmp_path):
        custom = str(tmp_path / "a" / "b" / "c")
        result = _get_download_dir(custom)
        assert result.exists()


# ── extract_text_from_pdf() ─────────────────────────────────────────────

class TestExtractTextFromPdf:
    def test_invalid_file_raises(self, tmp_path):
        fake_pdf = tmp_path / "fake.pdf"
        fake_pdf.write_bytes(b"not a pdf")
        with pytest.raises(Exception):
            extract_text_from_pdf(str(fake_pdf))

    def test_nonexistent_file_raises(self):
        with pytest.raises(Exception):
            extract_text_from_pdf("/nonexistent/path.pdf")


# ── PaperIndex: Edge Cases ──────────────────────────────────────────────

class TestPaperIndexEdgeCases:
    def test_unicode_content(self, db):
        meta = {
            "arxiv_id": "unicode:001",
            "title": "Ünïcödé Tïtlé",
            "authors": ["Müller", "Süß"],
            "summary": "Résumé with accénts",
            "categories": [],
            "published": "2024-01-01",
        }
        chunks = [
            {"content": "Schrödingers Katze und Quantenmechanik",
             "page_start": 1, "page_end": 1, "heading": "Einleitung"},
        ]
        db.upsert_paper(meta, chunks, "/tmp/unicode.pdf", 1, "uhash")
        papers = db.list_papers()
        assert papers[0]["title"] == "Ünïcödé Tïtlé"

    def test_empty_chunks(self, db, sample_meta):
        """Indexing with no chunks should work."""
        db.upsert_paper(sample_meta, [], "/tmp/empty.pdf", 0, "emptyhash")
        stats = db.get_stats()
        assert stats["total_papers"] == 1
        assert stats["total_chunks"] == 0

    def test_very_long_content(self, db, sample_meta):
        long_content = "word " * 100000
        chunks = [
            {"content": long_content, "page_start": 1,
             "page_end": 1, "heading": ""},
        ]
        db.upsert_paper(sample_meta, chunks, "/tmp/long.pdf", 1, "longhash")
        results = db.search("word")
        assert len(results) >= 1

    def test_special_characters_in_search(self, db, sample_meta):
        chunks = [
            {"content": "The O(n log n) algorithm uses C++ templates.",
             "page_start": 1, "page_end": 1, "heading": "Methods"},
        ]
        db.upsert_paper(sample_meta, chunks, "/tmp/spec.pdf", 1, "spechash")
        results = db.search("algorithm")
        assert len(results) >= 1

    def test_authors_stored_as_json(self, db):
        meta = {
            "arxiv_id": "json:001",
            "title": "Test",
            "authors": ["A", "B", "C"],
            "summary": "",
            "categories": ["cs.AI"],
            "published": "2024-01-01",
        }
        chunks = [
            {"content": "text", "page_start": 1, "page_end": 1, "heading": ""},
        ]
        db.upsert_paper(meta, chunks, "/tmp/j.pdf", 1, "jh")
        results = db.search("text")
        assert results[0]["authors"] == ["A", "B", "C"]

    def test_search_returns_bm25_ranked(self, db):
        """Results with more matches should rank higher."""
        meta1 = {
            "arxiv_id": "rank:001", "title": "High Match",
            "authors": [], "summary": "", "categories": [],
            "published": "2024",
        }
        meta2 = {
            "arxiv_id": "rank:002", "title": "Low Match",
            "authors": [], "summary": "", "categories": [],
            "published": "2024",
        }
        db.upsert_paper(meta1, [
            {"content": "transformer transformer transformer attention",
             "page_start": 1, "page_end": 1, "heading": ""},
        ], "/tmp/r1.pdf", 1, "rh1")
        db.upsert_paper(meta2, [
            {"content": "something else entirely about transformers",
             "page_start": 1, "page_end": 1, "heading": ""},
        ], "/tmp/r2.pdf", 1, "rh2")
        results = db.search("transformer")
        assert len(results) >= 2
        # The one with more "transformer" occurrences should come first
        assert results[0]["arxiv_id"] == "rank:001"

    def test_concurrent_db_instances(self, tmp_path):
        """Two PaperIndex instances pointing to the same DB should work."""
        db_path = str(tmp_path / "shared.db")
        db1 = PaperIndex(db_path)
        db2 = PaperIndex(db_path)
        meta = {
            "arxiv_id": "shared:001", "title": "Shared",
            "authors": [], "summary": "", "categories": [],
            "published": "2024",
        }
        db1.upsert_paper(meta, [
            {"content": "test", "page_start": 1, "page_end": 1, "heading": ""},
        ], "/tmp/s.pdf", 1, "sh")
        assert db2.is_indexed("shared:001")


# ═══════════════════════════════════════════════════════════════════════════
# NEW TESTS — _safe_json_loads, URL validation, PDF context manager
# ═══════════════════════════════════════════════════════════════════════════


class TestSafeJsonLoads:
    def test_valid_json_list(self):
        assert _safe_json_loads('["a", "b"]') == ["a", "b"]

    def test_valid_json_dict(self):
        assert _safe_json_loads('{"key": 1}') == {"key": 1}

    def test_invalid_json_returns_default_list(self):
        assert _safe_json_loads("not json") == []

    def test_none_input_returns_default_list(self):
        assert _safe_json_loads(None) == []

    def test_default_override(self):
        assert _safe_json_loads("bad", default={}) == {}

    def test_empty_string(self):
        assert _safe_json_loads("") == []

    def test_valid_json_string(self):
        assert _safe_json_loads('"hello"') == "hello"

    def test_valid_json_number(self):
        assert _safe_json_loads("42") == 42


class TestUrlValidation:
    """Test that fetch_cloud_doc_page rejects URLs not on allowed hosts."""

    @patch("server.requests.get")
    def test_rejects_evil_domain_with_allowed_in_path(self, mock_get):
        """A URL like https://evil.com/docs.aws.amazon.com/ must be rejected."""
        result = fetch_cloud_doc_page("https://evil.com/docs.aws.amazon.com/foo")
        assert "error" in result
        mock_get.assert_not_called()

    @patch("server.requests.get")
    def test_rejects_subdomain_trick(self, mock_get):
        """A URL like https://docs.aws.amazon.com.evil.com/ must be rejected."""
        result = fetch_cloud_doc_page("https://docs.aws.amazon.com.evil.com/page")
        assert "error" in result
        mock_get.assert_not_called()

    @patch("server.requests.get")
    def test_accepts_valid_aws_url(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><head><title>AWS</title></head><body>Content</body></html>"
        mock_get.return_value = mock_resp
        result = fetch_cloud_doc_page("https://docs.aws.amazon.com/IAM/latest/")
        assert "error" not in result

    @patch("server.requests.get")
    def test_accepts_valid_gcp_url(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><head><title>GCP</title></head><body>Content</body></html>"
        mock_get.return_value = mock_resp
        result = fetch_cloud_doc_page("https://cloud.google.com/iam/docs/overview")
        assert "error" not in result

    @patch("server.requests.get")
    def test_rejects_empty_url(self, mock_get):
        result = fetch_cloud_doc_page("")
        assert "error" in result
        mock_get.assert_not_called()


class TestExtractTextFromPdfContextManager:
    """Verify extract_text_from_pdf uses a context manager for cleanup."""

    @patch("server.fitz.open")
    def test_context_manager_exit_called_on_success(self, mock_fitz_open):
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Hello world"
        mock_doc.__enter__ = MagicMock(return_value=mock_doc)
        mock_doc.__exit__ = MagicMock(return_value=False)
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_fitz_open.return_value = mock_doc

        result = extract_text_from_pdf("/fake/path.pdf")
        assert len(result) == 1
        assert result[0]["text"] == "Hello world"
        mock_doc.__exit__.assert_called_once()

    @patch("server.fitz.open")
    def test_context_manager_exit_called_on_error(self, mock_fitz_open):
        mock_doc = MagicMock()
        mock_doc.__enter__ = MagicMock(return_value=mock_doc)
        mock_doc.__exit__ = MagicMock(return_value=False)
        mock_doc.__iter__ = MagicMock(side_effect=RuntimeError("mid-iteration error"))
        mock_fitz_open.return_value = mock_doc

        with pytest.raises(RuntimeError, match="mid-iteration"):
            extract_text_from_pdf("/fake/path.pdf")
        mock_doc.__exit__.assert_called_once()

    @patch("server.fitz.open")
    def test_empty_pages_skipped(self, mock_fitz_open):
        mock_doc = MagicMock()
        page1 = MagicMock()
        page1.get_text.return_value = "   "
        page2 = MagicMock()
        page2.get_text.return_value = "Real content"
        mock_doc.__enter__ = MagicMock(return_value=mock_doc)
        mock_doc.__exit__ = MagicMock(return_value=False)
        mock_doc.__iter__ = MagicMock(return_value=iter([page1, page2]))
        mock_fitz_open.return_value = mock_doc

        result = extract_text_from_pdf("/fake/path.pdf")
        assert len(result) == 1
        assert result[0]["page"] == 2
        assert result[0]["text"] == "Real content"
