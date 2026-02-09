# Changelog

## [1.0.0] - 2025-02-08

### Added
- **arXiv API integration**: `search_arxiv` and `get_paper_metadata` tools
- **PDF download**: `download_paper` with automatic text extraction and indexing
- **Full-text search**: `query_papers` using SQLite FTS5 with BM25 ranking
- **Text extraction**: PyMuPDF-based page-by-page extraction with heading detection
- **Smart chunking**: Overlapping text chunks with configurable size and overlap
- **Content hashing**: SHA-256 based change detection to skip redundant re-indexing
- **Batch indexing**: `index_all_papers` scans download directory
- **Page-range retrieval**: `get_paper_text` supports fetching specific pages
- **Management tools**: `list_indexed_papers`, `remove_paper`, `index_stats`
- **Dual transport**: stdio (Claude Desktop/Code) and SSE (HTTP) modes
- **CI pipeline**: GitHub Actions with Python 3.10-3.12 matrix testing
