#!/usr/bin/env python3
"""
My Research MCP Server — Multi-Source Academic Search
=====================================================
An MCP server that lets you:
  - Search arXiv, Semantic Scholar, OpenAlex, CORE, Crossref,
    Harvard DASH, MIT DSpace, Cornell eCommons, Penn ScholarlyCommons
  - Download paper PDFs from any source (arXiv, DOI via Unpaywall, CORE)
  - Extract & index full text from every PDF (SQLite FTS5 + BM25)
  - Query across the full content of your entire local paper library
  - Search cloud vendor docs (AWS, GCP, Microsoft Learn)
  - Search & index IAM/identity documentation (26 OSS projects)
  - Search GitHub repos and code for implementations
  - Run SQL analytics over the paper index via DuckDB
  - Embed paper chunks and run semantic vector search (fastembed + HNSW)

Dependencies:  pip install mcp requests pymupdf duckdb google-auth fastembed
"""

import os
import re
import sys
import json
import time
import uuid
import sqlite3
import logging
import hashlib
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import fitz  # PyMuPDF
import requests
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ARXIV_API_BASE = "http://export.arxiv.org/api/query"
PDF_BASE_URL = "https://arxiv.org/pdf"
ABS_BASE_URL = "https://arxiv.org/abs"

DEFAULT_DOWNLOAD_DIR = os.environ.get("ARXIV_DOWNLOAD_DIR", os.path.expanduser("~/arxiv-papers"))
DEFAULT_DB_PATH = os.environ.get("ARXIV_DB_PATH", os.path.join(DEFAULT_DOWNLOAD_DIR, "arxiv_index.db"))
CHUNK_SIZE = int(os.environ.get("ARXIV_CHUNK_SIZE", "1500"))       # chars per chunk
CHUNK_OVERLAP = int(os.environ.get("ARXIV_CHUNK_OVERLAP", "200"))  # overlap between chunks
RATE_LIMIT_SECONDS = 3

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("arxiv-mcp")


