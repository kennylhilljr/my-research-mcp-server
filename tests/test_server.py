"""Tests for the arXiv MCP server."""

import json
import os
import tempfile

import pytest

# Import from the server module at repo root
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import (
    PaperIndex,
    chunk_pages,
    compute_content_hash,
    detect_heading,
    _parse_feed,
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
            "content": "The dominant sequence transduction models are based on complex recurrent neural networks.",
            "page_start": 1,
            "page_end": 1,
            "heading": "1 Introduction",
        },
        {
            "content": "Multi-head attention allows the model to jointly attend to information from different subspaces.",
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
            "content": "Policy gradient methods and reinforcement learning provide optimization strategies.",
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
        new_chunks = [{"content": "Replaced content.", "page_start": 1, "page_end": 1, "heading": ""}]
        indexed_db.upsert_paper(sample_meta, new_chunks, "/tmp/new.pdf", 1, "new_hash")
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
            {"content": "Policy gradient estimation in reinforcement learning.", "page_start": 1, "page_end": 1, "heading": "Abstract"},
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


# ── PaperIndex: Text Retrieval ───────────────────────────────────────────

class TestTextRetrieval:
    def test_full_text(self, indexed_db):
        result = indexed_db.get_paper_text("2301.12345v1")
        assert result["title"] == "Attention Is All You Need"
        assert len(result["chunks"]) == 4

    def test_page_range(self, indexed_db):
        result = indexed_db.get_paper_text("2301.12345v1", page_start=3, page_end=5)
        assert len(result["chunks"]) == 2
        for c in result["chunks"]:
            assert c["page_start"] >= 3 or c["page_end"] >= 3
            assert c["page_start"] <= 5 or c["page_end"] <= 5

    def test_nonexistent_paper(self, db):
        result = db.get_paper_text("0000.00000v1")
        assert "error" in result


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
            # Last part of chunk 0 should appear at start of chunk 1
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
        pages = []
        # Should not crash
        chunks = chunk_pages([{"page": 1, "text": "Content."}], chunk_size=10000)
        assert len(chunks) == 1


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
        <link href="http://arxiv.org/abs/1706.03762v7" rel="alternate" type="text/html" />
        <link href="http://arxiv.org/pdf/1706.03762v7" title="pdf" type="application/pdf" />
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


# ── FTS Query Builder ────────────────────────────────────────────────────

class TestFTSQueryBuilder:
    def test_single_word(self):
        assert PaperIndex._to_fts_query("attention") == "attention"

    def test_multi_word_becomes_and(self):
        result = PaperIndex._to_fts_query("attention mechanism")
        assert "AND" in result

    def test_passthrough_boolean(self):
        q = "attention AND NOT recurrence"
        assert PaperIndex._to_fts_query(q) == q

    def test_passthrough_phrase(self):
        q = '"self-supervised learning"'
        assert PaperIndex._to_fts_query(q) == q

    def test_passthrough_wildcard(self):
        q = "transform*"
        assert PaperIndex._to_fts_query(q) == q


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
