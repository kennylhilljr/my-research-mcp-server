# Changelog

## [2.0.0] - 2025-04-17

### Added
- **Semantic Scholar integration**: `search_semantic_scholar`, `get_semantic_scholar_paper` — cross-repository search across arXiv, PubMed, ACM, IEEE, Springer, and more
- **Institutional repositories** (DSpace 8): `search_harvard_dash`, `get_harvard_dash_item`, `search_cornell_ecommons`, `get_cornell_ecommons_item`, `search_penn_scholarly`, `get_penn_scholarly_item`
- **MIT DSpace** (DSpace v6): `search_mit_dspace`, `get_mit_dspace_item`
- **DOI / Crossref tools**: `resolve_doi`, `search_crossref`, `get_doi_citation`, `download_paper_by_doi` (multi-source OA PDF discovery via Unpaywall + Semantic Scholar)
- **OpenAlex integration**: `search_openalex`, `get_openalex_work`, `search_openalex_authors` — 250M+ works, free CC0 catalog
- **CORE integration**: `search_core`, `get_core_work`, `download_core_paper` — 200M+ open-access works from 11,000+ repositories
- **Cloud vendor docs**: `search_aws_docs`, `search_gcp_docs`, `search_microsoft_docs`, `fetch_cloud_doc_page`
- **IAM documentation**: `search_iam_docs` (live search across ~23 OSS IAM doc sites via Google PSE / Vertex AI / Brave / SerpAPI), `index_iam_project`, `search_iam_index`, `list_iam_indexed`
- **GitHub tools**: `search_github_repos`, `search_github_code`, `fetch_github_readme`
- **DuckDB analytics**: `analytics_sql` (read-only SQL over the paper index), `list_datasets`, `dataset_query` (SQL over Parquet/CSV/JSON files)
- **Semantic vector search**: `embedding_stats`, `embed_chunks`, `semantic_search` — local ONNX embeddings via fastembed with DuckDB HNSW index
- 3 new dependencies: `duckdb>=1.0.0`, `google-auth>=2.0.0`, `fastembed>=0.3.0`

### Changed
- Project renamed from "arXiv MCP Server" to "My Research MCP Server"
- Tool count expanded from 11 to 47 across 13 categories
- README rewritten to cover all tools, sources, and environment variables
- `.env.example` expanded from 7 to 22 environment variables

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