def _safe_json_loads(raw, default=None):
    """Parse JSON with a fallback for corrupt or missing data."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE LAYER
# ═══════════════════════════════════════════════════════════════════════════

class PaperIndex:
    """SQLite + FTS5 index for extracted PDF text."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                arxiv_id      TEXT PRIMARY KEY,
                title         TEXT,
                authors       TEXT,   -- JSON array
                abstract      TEXT,
                categories    TEXT,   -- JSON array
                published     TEXT,
                pdf_path      TEXT,
                total_pages   INTEGER,
                indexed_at    TEXT,
                content_hash  TEXT    -- detect re-indexing needs
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                arxiv_id      TEXT NOT NULL,
                page_start    INTEGER,
                page_end      INTEGER,
                chunk_index   INTEGER,
                heading       TEXT,
                content       TEXT NOT NULL,
                FOREIGN KEY (arxiv_id) REFERENCES papers(arxiv_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_arxiv ON chunks(arxiv_id);
        """)
        # FTS5 virtual table — created separately so we can handle "already exists"
        try:
            cur.execute("""
                CREATE VIRTUAL TABLE chunks_fts USING fts5(
                    content, heading, arxiv_id,
                    content='chunks',
                    content_rowid='chunk_id',
                    tokenize='porter unicode61'
                );
            """)
            # Triggers to keep FTS in sync
            cur.executescript("""
                CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(rowid, content, heading, arxiv_id)
                    VALUES (new.chunk_id, new.content, new.heading, new.arxiv_id);
                END;

                CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, content, heading, arxiv_id)
                    VALUES ('delete', old.chunk_id, old.content, old.heading, old.arxiv_id);
                END;

                CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, content, heading, arxiv_id)
                    VALUES ('delete', old.chunk_id, old.content, old.heading, old.arxiv_id);
                    INSERT INTO chunks_fts(rowid, content, heading, arxiv_id)
                    VALUES (new.chunk_id, new.content, new.heading, new.arxiv_id);
                END;
            """)
        except sqlite3.OperationalError:
            pass  # already exists
        self.conn.commit()

    # ── Indexing ──────────────────────────────────────────────────────────

    def is_indexed(self, arxiv_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
        return row is not None

    def needs_reindex(self, arxiv_id: str, content_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT content_hash FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
        if row is None:
            return True
        return row["content_hash"] != content_hash

    def upsert_paper(self, meta: dict, chunks: list, pdf_path: str,
                     total_pages: int, content_hash: str):
        arxiv_id = meta["arxiv_id"]
        cur = self.conn.cursor()

        # Remove old data
        cur.execute("DELETE FROM chunks WHERE arxiv_id = ?", (arxiv_id,))
        cur.execute("DELETE FROM papers WHERE arxiv_id = ?", (arxiv_id,))

        # Insert paper record
        cur.execute("""
            INSERT INTO papers (arxiv_id, title, authors, abstract, categories,
                                published, pdf_path, total_pages, indexed_at, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            arxiv_id,
            meta.get("title", ""),
            json.dumps(meta.get("authors", [])),
            meta.get("summary", ""),
            json.dumps(meta.get("categories", [])),
            meta.get("published", ""),
            pdf_path,
            total_pages,
            datetime.now(timezone.utc).isoformat(),
            content_hash,
        ))

        # Insert chunks (FTS triggers handle the rest)
        for i, chunk in enumerate(chunks):
            cur.execute("""
                INSERT INTO chunks (arxiv_id, page_start, page_end, chunk_index,
                                    heading, content)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                arxiv_id,
                chunk.get("page_start"),
                chunk.get("page_end"),
                i,
                chunk.get("heading", ""),
                chunk["content"],
            ))

        self.conn.commit()
        logger.info(f"Indexed {arxiv_id}: {len(chunks)} chunks from {total_pages} pages")

    def remove_paper(self, arxiv_id: str):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM chunks WHERE arxiv_id = ?", (arxiv_id,))
        cur.execute("DELETE FROM papers WHERE arxiv_id = ?", (arxiv_id,))
        self.conn.commit()

    # ── Querying ─────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20, arxiv_ids: list = None) -> list:
        """Full-text search across all indexed paper content."""
        fts_query = self.to_fts_query(query)

        if arxiv_ids:
            placeholders = ",".join("?" for _ in arxiv_ids)
            sql = f"""
                SELECT c.chunk_id, c.arxiv_id, c.page_start, c.page_end,
                       c.heading, c.content, c.chunk_index,
                       p.title, p.authors,
                       rank
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = chunks_fts.rowid
                JOIN papers p ON p.arxiv_id = c.arxiv_id
                WHERE chunks_fts MATCH ?
                  AND c.arxiv_id IN ({placeholders})
                ORDER BY rank
                LIMIT ?
            """
            params = [fts_query] + arxiv_ids + [limit]
        else:
            sql = """
                SELECT c.chunk_id, c.arxiv_id, c.page_start, c.page_end,
                       c.heading, c.content, c.chunk_index,
                       p.title, p.authors,
                       rank
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = chunks_fts.rowid
                JOIN papers p ON p.arxiv_id = c.arxiv_id
                WHERE chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            params = [fts_query, limit]

        rows = self.conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            results.append({
                "arxiv_id": row["arxiv_id"],
                "title": row["title"],
                "authors": _safe_json_loads(row["authors"]),
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "heading": row["heading"],
                "content": row["content"],
                "chunk_index": row["chunk_index"],
                "relevance_rank": row["rank"],
            })
        return results

    def get_paper_text(self, arxiv_id: str, page_start: int = None,
                       page_end: int = None) -> dict:
        """Get full or partial text of an indexed paper."""
        paper = self.conn.execute(
            "SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
        if not paper:
            return {"error": f"Paper {arxiv_id} not found in index."}

        if page_start is not None or page_end is not None:
            sql = "SELECT * FROM chunks WHERE arxiv_id = ?"
            params = [arxiv_id]
            if page_start is not None:
                sql += " AND page_end >= ?"
                params.append(page_start)
            if page_end is not None:
                sql += " AND page_start <= ?"
                params.append(page_end)
            sql += " ORDER BY chunk_index"
            chunks = self.conn.execute(sql, params).fetchall()
        else:
            chunks = self.conn.execute(
                "SELECT * FROM chunks WHERE arxiv_id = ? ORDER BY chunk_index",
                (arxiv_id,)
            ).fetchall()

        return {
            "arxiv_id": arxiv_id,
            "title": paper["title"],
            "authors": _safe_json_loads(paper["authors"]),
            "abstract": paper["abstract"],
            "total_pages": paper["total_pages"],
            "indexed_at": paper["indexed_at"],
            "chunks": [
                {
                    "page_start": c["page_start"],
                    "page_end": c["page_end"],
                    "heading": c["heading"],
                    "content": c["content"],
                }
                for c in chunks
            ],
        }

    def list_papers(self) -> list:
        rows = self.conn.execute("""
            SELECT p.arxiv_id, p.title, p.authors, p.categories, p.published,
                   p.total_pages, p.indexed_at, p.pdf_path,
                   COUNT(c.chunk_id) as chunk_count
            FROM papers p
            LEFT JOIN chunks c ON c.arxiv_id = p.arxiv_id
            GROUP BY p.arxiv_id
            ORDER BY p.indexed_at DESC
        """).fetchall()
        return [
            {
                "arxiv_id": r["arxiv_id"],
                "title": r["title"],
                "authors": _safe_json_loads(r["authors"]),
                "categories": _safe_json_loads(r["categories"]),
                "published": r["published"],
                "total_pages": r["total_pages"],
                "chunk_count": r["chunk_count"],
                "indexed_at": r["indexed_at"],
                "pdf_path": r["pdf_path"],
            }
            for r in rows
        ]

    def get_stats(self) -> dict:
        paper_count = self.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        chunk_count = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        total_pages = self.conn.execute(
            "SELECT COALESCE(SUM(total_pages), 0) FROM papers"
        ).fetchone()[0]
        return {
            "total_papers": paper_count,
            "total_chunks": chunk_count,
            "total_pages": total_pages,
            "db_path": self.db_path,
        }

    @staticmethod
    def to_fts_query(query: str) -> str:
        """Convert natural language to FTS5 query."""
        q = query.strip()
        # Pass through queries that already use FTS5 syntax
        if any(op in q.upper() for op in [" AND ", " OR ", " NOT ", " NEAR("]):
            return q
        # Pass through quoted phrases and explicit prefix wildcards
        if q.startswith('"') or "*" in q:
            return q
        tokens = q.split()
        if len(tokens) == 1:
            return tokens[0]
        return " AND ".join(tokens)


# ═══════════════════════════════════════════════════════════════════════════
# PDF TEXT EXTRACTION & CHUNKING
# ═══════════════════════════════════════════════════════════════════════════

def extract_text_from_pdf(pdf_path: str) -> list:
    """Extract text page-by-page from a PDF using PyMuPDF."""
    pages = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text")
            if text.strip():
                pages.append({"page": i + 1, "text": text})
    return pages


def detect_heading(line: str) -> str:
    """Heuristic: detect section headings in academic papers."""
    line = line.strip()
    if not line or len(line) > 120:
        return None
    # Numbered sections: "1. Introduction", "3.2 Methods", "A. Appendix"
    if re.match(r'^([A-Z]\.?\d*|[0-9]+\.?\d*\.?\d*)\s+[A-Z]', line):
        return line
    # ALL-CAPS headings
    if line.isupper() and 3 < len(line) < 80:
        return line
    # Common section titles
    common = ["abstract", "introduction", "related work", "background",
              "methods", "methodology", "approach", "model", "architecture",
              "experiments", "results", "evaluation", "discussion",
              "conclusion", "conclusions", "future work", "references",
              "acknowledgments", "acknowledgements", "appendix"]
    if line.lower().rstrip(".:") in common:
        return line
    return None


def chunk_pages(pages: list, chunk_size: int = CHUNK_SIZE,
                overlap: int = CHUNK_OVERLAP) -> list:
    """Split extracted page texts into overlapping chunks with heading detection."""
    chunks = []
    current_text = ""
    current_page_start = pages[0]["page"] if pages else 1
    current_page_end = current_page_start
    current_heading = ""

    for page_data in pages:
        page_num = page_data["page"]
        lines = page_data["text"].split("\n")

        for line in lines:
            heading = detect_heading(line)
            if heading:
                current_heading = heading

            current_text += line + "\n"
            current_page_end = page_num

            if len(current_text) >= chunk_size:
                chunks.append({
                    "content": current_text.strip(),
                    "page_start": current_page_start,
                    "page_end": current_page_end,
                    "heading": current_heading,
                })
                if overlap > 0 and len(current_text) > overlap:
                    current_text = current_text[-overlap:]
                else:
                    current_text = ""
                current_page_start = current_page_end

    if current_text.strip():
        chunks.append({
            "content": current_text.strip(),
            "page_start": current_page_start,
            "page_end": current_page_end,
            "heading": current_heading,
        })

    return chunks


def compute_content_hash(pdf_path: str) -> str:
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════
# ARXIV API HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _make_rate_limiter(delay):
    """Create a rate-limiter closure that enforces *delay* seconds between calls."""
    state = {"last": 0.0}
    def _limit():
        elapsed = time.time() - state["last"]
        if elapsed < delay:
            time.sleep(delay - elapsed)
        state["last"] = time.time()
    return _limit

_rate_limit = _make_rate_limiter(RATE_LIMIT_SECONDS)  # arXiv (3s)


def _parse_entry(entry: ET.Element) -> dict:
    def _text(tag, ns="atom"):
        el = entry.find(f"{ns}:{tag}", NS) if ns else entry.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    authors = []
    for a in entry.findall("atom:author", NS):
        n = a.find("atom:name", NS)
        if n is not None and n.text:
            authors.append(n.text.strip())

    categories = [c.get("term", "") for c in entry.findall("atom:category", NS) if c.get("term")]

    pdf_link, abs_link = "", ""
    for link in entry.findall("atom:link", NS):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_link = link.get("href", "")
        elif link.get("rel") == "alternate":
            abs_link = link.get("href", "")

    raw_id = _text("id")
    arxiv_id = raw_id.replace("http://arxiv.org/abs/", "").replace("https://arxiv.org/abs/", "")

    comment_el = entry.find("arxiv:comment", NS)
    journal_el = entry.find("arxiv:journal_ref", NS)
    pc_el = entry.find("arxiv:primary_category", NS)
    doi_el = entry.find("arxiv:doi", NS)

    return {
        "arxiv_id": arxiv_id,
        "title": " ".join(_text("title").split()),
        "authors": authors,
        "summary": " ".join(_text("summary").split()),
        "published": _text("published"),
        "updated": _text("updated"),
        "categories": categories,
        "primary_category": pc_el.get("term", "") if pc_el is not None else "",
        "comment": comment_el.text.strip() if comment_el is not None and comment_el.text else "",
        "journal_ref": journal_el.text.strip() if journal_el is not None and journal_el.text else "",
        "doi": doi_el.text.strip() if doi_el is not None and doi_el.text else "",
        "pdf_url": pdf_link or f"{PDF_BASE_URL}/{arxiv_id}",
        "abs_url": abs_link or f"{ABS_BASE_URL}/{arxiv_id}",
    }


def _parse_feed(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    tr = root.find("opensearch:totalResults", NS)
    si = root.find("opensearch:startIndex", NS)
    ipp = root.find("opensearch:itemsPerPage", NS)
    return {
        "total_results": int(tr.text) if tr is not None and tr.text else 0,
        "start_index": int(si.text) if si is not None and si.text else 0,
        "items_per_page": int(ipp.text) if ipp is not None and ipp.text else 0,
        "entries": [_parse_entry(e) for e in root.findall("atom:entry", NS)],
    }


# ═══════════════════════════════════════════════════════════════════════════
# MCP SERVER & TOOLS
# ═══════════════════════════════════════════════════════════════════════════

mcp = FastMCP("my-research")

_index = None

def _get_index() -> PaperIndex:
    global _index
    if _index is None:
        _index = PaperIndex(DEFAULT_DB_PATH)
    return _index


def _get_download_dir(custom: str = None) -> Path:
    d = Path(custom) if custom else Path(DEFAULT_DOWNLOAD_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clamp(val, lo=1, hi=100):
    """Clamp an integer value to [lo, hi]."""
    return max(lo, min(hi, int(val)))


def _index_downloaded_pdf(filepath, paper_meta, result, id_label):
    """Extract text from a downloaded PDF and index it. Mutates *result* in place."""
    try:
        idx = _get_index()
        pages = extract_text_from_pdf(str(filepath))
        chunks = chunk_pages(pages)
        content_hash = compute_content_hash(str(filepath))
        idx.upsert_paper(paper_meta, chunks, str(filepath), len(pages), content_hash)
        result["indexed"] = True
        result["total_pages"] = len(pages)
        result["total_chunks"] = len(chunks)
    except Exception as e:
        result["index_error"] = str(e)
        logger.error(f"Indexing failed for {id_label}: {e}")


# ── arXiv API Tools ──────────────────────────────────────────────────────

@mcp.tool()
def search_arxiv(
    query: str,
    max_results: int = 10,
    start: int = 0,
    sort_by: str = "relevance",
    sort_order: str = "descending",
    category: str = None,
) -> str:
    """
    Search the arXiv API for papers matching a query (metadata search).

    This searches arXiv's online catalog. To search the full text of papers
    you've already downloaded and indexed, use query_papers instead.

    Args:
        query: arXiv query syntax. Field prefixes: ti: au: abs: cat: all:
               Boolean: AND, OR, ANDNOT. Example: "ti:transformer AND cat:cs.CL"
        max_results: 1-100 results to return (default 10).
        start: Paging offset (default 0).
        sort_by: "relevance", "lastUpdatedDate", or "submittedDate".
        sort_order: "descending" or "ascending".
        category: Optional category filter appended as AND (e.g. "cs.AI").

    Returns:
        JSON with total_results and entries list.
    """
    max_results = _clamp(max_results, 1, 100)

    search_query = f"({query}) AND cat:{category}" if category else query
    params = {
        "search_query": search_query,
        "start": start,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }

    _rate_limit()
    logger.info(f"arXiv API search: {params}")
    resp = requests.get(ARXIV_API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return json.dumps(_parse_feed(resp.text), indent=2)


@mcp.tool()
def get_paper_metadata(arxiv_id: str) -> str:
    """
    Fetch metadata for one or more papers by arXiv ID from the arXiv API.

    Args:
        arxiv_id: Comma-separated IDs, e.g. "2301.12345" or "2301.12345,2302.67890".
    """
    id_list = ",".join(i.strip() for i in arxiv_id.split(",") if i.strip())
    params = {"id_list": id_list, "max_results": len(id_list.split(","))}
    _rate_limit()
    resp = requests.get(ARXIV_API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return json.dumps(_parse_feed(resp.text), indent=2)


# ── Download & Indexing Tools ────────────────────────────────────────────

@mcp.tool()
def download_paper(
    arxiv_id: str,
    auto_index: bool = True,
    download_dir: str = None,
) -> str:
    """
    Download a paper's PDF and automatically index its full text for search.

    Args:
        arxiv_id: arXiv paper ID (e.g. "2301.12345" or "2301.12345v2").
        auto_index: If True (default), immediately extract and index the text.
        download_dir: Custom download directory. Default: ~/arxiv-papers.

    Returns:
        JSON with file path, size, and indexing status.
    """
    target_dir = _get_download_dir(download_dir)
    clean_id = arxiv_id.strip().replace("/", "_")
    filepath = target_dir / f"{clean_id}.pdf"

    # Fetch metadata
    _rate_limit()
    meta_resp = requests.get(ARXIV_API_BASE,
                             params={"id_list": arxiv_id.strip(), "max_results": 1},
                             timeout=30)
    meta_resp.raise_for_status()
    meta = _parse_feed(meta_resp.text)
    paper_meta = meta["entries"][0] if meta["entries"] else {}

    # Download PDF
    pdf_url = f"{PDF_BASE_URL}/{arxiv_id.strip()}"
    _rate_limit()
    logger.info(f"Downloading {pdf_url}")
    pdf_resp = requests.get(pdf_url, timeout=120, stream=True)
    pdf_resp.raise_for_status()

    with open(filepath, "wb") as f:
        for chunk in pdf_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    file_size = filepath.stat().st_size
    result = {
        "status": "downloaded",
        "file_path": str(filepath),
        "file_size_bytes": file_size,
        "file_size_mb": round(file_size / (1024 * 1024), 2),
        "arxiv_id": arxiv_id.strip(),
        "title": paper_meta.get("title", ""),
        "authors": paper_meta.get("authors", []),
        "pdf_url": pdf_url,
        "indexed": False,
    }

    if auto_index:
        _index_downloaded_pdf(filepath, paper_meta, result, arxiv_id)

    return json.dumps(result, indent=2)


@mcp.tool()
def index_paper(arxiv_id: str, pdf_path: str = None,
                download_dir: str = None) -> str:
    """
    Extract text from a downloaded PDF and index it for full-text search.

    Use this to manually index a paper, or re-index after updates.

    Args:
        arxiv_id: The arXiv ID of the paper.
        pdf_path: Explicit path to the PDF. If not given, looks in download_dir.
        download_dir: Where to look for the PDF. Default: ~/arxiv-papers.
    """
    idx = _get_index()

    if not pdf_path:
        target_dir = _get_download_dir(download_dir)
        clean_id = arxiv_id.strip().replace("/", "_")
        pdf_path = str(target_dir / f"{clean_id}.pdf")

    if not Path(pdf_path).exists():
        return json.dumps({"error": f"PDF not found at {pdf_path}. Download it first."})

    content_hash = compute_content_hash(pdf_path)
    if not idx.needs_reindex(arxiv_id, content_hash):
        return json.dumps({"status": "already_indexed", "arxiv_id": arxiv_id,
                           "message": "PDF content unchanged, skipping."})

    try:
        _rate_limit()
        meta_resp = requests.get(ARXIV_API_BASE,
                                 params={"id_list": arxiv_id.strip(), "max_results": 1},
                                 timeout=30)
        meta_resp.raise_for_status()
        meta = _parse_feed(meta_resp.text)
        paper_meta = meta["entries"][0] if meta["entries"] else {"arxiv_id": arxiv_id}
    except Exception as e:
        logger.debug("arXiv metadata fetch skipped for %s: %s", arxiv_id, e)
        paper_meta = {"arxiv_id": arxiv_id}

    pages = extract_text_from_pdf(pdf_path)
    chunks = chunk_pages(pages)
    idx.upsert_paper(paper_meta, chunks, pdf_path, len(pages), content_hash)

    return json.dumps({
        "status": "indexed",
        "arxiv_id": arxiv_id,
        "total_pages": len(pages),
        "total_chunks": len(chunks),
    })


@mcp.tool()
def index_all_papers(download_dir: str = None) -> str:
    """
    Scan the download directory and index all unindexed or changed PDFs.

    Args:
        download_dir: Directory to scan. Default: ~/arxiv-papers.

    Returns:
        Summary of how many papers were indexed, skipped, or errored.
    """
    target_dir = _get_download_dir(download_dir)
    idx = _get_index()

    indexed, skipped, errors = 0, 0, 0
    details = []

    for pdf_file in sorted(target_dir.glob("*.pdf")):
        arxiv_id = pdf_file.stem.replace("_", "/")
        try:
            content_hash = compute_content_hash(str(pdf_file))
            if not idx.needs_reindex(arxiv_id, content_hash):
                skipped += 1
                continue

            try:
                _rate_limit()
                meta_resp = requests.get(ARXIV_API_BASE,
                                         params={"id_list": arxiv_id, "max_results": 1},
                                         timeout=30)
                meta_resp.raise_for_status()
                meta = _parse_feed(meta_resp.text)
                paper_meta = meta["entries"][0] if meta["entries"] else {"arxiv_id": arxiv_id}
            except Exception as e:
                logger.debug("arXiv metadata fetch skipped for %s: %s", arxiv_id, e)
                paper_meta = {"arxiv_id": arxiv_id}

            pages = extract_text_from_pdf(str(pdf_file))
            chunks = chunk_pages(pages)
            idx.upsert_paper(paper_meta, chunks, str(pdf_file), len(pages), content_hash)
            indexed += 1
            details.append({"arxiv_id": arxiv_id, "status": "indexed",
                            "pages": len(pages), "chunks": len(chunks)})
        except Exception as e:
            errors += 1
            details.append({"arxiv_id": arxiv_id, "status": "error", "error": str(e)})
            logger.error(f"Error indexing {arxiv_id}: {e}")

    return json.dumps({
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "total_scanned": indexed + skipped + errors,
        "details": details,
    }, indent=2)


# ── Full-Text Query Tools ───────────────────────────────────────────────

@mcp.tool()
def query_papers(
    query: str,
    max_results: int = 20,
    paper_ids: str = None,
) -> str:
    """
    Full-text search across the content of all indexed arXiv PDFs.

    This is the primary tool for finding specific information INSIDE papers —
    equations, methods, results, definitions, theorems, etc.

    Args:
        query: Search terms. Supports:
               - Natural language: "gradient descent convergence proof"
               - Boolean: "attention AND mechanism"
               - Phrases: '"self-supervised learning"'
               - Prefix: "transform*" matches transformer, transformation, etc.
               - FTS5 operators: NEAR(term1 term2, 10), NOT, OR
        max_results: Max chunks to return (default 20).
        paper_ids: Optional comma-separated arXiv IDs to restrict search to
                   specific papers. Leave empty to search all indexed papers.

    Returns:
        JSON with matching text chunks, their paper title, page numbers,
        section headings, and BM25 relevance ranking.
    """
    idx = _get_index()
    arxiv_ids = None
    if paper_ids:
        arxiv_ids = [i.strip() for i in paper_ids.split(",") if i.strip()]

    results = idx.search(query, limit=max_results, arxiv_ids=arxiv_ids)

    return json.dumps({
        "query": query,
        "results_count": len(results),
        "results": results,
    }, indent=2)


@mcp.tool()
def get_paper_text(
    arxiv_id: str,
    page_start: int = None,
    page_end: int = None,
) -> str:
    """
    Retrieve the full extracted text of an indexed paper, or a specific page range.

    Args:
        arxiv_id: The arXiv paper ID.
        page_start: Optional start page (1-based). Omit for full text.
        page_end: Optional end page (inclusive). Omit for full text.

    Returns:
        JSON with paper metadata and ordered text chunks.
    """
    idx = _get_index()
    result = idx.get_paper_text(arxiv_id, page_start, page_end)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_indexed_papers() -> str:
    """
    List all papers in the full-text search index.

    Returns each paper's arXiv ID, title, authors, page/chunk counts, and index date.
    """
    idx = _get_index()
    papers = idx.list_papers()
    stats = idx.get_stats()
    return json.dumps({"stats": stats, "papers": papers}, indent=2)


@mcp.tool()
def remove_paper(arxiv_id: str) -> str:
    """
    Remove a paper from the full-text index (does not delete the PDF file).

    Args:
        arxiv_id: arXiv paper ID to remove.
    """
    idx = _get_index()
    if not idx.is_indexed(arxiv_id):
        return json.dumps({"error": f"{arxiv_id} is not in the index."})
    idx.remove_paper(arxiv_id)
    return json.dumps({"status": "removed", "arxiv_id": arxiv_id})


@mcp.tool()
def index_stats() -> str:
    """Get statistics: total papers, chunks, pages indexed, and database path."""
    idx = _get_index()
    return json.dumps(idx.get_stats(), indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# SEMANTIC SCHOLAR TOOLS
# ═══════════════════════════════════════════════════════════════════════════

SEMANTIC_SCHOLAR_API_BASE = "https://api.semanticscholar.org/graph/v1"
SS_FIELDS = "title,abstract,authors,year,externalIds,url,openAccessPdf,citationCount,fieldsOfStudy"
_ss_rate_limit = _make_rate_limiter(1.0)  # Semantic Scholar


def _ss_request(url: str, params: dict, max_retries: int = 3) -> requests.Response:
    """Make a Semantic Scholar API request with retry on 429."""
    for attempt in range(max_retries):
        _ss_rate_limit()
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 429:
            wait = min(2 ** (attempt + 1), 30)
            logger.warning(f"Semantic Scholar 429, retrying in {wait}s (attempt {attempt + 1})")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()  # raise the last 429
    return resp


@mcp.tool()
def search_semantic_scholar(
    query: str,
    max_results: int = 10,
    year: str = None,
    fields_of_study: str = None,
) -> str:
    """
    Search Semantic Scholar for papers across all major academic sources
    (arXiv, PubMed, ACM, IEEE, Springer, etc.).

    This is a cross-repository search that covers far more sources than arXiv alone.

    Args:
        query: Search terms matched against paper title and abstract.
               Supports + (require), - (exclude), | (OR), and "quotes" for phrases.
               Example: "policy as code" +LLM -robotics
        max_results: 1-100 results to return (default 10).
        year: Optional year filter. Examples: "2024", "2020-2024", "2020-", "-2023".
        fields_of_study: Optional comma-separated fields. Examples:
                         "Computer Science", "Computer Science,Linguistics".

    Returns:
        JSON with total results and paper entries including title, abstract,
        authors, year, citation count, external IDs (arXiv, DOI, etc.),
        and open-access PDF links when available.
    """
    max_results = _clamp(max_results, 1, 100)

    params = {
        "query": query,
        "limit": max_results,
        "fields": SS_FIELDS,
    }
    if year:
        params["year"] = year
    if fields_of_study:
        params["fieldsOfStudy"] = fields_of_study

    logger.info(f"Semantic Scholar search: {params}")
    resp = _ss_request(
        f"{SEMANTIC_SCHOLAR_API_BASE}/paper/search/bulk",
        params,
    )
    resp.raise_for_status()
    data = resp.json()

    entries = []
    for paper in data.get("data", []):
        authors = [a.get("name", "") for a in paper.get("authors", [])]
        ext_ids = paper.get("externalIds") or {}
        pdf_info = paper.get("openAccessPdf") or {}
        entries.append({
            "paper_id": paper.get("paperId", ""),
            "title": paper.get("title", ""),
            "authors": authors,
            "year": paper.get("year"),
            "abstract": paper.get("abstract", ""),
            "citation_count": paper.get("citationCount", 0),
            "fields_of_study": paper.get("fieldsOfStudy") or [],
            "arxiv_id": ext_ids.get("ArXiv", ""),
            "doi": ext_ids.get("DOI", ""),
            "corpus_id": ext_ids.get("CorpusId", ""),
            "url": paper.get("url", ""),
            "open_access_pdf": pdf_info.get("url", ""),
        })

    return json.dumps({
        "total_results": data.get("total", 0),
        "results_count": len(entries),
        "entries": entries,
    }, indent=2)


@mcp.tool()
def get_semantic_scholar_paper(paper_id: str) -> str:
    """
    Get detailed metadata for a paper from Semantic Scholar.

    Args:
        paper_id: Semantic Scholar paper ID, arXiv ID (prefix with "ArXiv:"),
                  DOI (prefix with "DOI:"), or Corpus ID (prefix with "CorpusId:").
                  Examples: "ArXiv:2509.07006", "DOI:10.1145/1234", "649def34f8be52c8b66281af98ae884c09aef38b"
    """
    resp = _ss_request(
        f"{SEMANTIC_SCHOLAR_API_BASE}/paper/{paper_id}",
        {"fields": SS_FIELDS + ",references,citations"},
    )
    resp.raise_for_status()
    paper = resp.json()

    authors = [a.get("name", "") for a in paper.get("authors", [])]
    ext_ids = paper.get("externalIds") or {}
    pdf_info = paper.get("openAccessPdf") or {}

    refs = []
    for r in (paper.get("references") or [])[:20]:
        refs.append({
            "paper_id": r.get("paperId", ""),
            "title": r.get("title", ""),
        })

    cites = []
    for c in (paper.get("citations") or [])[:20]:
        cites.append({
            "paper_id": c.get("paperId", ""),
            "title": c.get("title", ""),
        })

    return json.dumps({
        "paper_id": paper.get("paperId", ""),
        "title": paper.get("title", ""),
        "authors": authors,
        "year": paper.get("year"),
        "abstract": paper.get("abstract", ""),
        "citation_count": paper.get("citationCount", 0),
        "fields_of_study": paper.get("fieldsOfStudy") or [],
        "arxiv_id": ext_ids.get("ArXiv", ""),
        "doi": ext_ids.get("DOI", ""),
        "url": paper.get("url", ""),
        "open_access_pdf": pdf_info.get("url", ""),
        "references": refs,
        "citations": cites,
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# DSPACE@MIT TOOLS
# ═══════════════════════════════════════════════════════════════════════════

DSPACE_MIT_BASE = "https://dspace.mit.edu/rest"


def _dspace_parse_item(item: dict) -> dict:
    """Parse a DSpace item with metadata into a clean dict."""
    metadata = item.get("metadata", [])
    authors = []
    meta_dict = {}
    for m in metadata:
        key = m.get("key", "")
        value = m.get("value", "")
        if key == "dc.contributor.author":
            authors.append(value)
        elif key not in meta_dict:
            meta_dict[key] = value

    return {
        "uuid": item.get("uuid", ""),
        "title": meta_dict.get("dc.title", item.get("name", "")),
        "authors": authors,
        "abstract": meta_dict.get("dc.description.abstract", ""),
        "date_issued": meta_dict.get("dc.date.issued", ""),
        "type": meta_dict.get("dc.type", ""),
        "department": meta_dict.get("dc.contributor.department", ""),
        "handle_url": f"https://hdl.handle.net/{item.get('handle', '')}",
        "publisher": meta_dict.get("dc.publisher", ""),
        "journal": meta_dict.get("dc.relation.journal", ""),
        "doi": meta_dict.get("dc.relation.isversionof", ""),
    }


def _dspace_search_field(field: str, query: str, limit: int) -> list:
    """Search a single DSpace metadata field."""
    resp = requests.get(
        f"{DSPACE_MIT_BASE}/filtered-items",
        params={
            "query_field[]": field,
            "query_op[]": "contains",
            "query_val[]": query,
            "limit": limit,
            "expand": "metadata",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


@mcp.tool()
def search_mit_dspace(
    query: str,
    max_results: int = 10,
    search_field: str = "all",
) -> str:
    """
    Search MIT's DSpace institutional repository for technical reports,
    theses, white papers, and peer-reviewed articles by MIT researchers.

    This searches MIT's collection of 60,000+ works across all departments.

    Args:
        query: Search term to find in item metadata.
        max_results: 1-100 results to return (default 10).
        search_field: Where to search. Options:
                      "all" (default) — searches title and abstract, merges results.
                      "title" — dc.title only.
                      "abstract" — dc.description.abstract only.
                      "author" — dc.contributor.author only.
                      "subject" — dc.subject only.

    Returns:
        JSON with matching items including title, authors, abstract,
        department, date, handle URL, and document type.
    """
    max_results = _clamp(max_results, 1, 100)

    logger.info(f"DSpace@MIT search: {query} (field={search_field})")

    if search_field == "all":
        # Search title and abstract separately, merge and deduplicate
        title_items = _dspace_search_field("dc.title", query, max_results)
        abstract_items = _dspace_search_field("dc.description.abstract", query, max_results)

        seen_uuids = set()
        merged = []
        for item in title_items + abstract_items:
            uuid = item.get("uuid", "")
            if uuid not in seen_uuids:
                seen_uuids.add(uuid)
                merged.append(item)
        items = merged[:max_results]
    else:
        field_map = {
            "title": "dc.title",
            "abstract": "dc.description.abstract",
            "author": "dc.contributor.author",
            "subject": "dc.subject",
        }
        dc_field = field_map.get(search_field, "dc.title")
        items = _dspace_search_field(dc_field, query, max_results)

    entries = [_dspace_parse_item(item) for item in items]

    return json.dumps({
        "total_results": len(entries),
        "query": query,
        "search_field": search_field,
        "entries": entries,
    }, indent=2)


@mcp.tool()
def get_mit_dspace_item(uuid: str) -> str:
    """
    Get full metadata for a specific item in MIT's DSpace repository.

    Args:
        uuid: The UUID of the item (from search results).
    """
    resp = requests.get(
        f"{DSPACE_MIT_BASE}/items/{uuid}",
        params={"expand": "metadata,bitstreams"},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    item = resp.json()

    metadata = item.get("metadata", [])
    meta_dict = {}
    authors = []
    for m in metadata:
        key = m.get("key", "")
        value = m.get("value", "")
        if key == "dc.contributor.author":
            authors.append(value)
        elif key not in meta_dict:
            meta_dict[key] = value

    bitstreams = []
    for b in item.get("bitstreams", []):
        bitstreams.append({
            "name": b.get("name", ""),
            "size_bytes": b.get("sizeBytes", 0),
            "mime_type": b.get("mimeType", ""),
            "retrieve_link": f"https://dspace.mit.edu{b.get('retrieveLink', '')}",
        })

    return json.dumps({
        "uuid": item.get("uuid", ""),
        "title": meta_dict.get("dc.title", item.get("name", "")),
        "authors": authors,
        "abstract": meta_dict.get("dc.description.abstract", ""),
        "date_issued": meta_dict.get("dc.date.issued", ""),
        "type": meta_dict.get("dc.type", ""),
        "department": meta_dict.get("dc.contributor.department", ""),
        "handle_url": f"https://hdl.handle.net/{item.get('handle', '')}",
        "publisher": meta_dict.get("dc.publisher", ""),
        "journal": meta_dict.get("dc.relation.journal", ""),
        "doi": meta_dict.get("dc.relation.isversionof", ""),
        "bitstreams": bitstreams,
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# DSPACE 8 GENERIC HELPERS (Harvard, Cornell, Penn)
# ═══════════════════════════════════════════════════════════════════════════

DSPACE8_REPOS = {
    "harvard": {
        "name": "Harvard DASH",
        "api_base": "https://dash.harvard.edu/server/api",
        "web_base": "https://dash.harvard.edu",
        "description": "58,000+ works: articles, working papers, theses, case studies by Harvard researchers",
    },
    "cornell": {
        "name": "Cornell eCommons",
        "api_base": "https://ecommons.cornell.edu/server/api",
        "web_base": "https://ecommons.cornell.edu",
        "description": "24,000+ works: CS, engineering, policy research by Cornell researchers",
    },
    "penn": {
        "name": "Penn ScholarlyCommons",
        "api_base": "https://repository.upenn.edu/server/api",
        "web_base": "https://repository.upenn.edu",
        "description": "43,000+ works: articles, theses, datasets by UPenn researchers",
    },
}


def _dspace8_parse_item(item: dict, web_base: str) -> dict:
    """Parse a DSpace 8 item into a clean dict."""
    metadata = item.get("metadata", {})

    def _first(key):
        vals = metadata.get(key, [])
        return vals[0].get("value", "") if vals else ""

    def _all(key):
        return [v.get("value", "") for v in metadata.get(key, [])]

    return {
        "uuid": item.get("uuid", ""),
        "title": _first("dc.title") or item.get("name", ""),
        "authors": _all("dc.contributor.author"),
        "abstract": _first("dc.description.abstract"),
        "date_issued": _first("dc.date.issued"),
        "type": _first("dc.type"),
        "department": _first("dc.contributor.department")
                      or _first("dc.contributor.other"),
        "handle_url": f"{web_base}/handle/{item.get('handle', '')}",
        "publisher": _first("dc.publisher"),
        "journal": _first("dc.relation.journal")
                   or _first("dc.source"),
        "doi": _first("dc.identifier.doi")
               or _first("dc.relation.isversionof"),
        "subjects": _all("dc.subject"),
    }


def _dspace8_search(repo_key: str, query: str, max_results: int) -> dict:
    """Search a DSpace 8 repository."""
    repo = DSPACE8_REPOS[repo_key]
    api_base = repo["api_base"]

    logger.info(f"{repo['name']} search: {query}")
    resp = requests.get(
        f"{api_base}/discover/search/objects",
        params={"query": query, "dsoType": "ITEM", "size": max_results},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    sr = data.get("_embedded", {}).get("searchResult", {})
    page = sr.get("page", {})
    total = page.get("totalElements", 0)

    entries = []
    for obj in sr.get("_embedded", {}).get("objects", []):
        item = obj.get("_embedded", {}).get("indexableObject", {})
        entries.append(_dspace8_parse_item(item, repo["web_base"]))

    return {
        "total_results": total,
        "results_count": len(entries),
        "source": repo["name"],
        "query": query,
        "entries": entries,
    }


def _dspace8_get_item(repo_key: str, uuid: str) -> dict:
    """Get full metadata + bitstreams for a DSpace 8 item."""
    repo = DSPACE8_REPOS[repo_key]
    api_base = repo["api_base"]

    resp = requests.get(
        f"{api_base}/core/items/{uuid}",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    item = resp.json()

    bitstreams = []
    try:
        bs_resp = requests.get(
            f"{api_base}/core/items/{uuid}/bundles",
            headers={"Accept": "application/json"},
            timeout=30,
        )
        bs_resp.raise_for_status()
        bundles = bs_resp.json().get("_embedded", {}).get("bundles", [])
        for bundle in bundles:
            if bundle.get("name") == "ORIGINAL":
                bs_list_resp = requests.get(
                    f"{api_base}/core/bundles/{bundle['uuid']}/bitstreams",
                    headers={"Accept": "application/json"},
                    timeout=30,
                )
                bs_list_resp.raise_for_status()
                for b in bs_list_resp.json().get("_embedded", {}).get("bitstreams", []):
                    bitstreams.append({
                        "name": b.get("name", ""),
                        "size_bytes": b.get("sizeBytes", 0),
                        "mime_type": b.get("metadata", {}).get(
                            "dc.format.mimetype", [{}]
                        )[0].get("value", "") if b.get("metadata") else "",
                        "retrieve_link": f"{api_base}/core/bitstreams/{b['uuid']}/content",
                    })
    except Exception as e:
        logger.debug("Bitstream fetch skipped: %s", e)

    result = _dspace8_parse_item(item, repo["web_base"])
    result["bitstreams"] = bitstreams
    return result


# ── Harvard DASH ────────────────────────────────────────────────────────

@mcp.tool()
def search_harvard_dash(query: str, max_results: int = 10) -> str:
    """
    Search Harvard's DASH open-access repository for scholarly works.
    58,000+ works: articles, working papers, theses, case studies.

    Args:
        query: Search terms (matched against title, abstract, metadata).
        max_results: 1-100 results to return (default 10).
    """
    max_results = _clamp(max_results, 1, 100)
    return json.dumps(_dspace8_search("harvard", query, max_results), indent=2)


@mcp.tool()
def get_harvard_dash_item(uuid: str) -> str:
    """
    Get full metadata and downloadable files for a Harvard DASH item.

    Args:
        uuid: The UUID of the item (from search results).
    """
    return json.dumps(_dspace8_get_item("harvard", uuid), indent=2)


# ── Cornell eCommons ────────────────────────────────────────────────────

@mcp.tool()
def search_cornell_ecommons(query: str, max_results: int = 10) -> str:
    """
    Search Cornell's eCommons repository for scholarly works.
    24,000+ works: strong in CS, engineering, and policy research.
    Includes theses, articles, technical reports, and datasets.

    Args:
        query: Search terms (matched against title, abstract, metadata).
        max_results: 1-100 results to return (default 10).
    """
    max_results = _clamp(max_results, 1, 100)
    return json.dumps(_dspace8_search("cornell", query, max_results), indent=2)


@mcp.tool()
def get_cornell_ecommons_item(uuid: str) -> str:
    """
    Get full metadata and downloadable files for a Cornell eCommons item.

    Args:
        uuid: The UUID of the item (from search results).
    """
    return json.dumps(_dspace8_get_item("cornell", uuid), indent=2)


# ── Penn ScholarlyCommons ──────────────────────────────────────────────

@mcp.tool()
def search_penn_scholarly(query: str, max_results: int = 10) -> str:
    """
    Search UPenn's ScholarlyCommons repository for scholarly works.
    43,000+ works: articles, theses, datasets, conference papers.
    Strong in AI ethics, governance, and policy research.

    Args:
        query: Search terms (matched against title, abstract, metadata).
        max_results: 1-100 results to return (default 10).
    """
    max_results = _clamp(max_results, 1, 100)
    return json.dumps(_dspace8_search("penn", query, max_results), indent=2)


@mcp.tool()
def get_penn_scholarly_item(uuid: str) -> str:
    """
    Get full metadata and downloadable files for a Penn ScholarlyCommons item.

    Args:
        uuid: The UUID of the item (from search results).
    """
    return json.dumps(_dspace8_get_item("penn", uuid), indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# DOI TOOLS (Crossref, DataCite, Unpaywall, Content Negotiation)
# ═══════════════════════════════════════════════════════════════════════════

CROSSREF_API_BASE = "https://api.crossref.org/works"
DATACITE_API_BASE = "https://api.datacite.org/dois"
UNPAYWALL_API_BASE = "https://api.unpaywall.org/v2"
UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "research-mcp@example.com")

_DOI_PREFIXES = ["https://doi.org/", "http://doi.org/", "doi:"]


def _clean_doi(doi: str) -> str:
    """Strip common URL/scheme prefixes from a DOI string."""
    doi = doi.strip()
    for prefix in _DOI_PREFIXES:
        if doi.lower().startswith(prefix.lower()):
            doi = doi[len(prefix):]
    return doi


def _parse_crossref(item: dict) -> dict:
    """Parse a Crossref work item into a clean dict."""
    authors = []
    for a in item.get("author", []):
        name = f"{a.get('given', '')} {a.get('family', '')}".strip()
        if name:
            authors.append(name)

    container = item.get("container-title", [])
    journal = container[0] if container else ""

    published_parts = item.get("published-print", item.get("published-online", {}))
    date_parts = published_parts.get("date-parts", [[]])[0]
    date_str = "-".join(str(d) for d in date_parts) if date_parts else ""

    abstract = item.get("abstract", "")
    # Crossref abstracts sometimes contain JATS XML tags
    if abstract:
        abstract = re.sub(r"<[^>]+>", "", abstract).strip()

    return {
        "doi": item.get("DOI", ""),
        "title": item.get("title", [""])[0] if item.get("title") else "",
        "authors": authors,
        "abstract": abstract,
        "journal": journal,
        "publisher": item.get("publisher", ""),
        "type": item.get("type", ""),
        "published": date_str,
        "url": item.get("URL", ""),
        "citation_count": item.get("is-referenced-by-count", 0),
        "references_count": item.get("references-count", 0),
        "issn": item.get("ISSN", []),
        "subject": item.get("subject", []),
        "license": [
            lic.get("URL", "") for lic in item.get("license", [])
        ],
    }


@mcp.tool()
def resolve_doi(doi: str) -> str:
    """
    Resolve a DOI to get full metadata from Crossref (articles) or DataCite (datasets).

    Retrieves title, authors, abstract, journal, publisher, citation count,
    license, and more.

    Args:
        doi: A DOI string, e.g. "10.1145/3649835" or "10.1038/s41586-020-2649-2".
             Can include "https://doi.org/" prefix — it will be stripped.

    Returns:
        JSON with full metadata from Crossref or DataCite.
    """
    doi = _clean_doi(doi)

    # Try Crossref first (covers most scholarly articles)
    try:
        resp = requests.get(
            f"{CROSSREF_API_BASE}/{doi}",
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            item = data.get("message", {})
            result = _parse_crossref(item)
            result["source"] = "crossref"
            return json.dumps(result, indent=2)
    except Exception as e:
        logger.warning(f"Crossref lookup failed for {doi}: {e}")

    # Fallback to DataCite (datasets, software, etc.)
    try:
        resp = requests.get(
            f"{DATACITE_API_BASE}/{doi}",
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            attrs = data.get("attributes", {})
            creators = [
                c.get("name", "") or f"{c.get('givenName', '')} {c.get('familyName', '')}".strip()
                for c in attrs.get("creators", [])
            ]
            return json.dumps({
                "doi": doi,
                "title": attrs.get("titles", [{}])[0].get("title", ""),
                "authors": creators,
                "abstract": (attrs.get("descriptions", [{}])[0].get("description", "")
                             if attrs.get("descriptions") else ""),
                "publisher": attrs.get("publisher", ""),
                "type": attrs.get("types", {}).get("resourceTypeGeneral", ""),
                "published": str(attrs.get("publicationYear", "")),
                "url": attrs.get("url", ""),
                "source": "datacite",
            }, indent=2)
    except Exception as e:
        logger.warning(f"DataCite lookup failed for {doi}: {e}")

    return json.dumps({"error": f"Could not resolve DOI: {doi}", "doi": doi})


@mcp.tool()
def search_crossref(
    query: str,
    max_results: int = 10,
    filter_type: str = None,
    sort: str = "relevance",
) -> str:
    """
    Search Crossref for scholarly works by query across all publishers
    (ACM, IEEE, Springer, Elsevier, Wiley, etc.).

    Crossref indexes 150M+ works. Use this to find papers by title/author/topic
    when you have a DOI or want to search beyond arXiv.

    Args:
        query: Search terms matched against titles, authors, abstracts, and full text.
        max_results: 1-100 results to return (default 10).
        filter_type: Optional type filter. Examples: "journal-article", "proceedings-article",
                     "book-chapter", "dissertation". Leave empty for all types.
        sort: Sort order. Options: "relevance" (default), "published", "is-referenced-by-count".

    Returns:
        JSON with total results and entries including title, authors, journal,
        DOI, citation count, and publication date.
    """
    max_results = _clamp(max_results, 1, 100)

    params = {
        "query": query,
        "rows": max_results,
        "sort": sort,
        "order": "desc" if sort != "relevance" else None,
    }
    if filter_type:
        params["filter"] = f"type:{filter_type}"

    # Remove None values
    params = {k: v for k, v in params.items() if v is not None}

    logger.info(f"Crossref search: {params}")
    resp = requests.get(
        CROSSREF_API_BASE,
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("message", {})

    entries = [_parse_crossref(item) for item in data.get("items", [])]

    return json.dumps({
        "total_results": data.get("total-results", 0),
        "results_count": len(entries),
        "query": query,
        "entries": entries,
    }, indent=2)


@mcp.tool()
def get_doi_citation(
    doi: str,
    style: str = "apa",
    format: str = "text",
) -> str:
    """
    Get a formatted citation for a DOI using content negotiation.

    Args:
        doi: The DOI string, e.g. "10.1145/3649835".
        style: Citation style. Options: "apa", "chicago-author-date", "ieee",
               "harvard-cite-them-right", "modern-language-association".
               Default: "apa".
        format: Output format. Options:
                "text" — plain text citation (default).
                "bibtex" — BibTeX entry.
                "citeproc" — structured JSON (CSL-JSON).

    Returns:
        The formatted citation string or JSON.
    """
    doi = _clean_doi(doi)

    mime_map = {
        "text": f"text/x-bibliography; style={style}",
        "bibtex": "application/x-bibtex",
        "citeproc": "application/citeproc+json",
    }
    accept = mime_map.get(format, f"text/x-bibliography; style={style}")

    resp = requests.get(
        f"https://doi.org/{doi}",
        headers={"Accept": accept},
        timeout=30,
        allow_redirects=True,
    )
    resp.raise_for_status()

    if format == "citeproc":
        return json.dumps(resp.json(), indent=2)
    return resp.text.strip()


@mcp.tool()
def download_paper_by_doi(
    doi: str,
    auto_index: bool = True,
    download_dir: str = None,
) -> str:
    """
    Find and download the open-access PDF for a paper given its DOI,
    then index it for full-text search.

    Uses multiple sources to find free/legal PDFs:
      1. Unpaywall (if UNPAYWALL_EMAIL env var is set to your real email)
      2. Semantic Scholar open-access PDF links
      3. Crossref license check (some CC-licensed works have direct links)

    Args:
        doi: The DOI of the paper, e.g. "10.1145/3649835".
        auto_index: If True (default), extract and index the text after download.
        download_dir: Custom download directory. Default: ~/arxiv-papers.

    Returns:
        JSON with file path, metadata, and indexing status.
        If no open-access PDF exists, returns an error with the DOI metadata.
    """
    doi = _clean_doi(doi)

    # Step 1: Get metadata from Crossref
    metadata = {}
    try:
        cr_resp = requests.get(
            f"{CROSSREF_API_BASE}/{doi}",
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if cr_resp.status_code == 200:
            metadata = _parse_crossref(cr_resp.json().get("message", {}))
    except Exception as e:
        logger.debug("Crossref metadata fetch skipped: %s", e)

    # Step 2: Find open-access PDF via multiple sources
    pdf_url = None
    oa_status = "unknown"
    pdf_source = ""

    # Source A: Unpaywall (requires real email in UNPAYWALL_EMAIL env var)
    if UNPAYWALL_EMAIL and "example.com" not in UNPAYWALL_EMAIL:
        try:
            up_resp = requests.get(
                f"{UNPAYWALL_API_BASE}/{doi}",
                params={"email": UNPAYWALL_EMAIL},
                timeout=30,
            )
            if up_resp.status_code == 200:
                up_data = up_resp.json()
                oa_status = up_data.get("oa_status", "closed")
                best_loc = up_data.get("best_oa_location")
                if best_loc:
                    pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")
                    pdf_source = "unpaywall"
                if not pdf_url:
                    for loc in up_data.get("oa_locations", []):
                        url = loc.get("url_for_pdf") or loc.get("url")
                        if url:
                            pdf_url = url
                            pdf_source = "unpaywall"
                            break
        except Exception as e:
            logger.warning(f"Unpaywall lookup failed for {doi}: {e}")

    # Source B: Semantic Scholar openAccessPdf
    if not pdf_url:
        try:
            _ss_rate_limit()
            ss_resp = requests.get(
                f"{SEMANTIC_SCHOLAR_API_BASE}/paper/DOI:{doi}",
                params={"fields": "openAccessPdf,externalIds,title,authors"},
                timeout=30,
            )
            if ss_resp.status_code == 200:
                ss_data = ss_resp.json()
                oa_pdf = ss_data.get("openAccessPdf") or {}
                if oa_pdf.get("url"):
                    pdf_url = oa_pdf["url"]
                    pdf_source = "semantic_scholar"
                # Also check if there's an arXiv version we can grab
                ext_ids = ss_data.get("externalIds") or {}
                arxiv_id = ext_ids.get("ArXiv", "")
                if not pdf_url and arxiv_id:
                    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
                    pdf_source = "arxiv_via_semantic_scholar"
        except Exception as e:
            logger.warning(f"Semantic Scholar lookup failed for {doi}: {e}")

    if not pdf_url:
        return json.dumps({
            "error": "No open-access PDF found for this DOI.",
            "doi": doi,
            "oa_status": oa_status,
            "metadata": metadata,
            "suggestion": "This paper may require institutional access. "
                          "Set UNPAYWALL_EMAIL env var to your real email for better OA coverage, "
                          "or check if an arXiv preprint exists via search_semantic_scholar.",
        }, indent=2)

    # Step 3: Download the PDF
    target_dir = _get_download_dir(download_dir)
    clean_doi = doi.replace("/", "_").replace(":", "_")
    filepath = target_dir / f"doi_{clean_doi}.pdf"

    try:
        logger.info(f"Downloading PDF for DOI {doi} from {pdf_url}")
        pdf_resp = requests.get(pdf_url, timeout=120, stream=True, headers={
            "User-Agent": "research-mcp/1.0 (mailto:{})".format(UNPAYWALL_EMAIL),
        })
        pdf_resp.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in pdf_resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        return json.dumps({
            "error": f"PDF download failed: {e}",
            "doi": doi,
            "pdf_url": pdf_url,
            "metadata": metadata,
        }, indent=2)

    file_size = filepath.stat().st_size
    result = {
        "status": "downloaded",
        "file_path": str(filepath),
        "file_size_bytes": file_size,
        "file_size_mb": round(file_size / (1024 * 1024), 2),
        "doi": doi,
        "title": metadata.get("title", ""),
        "authors": metadata.get("authors", []),
        "oa_status": oa_status,
        "pdf_url": pdf_url,
        "pdf_source": pdf_source,
        "indexed": False,
    }

    if auto_index:
        paper_meta = {
            "arxiv_id": f"doi:{doi}",
            "title": result["title"],
            "authors": result["authors"],
            "summary": metadata.get("abstract", ""),
            "categories": metadata.get("subject", []),
            "published": metadata.get("published", ""),
        }
        _index_downloaded_pdf(filepath, paper_meta, result, f"DOI {doi}")

    return json.dumps(result, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# OPENALEX TOOLS
# ═══════════════════════════════════════════════════════════════════════════
# OpenAlex (https://openalex.org) — Free, CC0 catalog of 250M+ scholarly works.
# Polite pool: include email in User-Agent or `mailto` param for higher rate limits.

OPENALEX_API_BASE = "https://api.openalex.org"
OPENALEX_EMAIL = os.environ.get("OPENALEX_EMAIL", "")


def _openalex_params(extra: dict = None) -> dict:
    params = dict(extra or {})
    if OPENALEX_EMAIL:
        params["mailto"] = OPENALEX_EMAIL
    return params


def _parse_openalex_work(work: dict) -> dict:
    """Parse an OpenAlex work into a clean dict."""
    authors = []
    institutions = []
    for authorship in work.get("authorships", []):
        a = authorship.get("author", {}) or {}
        if a.get("display_name"):
            authors.append(a["display_name"])
        for inst in authorship.get("institutions", []) or []:
            name = inst.get("display_name")
            if name and name not in institutions:
                institutions.append(name)

    # Concepts (deprecated but still useful) and topics
    concepts = [
        c.get("display_name", "") for c in (work.get("concepts") or [])[:5]
        if c.get("display_name")
    ]
    topics = [
        t.get("display_name", "") for t in (work.get("topics") or [])[:5]
        if t.get("display_name")
    ]

    # Best OA location for PDF
    oa_location = work.get("best_oa_location") or {}
    pdf_url = oa_location.get("pdf_url") or ""

    # Build a clean ID (strip URL prefix)
    work_id = work.get("id", "").replace("https://openalex.org/", "")

    return {
        "openalex_id": work_id,
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "title": work.get("title") or work.get("display_name", ""),
        "authors": authors,
        "institutions": institutions,
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date", ""),
        "abstract": _openalex_abstract(work.get("abstract_inverted_index")),
        "type": work.get("type", ""),
        "venue": (work.get("primary_location") or {}).get("source", {}).get("display_name", "") if work.get("primary_location") else "",
        "citation_count": work.get("cited_by_count", 0),
        "concepts": concepts,
        "topics": topics,
        "is_oa": work.get("open_access", {}).get("is_oa", False),
        "oa_status": work.get("open_access", {}).get("oa_status", ""),
        "pdf_url": pdf_url,
        "url": (work.get("primary_location") or {}).get("landing_page_url", ""),
    }


def _openalex_abstract(inverted_index: dict) -> str:
    """Reconstruct abstract from OpenAlex's inverted index format."""
    if not inverted_index:
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(w for _, w in word_positions)


@mcp.tool()
def search_openalex(
    query: str,
    max_results: int = 10,
    year_from: int = None,
    year_to: int = None,
    concept: str = None,
    institution: str = None,
    open_access_only: bool = False,
    sort: str = "relevance_score:desc",
) -> str:
    """
    Search OpenAlex — a free, CC0 catalog of 250M+ scholarly works covering
    all disciplines. Better than Semantic Scholar for institution affiliations,
    funder data, concept/topic taxonomies, and citation networks.

    Args:
        query: Search terms (matched against title, abstract, full text).
        max_results: 1-200 results to return (default 10).
        year_from: Optional earliest publication year (e.g. 2020).
        year_to: Optional latest publication year (e.g. 2025).
        concept: Optional concept name filter (e.g. "Computer security",
                 "Access control", "Machine learning").
        institution: Optional institution name (e.g. "Stanford University").
        open_access_only: If True, only return open-access works.
        sort: Sort order. Options:
              "relevance_score:desc" (default), "cited_by_count:desc",
              "publication_date:desc", "publication_date:asc".

    Returns:
        JSON with total results and entries including title, authors,
        institutions, year, abstract, citation count, concepts/topics,
        and PDF URL when available.
    """
    max_results = _clamp(max_results, 1, 200)

    # Build filter string
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if open_access_only:
        filters.append("is_oa:true")
    if concept:
        filters.append(f"concepts.display_name.search:{concept}")
    if institution:
        filters.append(f"institutions.display_name.search:{institution}")

    params = _openalex_params({
        "search": query,
        "per-page": max_results,
        "sort": sort,
    })
    if filters:
        params["filter"] = ",".join(filters)

    logger.info(f"OpenAlex search: {params}")
    resp = requests.get(
        f"{OPENALEX_API_BASE}/works",
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    entries = [_parse_openalex_work(w) for w in data.get("results", [])]

    return json.dumps({
        "total_results": data.get("meta", {}).get("count", 0),
        "results_count": len(entries),
        "query": query,
        "entries": entries,
    }, indent=2)


@mcp.tool()
def get_openalex_work(work_id: str) -> str:
    """
    Get full metadata for an OpenAlex work, including references and
    related works.

    Args:
        work_id: OpenAlex work ID (e.g. "W2741809807"), DOI (prefix with
                 "doi:"), or full OpenAlex URL.

    Returns:
        JSON with full metadata, abstract, references, and related works.
    """
    work_id = work_id.strip()
    work_id = work_id.replace("https://openalex.org/", "")
    if work_id.lower().startswith("doi:"):
        work_id = "doi:" + work_id[4:].replace("https://doi.org/", "")

    resp = requests.get(
        f"{OPENALEX_API_BASE}/works/{work_id}",
        params=_openalex_params(),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    work = resp.json()

    result = _parse_openalex_work(work)

    # Add references and related works (just IDs and titles)
    refs = []
    for ref_id in (work.get("referenced_works") or [])[:30]:
        refs.append(ref_id.replace("https://openalex.org/", ""))
    result["referenced_works"] = refs

    related = []
    for rel_id in (work.get("related_works") or [])[:20]:
        related.append(rel_id.replace("https://openalex.org/", ""))
    result["related_works"] = related

    return json.dumps(result, indent=2)


@mcp.tool()
def search_openalex_authors(query: str, max_results: int = 10) -> str:
    """
    Search OpenAlex for authors by name. Useful for disambiguating authors
    and finding all of their works.

    Args:
        query: Author name (e.g. "Scott Stoller").
        max_results: 1-50 results (default 10).

    Returns:
        JSON with author entries including ID, name, affiliations,
        works count, and citation count.
    """
    max_results = _clamp(max_results, 1, 50)

    params = _openalex_params({
        "search": query,
        "per-page": max_results,
    })

    resp = requests.get(
        f"{OPENALEX_API_BASE}/authors",
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    entries = []
    for a in data.get("results", []):
        last_inst = (a.get("last_known_institution") or {}).get("display_name", "")
        entries.append({
            "author_id": (a.get("id") or "").replace("https://openalex.org/", ""),
            "name": a.get("display_name", ""),
            "orcid": a.get("orcid", ""),
            "last_known_institution": last_inst,
            "works_count": a.get("works_count", 0),
            "cited_by_count": a.get("cited_by_count", 0),
            "h_index": (a.get("summary_stats") or {}).get("h_index", 0),
        })

    return json.dumps({
        "total_results": data.get("meta", {}).get("count", 0),
        "results_count": len(entries),
        "entries": entries,
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# CORE TOOLS
# ═══════════════════════════════════════════════════════════════════════════
# CORE (https://core.ac.uk) — Largest aggregator of OA papers (200M+ works
# across 11,000+ repositories). Best source for technical reports, working
# papers, and grey literature. Requires a free API key from
# https://core.ac.uk/services/api

CORE_API_BASE = "https://api.core.ac.uk/v3"
CORE_API_KEY = os.environ.get("CORE_API_KEY", "")
_core_rate_limit = _make_rate_limiter(6.5)  # CORE (~10 req/min free tier)


def _core_headers() -> dict:
    h = {"Accept": "application/json"}
    if CORE_API_KEY:
        h["Authorization"] = f"Bearer {CORE_API_KEY}"
    return h


def _parse_core_work(work: dict) -> dict:
    """Parse a CORE work into a clean dict."""
    authors = []
    for a in work.get("authors", []) or []:
        name = a.get("name", "") if isinstance(a, dict) else str(a)
        if name:
            authors.append(name)

    # Find best PDF link
    download_url = work.get("downloadUrl", "")
    fulltext_urls = []
    for link in work.get("links", []) or []:
        if isinstance(link, dict) and link.get("type") == "download":
            fulltext_urls.append(link.get("url", ""))

    # Repository info
    repo = (work.get("dataProviders") or [{}])[0] if work.get("dataProviders") else {}
    repo_name = repo.get("name", "") if isinstance(repo, dict) else ""

    return {
        "core_id": str(work.get("id", "")),
        "doi": work.get("doi", ""),
        "title": work.get("title", ""),
        "authors": authors,
        "abstract": work.get("abstract", ""),
        "year": work.get("yearPublished"),
        "publication_date": work.get("publishedDate", ""),
        "type": work.get("documentType", ""),
        "publisher": work.get("publisher", ""),
        "repository": repo_name,
        "language": (work.get("language") or {}).get("name", "") if work.get("language") else "",
        "download_url": download_url,
        "fulltext_urls": fulltext_urls,
        "url": work.get("sourceFulltextUrls", [""])[0] if work.get("sourceFulltextUrls") else "",
    }


@mcp.tool()
def search_core(
    query: str,
    max_results: int = 10,
    year_from: int = None,
    year_to: int = None,
    document_type: str = None,
) -> str:
    """
    Search CORE — the world's largest aggregator of open access research,
    with 200M+ works from 11,000+ repositories. Best source for technical
    reports, working papers, theses, and grey literature that aren't on
    arXiv or in traditional journals.

    Requires CORE_API_KEY environment variable. Get a free key at
    https://core.ac.uk/services/api

    Args:
        query: Search terms. CORE supports field-specific syntax:
               'title:"policy mining"', 'authors:"Stoller"', etc.
        max_results: 1-100 results (default 10).
        year_from: Optional earliest publication year.
        year_to: Optional latest publication year.
        document_type: Optional filter. Examples: "research", "thesis",
                       "report", "preprint", "conference", "book".

    Returns:
        JSON with works including title, authors, abstract, repository,
        and direct PDF download URL.
    """
    if not CORE_API_KEY:
        return json.dumps({
            "error": "CORE_API_KEY not set. Get a free key at "
                     "https://core.ac.uk/services/api and set the env var.",
        })

    max_results = _clamp(max_results, 1, 100)

    # Build query with filters
    q_parts = [query]
    if year_from:
        q_parts.append(f"yearPublished>={year_from}")
    if year_to:
        q_parts.append(f"yearPublished<={year_to}")
    if document_type:
        q_parts.append(f'documentType:"{document_type}"')
    full_query = " AND ".join(q_parts)

    params = {
        "q": full_query,
        "limit": max_results,
    }

    _core_rate_limit()
    logger.info(f"CORE search: {full_query}")
    resp = requests.get(
        f"{CORE_API_BASE}/search/works",
        params=params,
        headers=_core_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    entries = [_parse_core_work(w) for w in data.get("results", [])]

    return json.dumps({
        "total_results": data.get("totalHits", 0),
        "results_count": len(entries),
        "query": full_query,
        "entries": entries,
    }, indent=2)


@mcp.tool()
def get_core_work(core_id: str) -> str:
    """
    Get full metadata for a CORE work by its ID.

    Args:
        core_id: The CORE work ID (numeric, from search results).
    """
    if not CORE_API_KEY:
        return json.dumps({"error": "CORE_API_KEY not set."})

    _core_rate_limit()
    resp = requests.get(
        f"{CORE_API_BASE}/works/{core_id}",
        headers=_core_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return json.dumps(_parse_core_work(resp.json()), indent=2)


@mcp.tool()
def download_core_paper(
    core_id: str,
    auto_index: bool = True,
    download_dir: str = None,
) -> str:
    """
    Download a paper's PDF from CORE and index it for full-text search.

    CORE provides direct PDF access for many works that have Cloudflare
    or paywall issues elsewhere — useful as a fallback.

    Args:
        core_id: The CORE work ID.
        auto_index: If True (default), index after download.
        download_dir: Custom download directory.
    """
    if not CORE_API_KEY:
        return json.dumps({"error": "CORE_API_KEY not set."})

    # Get metadata
    _core_rate_limit()
    meta_resp = requests.get(
        f"{CORE_API_BASE}/works/{core_id}",
        headers=_core_headers(),
        timeout=30,
    )
    meta_resp.raise_for_status()
    work = meta_resp.json()
    metadata = _parse_core_work(work)

    pdf_url = metadata.get("download_url") or (
        metadata["fulltext_urls"][0] if metadata.get("fulltext_urls") else ""
    )

    if not pdf_url:
        return json.dumps({
            "error": "No PDF download URL available for this CORE work.",
            "core_id": core_id,
            "metadata": metadata,
        }, indent=2)

    # Download
    target_dir = _get_download_dir(download_dir)
    filepath = target_dir / f"core_{core_id}.pdf"

    try:
        logger.info(f"Downloading CORE paper {core_id} from {pdf_url}")
        pdf_resp = requests.get(
            pdf_url,
            timeout=120,
            stream=True,
            headers=_core_headers(),
        )
        pdf_resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in pdf_resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        return json.dumps({
            "error": f"PDF download failed: {e}",
            "core_id": core_id,
            "pdf_url": pdf_url,
        }, indent=2)

    file_size = filepath.stat().st_size
    result = {
        "status": "downloaded",
        "file_path": str(filepath),
        "file_size_bytes": file_size,
        "file_size_mb": round(file_size / (1024 * 1024), 2),
        "core_id": core_id,
        "title": metadata.get("title", ""),
        "authors": metadata.get("authors", []),
        "indexed": False,
    }

    if auto_index:
        paper_meta = {
            "arxiv_id": f"core:{core_id}",
            "title": metadata.get("title", ""),
            "authors": metadata.get("authors", []),
            "summary": metadata.get("abstract", ""),
            "categories": [],
            "published": metadata.get("publication_date", ""),
        }
        _index_downloaded_pdf(filepath, paper_meta, result, f"CORE {core_id}")

    return json.dumps(result, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# CLOUD VENDOR DOCUMENTATION (AWS, Google Cloud, Microsoft Learn)
# ═══════════════════════════════════════════════════════════════════════════
#
# Live search over AWS, GCP, and Microsoft Learn documentation sites.
# These tools return JSON snippets + canonical URLs; they do not download or
# index pages into the FTS5 store (docs change too frequently for local
# indexing to pay off).
#
#   AWS            — public unauthenticated search proxy used by the AWS docs
#                    site (same endpoint awslabs/mcp calls).
#   Google Cloud   — Developer Knowledge API (developerknowledge.googleapis.com).
#                    Requires GOOGLE_DEVKNOWLEDGE_API_KEY (enable the API in a
#                    Google Cloud project and create an API key, ideally
#                    restricted to this single API).
#   Microsoft Learn — public site-search endpoint learn.microsoft.com/api/search
#                     used by the learn.microsoft.com search bar itself.

AWS_DOCS_SEARCH_URL = "https://proxy.search.docs.aws.com/search"
AWS_DOCS_USER_AGENT = "my-research-mcp-server/2.0 (+docs-search)"

GOOGLE_DEVKNOWLEDGE_API_KEY = os.environ.get("GOOGLE_DEVKNOWLEDGE_API_KEY")
GOOGLE_DEVKNOWLEDGE_BASE = "https://developerknowledge.googleapis.com/v1alpha"

MS_LEARN_SEARCH_URL = "https://learn.microsoft.com/api/search"


@mcp.tool()
def search_aws_docs(query: str, max_results: int = 10) -> str:
    """
    Search the official AWS documentation (docs.aws.amazon.com).

    Uses the public search proxy that powers the AWS docs site. No auth.
    Returns titles, URLs, and snippets — fetch the page separately to read.

    Args:
        query: Search terms.
        max_results: 1-50 results to return (default 10).
    """
    max_results = _clamp(max_results, 1, 50)
    session_id = str(uuid.uuid4())
    body = {
        "textQuery": {"input": query},
        "contextAttributes": [{"key": "domain", "value": "docs.aws.amazon.com"}],
        "acceptSuggestionBody": "RawText",
        "locales": ["en_us"],
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": AWS_DOCS_USER_AGENT,
        "X-MCP-Session-Id": session_id,
    }
    try:
        resp = requests.post(
            f"{AWS_DOCS_SEARCH_URL}?session={session_id}",
            json=body,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return json.dumps({"error": f"AWS docs search failed: {e}"}, indent=2)

    suggestions = data.get("suggestions") or data.get("results") or []
    items = []
    for s in suggestions[:max_results]:
        body_ = s.get("textExcerptSuggestion") or s.get("suggestion") or {}
        items.append({
            "title": body_.get("title") or s.get("title", ""),
            "url": body_.get("link") or body_.get("url") or s.get("url", ""),
            "snippet": body_.get("summary") or body_.get("excerpt") or s.get("snippet", ""),
            "context": body_.get("context", ""),
        })
    return json.dumps({
        "source": "aws_docs",
        "query": query,
        "count": len(items),
        "results": items,
    }, indent=2)


@mcp.tool()
def search_gcp_docs(query: str, max_results: int = 10) -> str:
    """
    Search Google Cloud documentation via the Developer Knowledge API.

    Requires GOOGLE_DEVKNOWLEDGE_API_KEY env var. Filters results to
    docs.cloud.google.com by default.

    Args:
        query: Raw natural-language query.
        max_results: 1-20 results (default 10; API max is 20).
    """
    if not GOOGLE_DEVKNOWLEDGE_API_KEY:
        return json.dumps({
            "error": "GOOGLE_DEVKNOWLEDGE_API_KEY not set.",
            "hint": "Enable the Developer Knowledge API in a Google Cloud "
                    "project, create an API key restricted to that API, and "
                    "export GOOGLE_DEVKNOWLEDGE_API_KEY.",
        }, indent=2)

    max_results = _clamp(max_results, 1, 20)
    params = {
        "key": GOOGLE_DEVKNOWLEDGE_API_KEY,
        "query": query,
        "pageSize": max_results,
        "filter": 'dataSource = "cloud.google.com"',
    }
    try:
        resp = requests.get(
            f"{GOOGLE_DEVKNOWLEDGE_BASE}/documents:searchDocumentChunks",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        return json.dumps({
            "error": f"GCP docs search failed: HTTP {resp.status_code}",
            "body": resp.text[:500],
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"GCP docs search failed: {e}"}, indent=2)

    items = []
    for r in data.get("results", []):
        doc = r.get("document") or {}
        items.append({
            "title": doc.get("title", ""),
            "url": doc.get("uri") or r.get("parent", ""),
            "snippet": r.get("content", "")[:500],
            "chunk_id": r.get("id", ""),
        })
    return json.dumps({
        "source": "gcp_docs",
        "query": query,
        "count": len(items),
        "results": items,
        "next_page_token": data.get("nextPageToken", ""),
    }, indent=2)


@mcp.tool()
def search_microsoft_docs(query: str, max_results: int = 10) -> str:
    """
    Search Microsoft Learn documentation (learn.microsoft.com).

    Uses the public site-search endpoint that powers the learn.microsoft.com
    search bar. No auth. Includes docs, training, and reference content.

    Args:
        query: Search terms.
        max_results: 1-50 results to return (default 10).
    """
    max_results = _clamp(max_results, 1, 50)
    params = {
        "search": query,
        "locale": "en-us",
        "$top": max_results,
        "expandScope": "true",
        "partnerId": "LearnSite",
    }
    try:
        resp = requests.get(MS_LEARN_SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return json.dumps({"error": f"Microsoft Learn search failed: {e}"}, indent=2)

    items = []
    for r in data.get("results", [])[:max_results]:
        items.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("description") or r.get("summary") or "")[:500],
            "content_type": r.get("contentType") or r.get("type", ""),
            "last_updated": r.get("lastUpdatedDate", ""),
        })
    return json.dumps({
        "source": "microsoft_learn",
        "query": query,
        "count": len(items),
        "results": items,
    }, indent=2)


@mcp.tool()
def fetch_cloud_doc_page(url: str, max_chars: int = 20000) -> str:
    """
    Fetch a documentation page from AWS, GCP, or Microsoft Learn and return
    plain text (HTML tags stripped). Use after search_* tools to read a hit.

    Args:
        url: Full URL on docs.aws.amazon.com, cloud.google.com, or
             learn.microsoft.com.
        max_chars: Truncate body at this many chars (default 20000).
    """
    allowed = (
        "docs.aws.amazon.com",
        "cloud.google.com",
        "learn.microsoft.com",
    )
    parsed = urlparse(url)
    if not any(
        parsed.netloc == host or parsed.netloc.endswith("." + host) for host in allowed
    ):
        return json.dumps({
            "error": f"URL must be on one of: {', '.join(allowed)}",
            "url": url,
        }, indent=2)

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": AWS_DOCS_USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        return json.dumps({"error": f"Fetch failed: {e}", "url": url}, indent=2)

    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html, flags=re.I)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]

    return json.dumps({
        "url": url,
        "char_count": len(text),
        "truncated": truncated,
        "text": text,
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# IAM DOCUMENTATION (Path A: Google PSE — Path C: local FTS crawler)
# ═══════════════════════════════════════════════════════════════════════════
#
# Path A — `search_iam_docs`: live search across ~23 OSS IAM doc sites via a
# Google Programmable Search Engine (PSE). Requires GOOGLE_PSE_CX (the search
# engine ID, not secret) and a Google API key with the Custom Search JSON API
# enabled (GOOGLE_DEVKNOWLEDGE_API_KEY is reused).
#
# Path C — `index_iam_project`, `search_iam_index`, `list_iam_indexed`:
# sitemap-driven crawler that stores extracted HTML text in the existing
# SQLite+FTS5 store (piggybacks on the papers/chunks tables with synthetic
# IDs prefixed 'iam:<project>:').

# Corpus for the PSE (Path A) — keep in sync with the PSE's site list.
IAM_DOCS_DOMAINS = [
    # Identity providers / auth servers
    "keycloak.org", "ory.sh", "supertokens.com", "authelia.com",
    "goauthentik.io", "zitadel.com", "logto.io", "casdoor.org",
    "authgear.com", "kanidm.github.io", "freeipa.org", "syncope.apache.org",
    # Authorization engines
    "openpolicyagent.org", "cerbos.dev", "authzed.com", "casbin.org",
    "permify.co", "topaz.sh", "warrant.dev",
    # Zero-trust / access proxies / secrets
    "pomerium.com", "developer.hashicorp.com", "infisical.com",
    # Standards & explainers
    "webauthn.guide", "jwt.io", "datatracker.ietf.org", "zanzibar.academy",
]

# Tier-1 crawl targets for Path C. Projects use one of two URL-discovery
# strategies depending on what the site exposes:
#   - "sitemap": fetch sitemap.xml and filter by path_filters (substrings).
#   - "seed_urls": start from a fixed list; optional follow_links does a
#                  single-level link extraction from the seed pages.
IAM_PROJECTS = {
    "keycloak": {
        "title": "Keycloak",
        "sitemap": "https://www.keycloak.org/sitemap.xml",
        "path_filters": ["/docs/", "/securing-apps/", "/guides/"],
    },
    "ory": {
        "title": "Ory (Kratos, Hydra, Keto, Oathkeeper)",
        "sitemap": "https://www.ory.sh/sitemap.xml",
        "path_filters": ["/docs/"],
    },
    "opa": {
        "title": "Open Policy Agent",
        "sitemap": "https://www.openpolicyagent.org/sitemap.xml",
        "path_filters": ["openpolicyagent.org/docs/"],
    },
    "vault": {
        "title": "HashiCorp Vault",
        "seed_urls": ["https://developer.hashicorp.com/vault/docs"],
        "follow_links": True,
        "path_filters": ["developer.hashicorp.com/vault/docs"],
    },
    "syncope": {
        "title": "Apache Syncope",
        # Reference + getting-started are monolithic HTML pages per version.
        "seed_urls": [
            "https://syncope.apache.org/docs/4.1/reference-guide.html",
            "https://syncope.apache.org/docs/4.1/getting-started.html",
            "https://syncope.apache.org/docs/4.0/reference-guide.html",
            "https://syncope.apache.org/docs/4.0/getting-started.html",
        ],
        "follow_links": False,
    },
    "freeipa": {
        "title": "FreeIPA",
        "seed_urls": ["https://www.freeipa.org/page/Documentation.html"],
        "follow_links": True,
        "path_filters": ["freeipa.org/page/"],
    },
}

GOOGLE_PSE_CX = os.environ.get("GOOGLE_PSE_CX")
CUSTOM_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY")
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY")
IAM_DOCS_SEARCH_PROVIDER = os.environ.get("IAM_DOCS_SEARCH_PROVIDER", "auto").strip().lower()

VERTEX_AI_PROJECT = os.environ.get("VERTEX_AI_PROJECT")
VERTEX_AI_LOCATION = os.environ.get("VERTEX_AI_LOCATION", "global")
VERTEX_AI_IAM_ENGINE_ID = os.environ.get("VERTEX_AI_IAM_ENGINE_ID")
_vertex_ai_creds = None


def _vertex_ai_access_token() -> str:
    """Return a fresh OAuth access token from ADC or service account."""
    global _vertex_ai_creds
    try:
        import google.auth
        from google.auth.transport.requests import Request as _GARequest
    except ImportError as e:
        raise RuntimeError(
            "google-auth not installed. Run: pip install google-auth"
        ) from e
    if _vertex_ai_creds is None:
        _vertex_ai_creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    if not _vertex_ai_creds.valid:
        _vertex_ai_creds.refresh(_GARequest())
    return _vertex_ai_creds.token


def _search_iam_docs_vertex(query: str, max_results: int) -> dict:
    if not VERTEX_AI_PROJECT or not VERTEX_AI_IAM_ENGINE_ID:
        raise ValueError("VERTEX_AI_PROJECT and VERTEX_AI_IAM_ENGINE_ID must be set")
    token = _vertex_ai_access_token()
    url = (
        f"https://discoveryengine.googleapis.com/v1/projects/{VERTEX_AI_PROJECT}"
        f"/locations/{VERTEX_AI_LOCATION}/collections/default_collection"
        f"/engines/{VERTEX_AI_IAM_ENGINE_ID}/servingConfigs/default_search:search"
    )
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": VERTEX_AI_PROJECT,
        },
        json={"query": query, "pageSize": max_results},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _iam_docs_sites_query(query: str) -> str:
    sites = " OR ".join(f"site:{d}" for d in IAM_DOCS_DOMAINS)
    return f"({query}) ({sites})"


def _search_iam_docs_brave(query: str, max_results: int) -> dict:
    if not BRAVE_SEARCH_API_KEY:
        raise ValueError("BRAVE_SEARCH_API_KEY not set")
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={
            "q": _iam_docs_sites_query(query),
            "count": max_results,
        },
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _search_iam_docs_serpapi(query: str, max_results: int) -> dict:
    if not SERPAPI_API_KEY:
        raise ValueError("SERPAPI_API_KEY not set")
    resp = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": "google",
            "q": _iam_docs_sites_query(query),
            "num": max_results,
            "api_key": SERPAPI_API_KEY,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _search_iam_docs_google_pse(query: str, max_results: int) -> dict:
    if not GOOGLE_PSE_CX:
        raise ValueError("GOOGLE_PSE_CX not set")
    if not GOOGLE_DEVKNOWLEDGE_API_KEY:
        raise ValueError("GOOGLE_DEVKNOWLEDGE_API_KEY not set")
    resp = requests.get(
        CUSTOM_SEARCH_URL,
        params={
            "key": GOOGLE_DEVKNOWLEDGE_API_KEY,
            "cx": GOOGLE_PSE_CX,
            "q": query,
            "num": max_results,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Path A ──────────────────────────────────────────────────────────────

@mcp.tool()
def search_iam_docs(query: str, max_results: int = 10) -> str:
    """
    Live search across ~23 OSS IAM documentation sites via Google
    Programmable Search Engine.

    Covers Keycloak, Ory, SuperTokens, Authelia, authentik, ZITADEL, Logto,
    Casdoor, Authgear, Kanidm, FreeIPA, Apache Syncope, OPA, Cerbos,
    SpiceDB (authzed), Casbin, Permify, Topaz, Warrant, Pomerium,
    HashiCorp Vault, Infisical, and standards/explainers (webauthn.guide,
    jwt.io, IETF datatracker, zanzibar.academy).

    Requires env vars GOOGLE_PSE_CX + GOOGLE_DEVKNOWLEDGE_API_KEY (the key
    must have the Custom Search JSON API enabled).

    Args:
        query: Search terms.
        max_results: 1-10 results per call (Custom Search API max is 10).
    """
    max_results = _clamp(max_results, 1, 10)
    providers: list[str]
    if IAM_DOCS_SEARCH_PROVIDER in {"vertex", "brave", "serpapi", "google"}:
        providers = [IAM_DOCS_SEARCH_PROVIDER]
    else:
        providers = []
        if VERTEX_AI_PROJECT and VERTEX_AI_IAM_ENGINE_ID:
            providers.append("vertex")
        if BRAVE_SEARCH_API_KEY:
            providers.append("brave")
        if SERPAPI_API_KEY:
            providers.append("serpapi")
        providers.append("google")

    last_error: str | None = None
    for p in providers:
        try:
            if p == "vertex":
                data = _search_iam_docs_vertex(query, max_results)
                items = []
                for r in data.get("results", [])[:max_results]:
                    d = r.get("document", {}).get("derivedStructData", {}) or {}
                    snippet = ""
                    snips = d.get("snippets") or []
                    if snips and isinstance(snips, list):
                        snippet = snips[0].get("snippet", "") if isinstance(snips[0], dict) else ""
                    items.append({
                        "title": d.get("title", ""),
                        "url": d.get("link", "") or d.get("formattedUrl", ""),
                        "snippet": snippet,
                        "display_url": d.get("displayLink", ""),
                    })
                return json.dumps({
                    "source": "iam_docs_vertex",
                    "query": query,
                    "count": len(items),
                    "results": items,
                }, indent=2)

            if p == "brave":
                data = _search_iam_docs_brave(query, max_results)
                items = []
                for r in data.get("web", {}).get("results", [])[:max_results]:
                    items.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("description", ""),
                        "display_url": r.get("profile", {}).get("name", ""),
                    })
                return json.dumps({
                    "source": "iam_docs_brave",
                    "query": query,
                    "count": len(items),
                    "results": items,
                }, indent=2)

            if p == "serpapi":
                data = _search_iam_docs_serpapi(query, max_results)
                items = []
                for r in data.get("organic_results", [])[:max_results]:
                    items.append({
                        "title": r.get("title", ""),
                        "url": r.get("link", ""),
                        "snippet": r.get("snippet", ""),
                        "display_url": r.get("displayed_link", ""),
                    })
                return json.dumps({
                    "source": "iam_docs_serpapi",
                    "query": query,
                    "count": len(items),
                    "results": items,
                }, indent=2)

            data = _search_iam_docs_google_pse(query, max_results)
            items = []
            for r in data.get("items", []):
                items.append({
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                    "display_url": r.get("displayLink", ""),
                })
            return json.dumps({
                "source": "iam_docs_pse",
                "query": query,
                "total_results": int(data.get("searchInformation", {}).get("totalResults", "0")),
                "count": len(items),
                "results": items,
            }, indent=2)
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            body = getattr(e.response, "text", "")
            last_error = f"{p}: HTTP {status} {body[:200]}"
        except Exception as e:
            last_error = f"{p}: {e}"

    return json.dumps({
        "error": "IAM docs search failed",
        "details": last_error,
        "hint": "Set IAM_DOCS_SEARCH_PROVIDER to one of: brave, serpapi, google, auto. "
                "Also ensure SERPAPI_API_KEY or BRAVE_SEARCH_API_KEY is set for non-Google providers.",
    }, indent=2)


# ── Path C ──────────────────────────────────────────────────────────────

def _extract_main_text(html: str) -> tuple[str, str]:
    """Return (title, plain_text) from an HTML page, stripping scripts/styles/tags."""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    title = title_match.group(1).strip() if title_match else ""

    body = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html, flags=re.I)
    body = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", body, flags=re.I)
    body = re.sub(r"<nav\b[^<]*(?:(?!</nav>)<[^<]*)*</nav>", " ", body, flags=re.I)
    body = re.sub(r"<footer\b[^<]*(?:(?!</footer>)<[^<]*)*</footer>", " ", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return title, body


def _parse_sitemap(xml: str) -> list[str]:
    """Extract <loc> URLs from a sitemap (handles sitemap-index too)."""
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)


def _extract_links(html: str, base_url: str) -> list[str]:
    """Pull href URLs out of HTML and resolve relative paths against base_url."""
    from urllib.parse import urljoin
    hrefs = re.findall(r'href\s*=\s*["\']([^"\'#]+)', html, flags=re.I)
    return [urljoin(base_url, h) for h in hrefs]


def _resolve_project_urls(cfg: dict) -> list[str]:
    """Build the candidate URL list for a project based on its config."""
    path_filters = cfg.get("path_filters", [])

    if "sitemap" in cfg:
        try:
            r = requests.get(
                cfg["sitemap"],
                headers={"User-Agent": AWS_DOCS_USER_AGENT},
                timeout=30,
            )
            r.raise_for_status()
        except Exception as e:
            raise RuntimeError(f"Sitemap fetch failed: {e}")

        urls = _parse_sitemap(r.text)
        nested = [u for u in urls if u.endswith(".xml")]
        if nested and len(nested) == len(urls):
            expanded = []
            for nu in nested[:10]:
                try:
                    rr = requests.get(nu, timeout=30)
                    rr.raise_for_status()
                    expanded.extend(_parse_sitemap(rr.text))
                except Exception as e:
                    logger.debug("Nested sitemap fetch skipped for %s: %s", nu, e)
                    continue
            urls = expanded
    elif "seed_urls" in cfg:
        urls = list(cfg["seed_urls"])
        if cfg.get("follow_links"):
            for seed in list(cfg["seed_urls"]):
                try:
                    r = requests.get(
                        seed,
                        headers={"User-Agent": AWS_DOCS_USER_AGENT},
                        timeout=30,
                    )
                    r.raise_for_status()
                    urls.extend(_extract_links(r.text, seed))
                except Exception as e:
                    logger.debug("Seed URL crawl skipped for %s: %s", seed, e)
                    continue
    else:
        raise RuntimeError("Project config has neither 'sitemap' nor 'seed_urls'.")

    if path_filters:
        urls = [u for u in urls if any(f in u for f in path_filters)]

    seen = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


@mcp.tool()
def index_iam_project(project: str, max_pages: int = 100) -> str:
    """
    Crawl one IAM project's docs and index into the local FTS store. Pages
    are stored with IDs like 'iam:<project>:<hash>' so query_papers and
    search_iam_index find them.

    Tier-1 projects: keycloak, ory, opa (sitemap-based);
                     vault, syncope, freeipa (seed-URL based).

    Args:
        project: Project key (see list_iam_indexed for available keys).
        max_pages: Cap on pages fetched per run (default 100). Raise later
                   runs to grow the corpus; already-indexed pages are skipped
                   when their content hash matches.
    """
    if project not in IAM_PROJECTS:
        return json.dumps({
            "error": f"Unknown project '{project}'.",
            "available": sorted(IAM_PROJECTS.keys()),
        }, indent=2)

    cfg = IAM_PROJECTS[project]
    max_pages = _clamp(max_pages, 1, 500)

    try:
        all_urls = _resolve_project_urls(cfg)
    except Exception as e:
        return json.dumps({"error": str(e), "project": project}, indent=2)

    candidates = all_urls[:max_pages]

    idx = _get_index()
    stats = {"fetched": 0, "indexed": 0, "skipped": 0, "failed": 0}
    errors = []

    for url in candidates:
        page_id = f"iam:{project}:{hashlib.sha1(url.encode()).hexdigest()[:16]}"
        try:
            r = requests.get(
                url,
                headers={"User-Agent": AWS_DOCS_USER_AGENT},
                timeout=30,
            )
            r.raise_for_status()
            stats["fetched"] += 1
        except Exception as e:
            stats["failed"] += 1
            errors.append({"url": url, "error": str(e)})
            continue

        title, text = _extract_main_text(r.text)
        if len(text) < 200:
            stats["skipped"] += 1
            continue

        content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        if not idx.needs_reindex(page_id, content_hash):
            stats["skipped"] += 1
            continue

        chunks = chunk_pages([{"page": 1, "text": text}])
        meta = {
            "arxiv_id": page_id,
            "title": title or url,
            "authors": [],
            "summary": text[:400],
            "categories": ["iam", project],
            "published": "",
        }
        idx.upsert_paper(meta, chunks, url, 1, content_hash)
        stats["indexed"] += 1
        time.sleep(0.2)  # be polite to the doc host

    return json.dumps({
        "project": project,
        "title": cfg["title"],
        "sitemap_urls_total": len(all_urls),
        "candidate_urls": len(candidates),
        "stats": stats,
        "errors": errors[:5],
    }, indent=2)


@mcp.tool()
def search_iam_index(query: str, project: str = "", max_results: int = 20) -> str:
    """
    Full-text search over locally indexed IAM docs (populated by
    `index_iam_project`).

    Args:
        query: Search terms (supports FTS5 syntax — see query_papers).
        project: Optional project key to restrict results (e.g. 'keycloak').
        max_results: Max chunks to return (default 20).
    """
    idx = _get_index()
    sql_prefix = "iam:%"
    if project:
        if project not in IAM_PROJECTS:
            return json.dumps({
                "error": f"Unknown project '{project}'.",
                "available": sorted(IAM_PROJECTS.keys()),
            }, indent=2)
        sql_prefix = f"iam:{project}:%"

    fts_query = idx.to_fts_query(query)
    rows = idx.conn.execute("""
        SELECT c.arxiv_id, c.heading, c.content, c.chunk_index,
               p.title, p.pdf_path AS url, rank
        FROM chunks_fts
        JOIN chunks c ON c.chunk_id = chunks_fts.rowid
        JOIN papers p ON p.arxiv_id = c.arxiv_id
        WHERE chunks_fts MATCH ? AND c.arxiv_id LIKE ?
        ORDER BY rank
        LIMIT ?
    """, (fts_query, sql_prefix, max_results)).fetchall()

    results = []
    for row in rows:
        page_id = row["arxiv_id"]
        proj = page_id.split(":")[1] if page_id.startswith("iam:") else ""
        results.append({
            "project": proj,
            "title": row["title"],
            "url": row["url"],
            "heading": row["heading"],
            "snippet": row["content"][:500],
            "relevance_rank": row["rank"],
        })
    return json.dumps({
        "query": query,
        "project_filter": project or "all",
        "count": len(results),
        "results": results,
    }, indent=2)


@mcp.tool()
def list_iam_indexed() -> str:
    """
    Show which IAM projects are available for indexing and how many pages
    are currently stored for each.
    """
    idx = _get_index()
    counts = {}
    for row in idx.conn.execute("""
        SELECT arxiv_id FROM papers WHERE arxiv_id LIKE 'iam:%'
    """).fetchall():
        proj = row["arxiv_id"].split(":")[1]
        counts[proj] = counts.get(proj, 0) + 1

    projects = []
    for key, cfg in IAM_PROJECTS.items():
        projects.append({
            "key": key,
            "title": cfg["title"],
            "source": cfg.get("sitemap") or f"seed_urls ({len(cfg.get('seed_urls', []))})",
            "indexed_pages": counts.get(key, 0),
        })
    return json.dumps({
        "total_indexed_pages": sum(counts.values()),
        "projects": projects,
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# GITHUB (narrow, research-focused tools)
# ═══════════════════════════════════════════════════════════════════════════
#
# A small set of GitHub tools aimed at research workflows: finding paper
# implementations, browsing topic-related repos, and reading READMEs. For
# general GitHub work (issues, PRs, actions, writes) use the official
# github/github-mcp-server alongside this one — don't duplicate it here.
#
# Auth: set GITHUB_TOKEN for 5000 req/hr and to enable code search
# (code search *requires* auth). Without a token, repo search still works
# at 60 req/hr.

GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")


def _github_headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "my-research-mcp-server/2.0",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


@mcp.tool()
def search_github_repos(query: str, max_results: int = 10, sort: str = "stars") -> str:
    """
    Search GitHub repositories — useful for finding reference
    implementations, topic surveys, or tooling related to a paper.

    Args:
        query: GitHub search query (supports qualifiers like
               'topic:diffusion language:python stars:>100').
        max_results: 1-50 results (default 10).
        sort: 'stars', 'forks', 'updated', or 'best-match' (default 'stars').
    """
    max_results = _clamp(max_results, 1, 50)
    params = {"q": query, "per_page": max_results}
    if sort != "best-match":
        params["sort"] = sort
        params["order"] = "desc"
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/search/repositories",
            params=params,
            headers=_github_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return json.dumps({"error": f"GitHub repo search failed: {e}"}, indent=2)

    items = []
    for r in data.get("items", [])[:max_results]:
        items.append({
            "full_name": r.get("full_name", ""),
            "url": r.get("html_url", ""),
            "description": r.get("description", ""),
            "stars": r.get("stargazers_count", 0),
            "forks": r.get("forks_count", 0),
            "language": r.get("language", ""),
            "topics": r.get("topics", []),
            "updated_at": r.get("updated_at", ""),
        })
    return json.dumps({
        "source": "github_repos",
        "query": query,
        "total_count": data.get("total_count", 0),
        "count": len(items),
        "results": items,
    }, indent=2)


@mcp.tool()
def search_github_code(query: str, max_results: int = 10) -> str:
    """
    Search code across public GitHub repositories. Requires GITHUB_TOKEN.

    Useful for finding implementations of a specific algorithm, function
    name, or string from a paper.

    Args:
        query: GitHub code-search query (supports 'repo:', 'language:',
               'filename:', 'path:' qualifiers).
        max_results: 1-30 results (default 10).
    """
    if not GITHUB_TOKEN:
        return json.dumps({
            "error": "GITHUB_TOKEN not set.",
            "hint": "Code search requires auth. Create a fine-grained PAT "
                    "with public-repo read access and export GITHUB_TOKEN.",
        }, indent=2)

    max_results = _clamp(max_results, 1, 30)
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/search/code",
            params={"q": query, "per_page": max_results},
            headers=_github_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return json.dumps({"error": f"GitHub code search failed: {e}"}, indent=2)

    items = []
    for r in data.get("items", [])[:max_results]:
        repo = r.get("repository", {}) or {}
        items.append({
            "repo": repo.get("full_name", ""),
            "path": r.get("path", ""),
            "url": r.get("html_url", ""),
            "score": r.get("score", 0),
        })
    return json.dumps({
        "source": "github_code",
        "query": query,
        "total_count": data.get("total_count", 0),
        "count": len(items),
        "results": items,
    }, indent=2)


@mcp.tool()
def fetch_github_readme(repo: str, max_chars: int = 20000) -> str:
    """
    Fetch a repo's README as plain text.

    Args:
        repo: 'owner/name' (e.g. 'pytorch/pytorch').
        max_chars: Truncate at this many chars (default 20000).
    """
    if "/" not in repo:
        return json.dumps({"error": "repo must be in 'owner/name' form"}, indent=2)

    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{repo}/readme",
            headers={**_github_headers(), "Accept": "application/vnd.github.raw"},
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        return json.dumps({"error": f"README fetch failed: {e}", "repo": repo}, indent=2)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return json.dumps({
        "repo": repo,
        "char_count": len(text),
        "truncated": truncated,
        "text": text,
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# DUCKDB TOOLS
# ═══════════════════════════════════════════════════════════════════════════
# Read-only SQL analytics over the existing SQLite papers/chunks DB, plus ad
# hoc SQL over Parquet/CSV/JSON files. Uses DuckDB — zero-copy SQLite attach
# and columnar file readers. Only SELECT/WITH queries are allowed.

DUCKDB_DATASETS_DIR = os.environ.get(
    "DUCKDB_DATASETS_DIR",
    os.path.expanduser("~/research-datasets"),
)
_duckdb_conn = None


def _duckdb() -> "object":
    """Return a shared in-memory DuckDB connection (lazy)."""
    global _duckdb_conn
    import duckdb
    if _duckdb_conn is None:
        _duckdb_conn = duckdb.connect(database=":memory:")
        # Attach the SQLite papers index read-only so analytics_sql works.
        try:
            _duckdb_conn.execute("INSTALL sqlite; LOAD sqlite;")
            _duckdb_conn.execute(
                f"ATTACH '{DEFAULT_DB_PATH}' AS papers_db (TYPE sqlite, READ_ONLY);"
            )
        except Exception as e:
            logger.warning(f"DuckDB SQLite attach failed: {e}")
    return _duckdb_conn


_READ_ONLY_SQL_RE = re.compile(
    r"^\s*(?:with\b[\s\S]*?select\b|select\b|pragma\b|describe\b|show\b)",
    re.IGNORECASE,
)
_FORBIDDEN_SQL_RE = re.compile(
    r"\b(attach|detach|copy\s+.*\s+to\b|insert|update|delete|drop|create|alter|"
    r"truncate|call|execute|export|load|install|set\s+\w+\s*=)\b",
    re.IGNORECASE,
)


def _ensure_read_only_sql(sql: str) -> Optional[str]:
    """Return an error string if the SQL is not a safe read-only query."""
    if ";" in sql.strip().rstrip(";"):
        return "Only a single SQL statement is allowed."
    if not _READ_ONLY_SQL_RE.match(sql):
        return "Only SELECT/WITH/PRAGMA/DESCRIBE/SHOW queries are allowed."
    if _FORBIDDEN_SQL_RE.search(sql):
        return "Statement contains forbidden keywords (writes/DDL/side-effects)."
    return None


def _duckdb_rows_to_json(cur) -> dict:
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    return {
        "columns": cols,
        "row_count": len(rows),
        "rows": [dict(zip(cols, r)) for r in rows],
    }


@mcp.tool()
def analytics_sql(sql: str, limit: int = 100) -> str:
    """
    Run read-only SQL analytics over the local papers/chunks SQLite DB via
    DuckDB. The SQLite DB is attached as schema `papers_db` (read-only).

    Tables available:
        papers_db.papers   - paper metadata (arxiv_id, title, authors JSON,
                             abstract, categories, published, pdf_path,
                             total_pages, indexed_at, content_hash)
        papers_db.chunks   - chunked text (chunk_id, arxiv_id, page_start,
                             page_end, chunk_index, heading, content)

    Examples:
        SELECT COUNT(*) FROM papers_db.papers
        SELECT SUBSTR(published, 1, 4) AS year, COUNT(*) AS n
          FROM papers_db.papers GROUP BY 1 ORDER BY 1 DESC
        SELECT arxiv_id, title FROM papers_db.papers WHERE title ILIKE '%keycloak%'
        SELECT arxiv_id, COUNT(*) AS chunk_count
          FROM papers_db.chunks GROUP BY arxiv_id
          ORDER BY chunk_count DESC LIMIT 10
        SELECT column_name, data_type FROM duckdb_columns()
          WHERE table_name = 'papers'

    Args:
        sql: A single SELECT/WITH/PRAGMA/DESCRIBE/SHOW query.
        limit: Max rows to return (default 100, hard cap 10000).
    """
    err = _ensure_read_only_sql(sql)
    if err:
        return json.dumps({"error": err}, indent=2)
    limit = _clamp(limit, 1, 10000)
    try:
        con = _duckdb()
        cur = con.execute(f"SELECT * FROM ({sql.rstrip(';')}) AS _q LIMIT {limit}")
        result = _duckdb_rows_to_json(cur)
        result["limit"] = limit
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"analytics_sql failed: {e}"}, indent=2)


@mcp.tool()
def list_datasets() -> str:
    """
    List data files (Parquet/CSV/JSON/NDJSON) under DUCKDB_DATASETS_DIR so you
    can reference them from `dataset_query`.

    Set DUCKDB_DATASETS_DIR to change the root (default: ~/research-datasets).
    """
    root = Path(DUCKDB_DATASETS_DIR)
    if not root.exists():
        return json.dumps({
            "datasets_dir": str(root),
            "exists": False,
            "hint": f"Create {root} and drop Parquet/CSV/JSON files into it.",
            "files": [],
        }, indent=2)
    exts = {".parquet", ".csv", ".tsv", ".json", ".ndjson", ".jsonl"}
    files = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            files.append({
                "path": str(p),
                "relative": str(p.relative_to(root)),
                "size_bytes": p.stat().st_size,
                "ext": p.suffix.lower(),
            })
    files.sort(key=lambda x: x["relative"])
    return json.dumps({
        "datasets_dir": str(root),
        "exists": True,
        "file_count": len(files),
        "files": files,
    }, indent=2)


@mcp.tool()
def dataset_query(sql: str, limit: int = 100) -> str:
    """
    Run read-only SQL over Parquet/CSV/JSON/NDJSON files under
    DUCKDB_DATASETS_DIR. Reference files with DuckDB reader functions using
    paths (absolute or relative to DUCKDB_DATASETS_DIR).

    Examples:
        SELECT * FROM read_parquet('arxiv_meta.parquet') LIMIT 10
        SELECT category, COUNT(*) FROM read_csv_auto('openalex.csv') GROUP BY 1
        SELECT * FROM read_json_auto('dump.ndjson')

    For security, only file paths that resolve inside DUCKDB_DATASETS_DIR are
    allowed. Only a single SELECT/WITH/PRAGMA/DESCRIBE/SHOW statement.

    Args:
        sql: A single SELECT/WITH/PRAGMA/DESCRIBE/SHOW query.
        limit: Max rows to return (default 100, hard cap 10000).
    """
    err = _ensure_read_only_sql(sql)
    if err:
        return json.dumps({"error": err}, indent=2)

    root = Path(DUCKDB_DATASETS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # Extract file paths from DuckDB reader functions and sandbox them.
    reader_re = re.compile(
        r"(read_parquet|read_csv|read_csv_auto|read_json|read_json_auto|read_ndjson|"
        r"read_ndjson_auto|parquet_scan)\s*\(\s*['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    safe_sql = sql
    for m in reader_re.finditer(sql):
        raw = m.group(2)
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (root / raw)
        try:
            resolved = candidate.resolve()
        except Exception:
            return json.dumps({"error": f"Cannot resolve path: {raw}"}, indent=2)
        if root not in resolved.parents and resolved != root:
            return json.dumps({
                "error": "Path outside DUCKDB_DATASETS_DIR is not allowed",
                "path": str(resolved),
                "datasets_dir": str(root),
            }, indent=2)
        # Rewrite with the resolved absolute path.
        safe_sql = safe_sql.replace(f"'{raw}'", f"'{resolved}'").replace(
            f'"{raw}"', f"'{resolved}'"
        )

    limit = _clamp(limit, 1, 10000)
    try:
        con = _duckdb()
        cur = con.execute(f"SELECT * FROM ({safe_sql.rstrip(';')}) AS _q LIMIT {limit}")
        result = _duckdb_rows_to_json(cur)
        result["limit"] = limit
        result["datasets_dir"] = str(root)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"dataset_query failed: {e}"}, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# SEMANTIC SEARCH (DuckDB VSS + fastembed)
# ═══════════════════════════════════════════════════════════════════════════
# Embeds paper chunks with a local ONNX model (fastembed) and stores vectors
# in a persistent DuckDB file with an HNSW index for fast cosine similarity.
# Default model: BAAI/bge-small-en-v1.5 (384-dim, ~100MB on first use).

EMBED_MODEL_NAME = os.environ.get("EMBED_MODEL_NAME", "BAAI/bge-small-en-v1.5")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "384"))
EMBED_DB_PATH = os.environ.get(
    "EMBED_DB_PATH",
    os.path.join(DEFAULT_DOWNLOAD_DIR, "embeddings.duckdb"),
)
_embed_model = None


def _get_embed_model():
    """Lazy-load the fastembed model (downloads on first use)."""
    global _embed_model
    if _embed_model is None:
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise RuntimeError(
                "fastembed not installed. Run: pip install fastembed"
            ) from e
        logger.info(f"Loading embedding model: {EMBED_MODEL_NAME}")
        _embed_model = TextEmbedding(model_name=EMBED_MODEL_NAME)
    return _embed_model


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Return one 384-dim embedding per input text (as python floats)."""
    model = _get_embed_model()
    return [list(map(float, v)) for v in model.embed(texts)]


def _ensure_embed_schema(con) -> None:
    """Attach persistent embeddings DB + ensure table and HNSW index exist."""
    # Only attach once per connection.
    attached = con.execute(
        "SELECT 1 FROM duckdb_databases() WHERE database_name='emb'"
    ).fetchone()
    if not attached:
        Path(EMBED_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"ATTACH '{EMBED_DB_PATH}' AS emb;")
    con.execute("INSTALL vss; LOAD vss;")
    con.execute("SET hnsw_enable_experimental_persistence=true;")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS emb.embeddings (
            chunk_id BIGINT PRIMARY KEY,
            arxiv_id VARCHAR,
            content_hash VARCHAR,
            embedding FLOAT[{EMBED_DIM}]
        );
    """)
    # HNSW index (no-op if it already exists).
    try:
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
            ON emb.embeddings USING HNSW (embedding)
            WITH (metric = 'cosine');
        """)
    except Exception as e:
        logger.warning(f"HNSW index create skipped: {e}")


@mcp.tool()
def embedding_stats() -> str:
    """
    Show how many chunks are embedded vs total, and the embedding model in use.
    """
    try:
        con = _duckdb()
        _ensure_embed_schema(con)
        total = con.execute("SELECT COUNT(*) FROM papers_db.chunks").fetchone()[0]
        embedded = con.execute("SELECT COUNT(*) FROM emb.embeddings").fetchone()[0]
        return json.dumps({
            "model": EMBED_MODEL_NAME,
            "dimensions": EMBED_DIM,
            "embeddings_db": EMBED_DB_PATH,
            "total_chunks": total,
            "embedded_chunks": embedded,
            "missing": max(0, total - embedded),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"embedding_stats failed: {e}"}, indent=2)


@mcp.tool()
def embed_chunks(limit: int = 500, batch_size: int = 32) -> str:
    """
    Embed paper chunks that don't yet have embeddings. Run this after
    indexing new papers. Uses the local fastembed ONNX model (no API calls).

    Args:
        limit: Max chunks to embed in this call (default 500, cap 10000).
        batch_size: Model batch size (default 32).
    """
    limit = _clamp(limit, 1, 10000)
    batch_size = _clamp(batch_size, 1, 256)
    try:
        con = _duckdb()
        _ensure_embed_schema(con)
        rows = con.execute(f"""
            SELECT c.chunk_id, c.arxiv_id, c.content
            FROM papers_db.chunks c
            LEFT JOIN emb.embeddings e ON e.chunk_id = c.chunk_id
            WHERE e.chunk_id IS NULL
              AND c.content IS NOT NULL
              AND LENGTH(c.content) > 0
            ORDER BY c.chunk_id
            LIMIT {limit}
        """).fetchall()
        if not rows:
            return json.dumps({
                "status": "up_to_date",
                "embedded_this_call": 0,
            }, indent=2)

        inserted = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            texts = [r[2] for r in batch]
            vectors = _embed_texts(texts)
            for (chunk_id, arxiv_id, content), vec in zip(batch, vectors):
                chash = hashlib.sha1(content.encode("utf-8", "ignore")).hexdigest()
                con.execute(
                    "INSERT OR REPLACE INTO emb.embeddings "
                    "(chunk_id, arxiv_id, content_hash, embedding) VALUES (?, ?, ?, ?)",
                    [chunk_id, arxiv_id, chash, vec],
                )
                inserted += 1
        con.execute("CHECKPOINT emb;")
        return json.dumps({
            "status": "ok",
            "embedded_this_call": inserted,
            "model": EMBED_MODEL_NAME,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"embed_chunks failed: {e}"}, indent=2)


@mcp.tool()
def semantic_search(query: str, max_results: int = 10, arxiv_id: str = "") -> str:
    """
    Semantic (vector) search over embedded paper chunks using cosine similarity.

    Requires chunks to have been embedded via `embed_chunks`. Only embedded
    chunks participate in the search — unembedded chunks are skipped.

    Args:
        query: Natural-language query.
        max_results: Max chunks to return (default 10, cap 100).
        arxiv_id: Optional — restrict results to a single paper.
    """
    max_results = _clamp(max_results, 1, 100)
    try:
        con = _duckdb()
        _ensure_embed_schema(con)
        qvec = _embed_texts([query])[0]
        where = ""
        params: list = [qvec]
        if arxiv_id:
            where = "WHERE e.arxiv_id = ?"
            params.append(arxiv_id)
        sql = f"""
            SELECT e.chunk_id, e.arxiv_id, c.heading, c.page_start, c.content,
                   array_cosine_similarity(e.embedding, ?::FLOAT[{EMBED_DIM}]) AS score
            FROM emb.embeddings e
            JOIN papers_db.chunks c ON c.chunk_id = e.chunk_id
            {where}
            ORDER BY score DESC
            LIMIT {max_results}
        """
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(zip(cols, r))
            content = d.get("content") or ""
            if len(content) > 600:
                content = content[:600] + "..."
            results.append({
                "chunk_id": d["chunk_id"],
                "arxiv_id": d["arxiv_id"],
                "heading": d.get("heading") or "",
                "page_start": d.get("page_start"),
                "score": round(float(d["score"]), 4),
                "snippet": content,
            })
        return json.dumps({
            "query": query,
            "model": EMBED_MODEL_NAME,
            "count": len(results),
            "results": results,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": f"semantic_search failed: {e}"}, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="My Research MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.port = args.port
        logger.info(f"Starting My Research MCP server (SSE) on port {args.port}")
        mcp.run(transport="sse")
    else:
        logger.info("Starting My Research MCP server (stdio)")
        mcp.run(transport="stdio")
