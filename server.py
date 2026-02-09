#!/usr/bin/env python3
"""
arXiv MCP Server — Full-Text PDF Search
========================================
An MCP server that lets you:
  - Search the arXiv API for papers
  - Download paper PDFs
  - Extract & index full text from every PDF
  - Query across the full content of your entire local paper library

Dependencies:  pip install mcp requests pymupdf
"""

import os
import re
import sys
import json
import time
import sqlite3
import logging
import hashlib
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
        fts_query = self._to_fts_query(query)

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
                "authors": json.loads(row["authors"]),
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
            "authors": json.loads(paper["authors"]),
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
                "authors": json.loads(r["authors"]),
                "categories": json.loads(r["categories"]),
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
    def _to_fts_query(query: str) -> str:
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
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text")
        if text.strip():
            pages.append({"page": i + 1, "text": text})
    doc.close()
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

_last_request_time = 0.0

def _rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.time()


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

mcp = FastMCP("arxiv")

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
    if max_results < 1: max_results = 1
    if max_results > 100: max_results = 100

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
            logger.error(f"Indexing failed for {arxiv_id}: {e}")

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
    except Exception:
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
            except Exception:
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
# Entrypoint
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="arXiv MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.port = args.port
        logger.info(f"Starting arXiv MCP server (SSE) on port {args.port}")
        mcp.run(transport="sse")
    else:
        logger.info("Starting arXiv MCP server (stdio)")
        mcp.run(transport="stdio")
