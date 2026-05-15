# Graph Report - /Users/bkh223/Documents/GitHub/my-research-mcp-server  (2026-05-11)

## Corpus Check
- Corpus is ~24,937 words - fits in a single context window. You may not need a graph.

## Summary
- 508 nodes · 796 edges · 46 communities (29 shown, 17 thin omitted)
- Extraction: 76% EXTRACTED · 24% INFERRED · 0% AMBIGUOUS · INFERRED: 193 edges (avg confidence: 0.77)
- Token cost: 83,855 input · 20,963 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Paper Indexing Pipeline|Paper Indexing Pipeline]]
- [[_COMMUNITY_Text Extraction & Doc Fetch|Text Extraction & Doc Fetch]]
- [[_COMMUNITY_CORE API Client|CORE API Client]]
- [[_COMMUNITY_Project Concepts & README|Project Concepts & README]]
- [[_COMMUNITY_IAM Source Ingestion|IAM Source Ingestion]]
- [[_COMMUNITY_Read-Only SQL Guard|Read-Only SQL Guard]]
- [[_COMMUNITY_arXiv API Client|arXiv API Client]]
- [[_COMMUNITY_CrossrefDOI Client|Crossref/DOI Client]]
- [[_COMMUNITY_DuckDB Analytics & Embeddings|DuckDB Analytics & Embeddings]]
- [[_COMMUNITY_Cloud Docs Search (AWSGCPMS)|Cloud Docs Search (AWS/GCP/MS)]]
- [[_COMMUNITY_Sitemap & Link Parsing|Sitemap & Link Parsing]]
- [[_COMMUNITY_Paper Index Tests|Paper Index Tests]]
- [[_COMMUNITY_DSpace 8 Repositories|DSpace 8 Repositories]]
- [[_COMMUNITY_MIT DSpace & IAM Search|MIT DSpace & IAM Search]]
- [[_COMMUNITY_AIIAM Research Concepts|AI/IAM Research Concepts]]
- [[_COMMUNITY_OpenAlex Abstract Parser|OpenAlex Abstract Parser]]
- [[_COMMUNITY_Full-Text Search Tests|Full-Text Search Tests]]
- [[_COMMUNITY_HTML & Paper Text Extraction|HTML & Paper Text Extraction]]
- [[_COMMUNITY_Paper Text Retrieval|Paper Text Retrieval]]
- [[_COMMUNITY_Paper Index Edge-Case Tests|Paper Index Edge-Case Tests]]
- [[_COMMUNITY_MIT DSpace Search|MIT DSpace Search]]
- [[_COMMUNITY_FTS Query Builder Tests|FTS Query Builder Tests]]
- [[_COMMUNITY_LinkText Extraction Tests|Link/Text Extraction Tests]]
- [[_COMMUNITY_DSpace 8 Search (CornellHarvardPenn)|DSpace 8 Search (Cornell/Harvard/Penn)]]
- [[_COMMUNITY_Rate Limiter|Rate Limiter]]
- [[_COMMUNITY_GitHub Search & README Fetch|GitHub Search & README Fetch]]
- [[_COMMUNITY_OpenAlex Search|OpenAlex Search]]
- [[_COMMUNITY_Semantic Scholar Client|Semantic Scholar Client]]
- [[_COMMUNITY_IAM Docs Sites Query|IAM Docs Sites Query]]
- [[_COMMUNITY_IAM Source Ingest CLI|IAM Source Ingest CLI]]
- [[_COMMUNITY_arXiv Atom Parser|arXiv Atom Parser]]
- [[_COMMUNITY_IAM Docs Search|IAM Docs Search]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]

## God Nodes (most connected - your core abstractions)
1. `PaperIndex` - 44 edges
2. `_clamp()` - 33 edges
3. `_ensure_read_only_sql()` - 22 edges
4. `_parse_feed()` - 19 edges
5. `TestEnsureReadOnlySql` - 19 edges
6. `chunk_pages()` - 17 edges
7. `TestPaperIndexing` - 17 edges
8. `TestFullTextSearch` - 14 edges
9. `TestAtomParsing` - 14 edges
10. `_safe_json_loads()` - 13 edges

## Surprising Connections (you probably didn't know these)
- `TestHeadingDetection` --uses--> `PaperIndex`  [INFERRED]
  tests/test_server.py → server.py
- `TestPaperIndexing` --uses--> `PaperIndex`  [INFERRED]
  tests/test_server.py → server.py
- `TestFullTextSearch` --uses--> `PaperIndex`  [INFERRED]
  tests/test_server.py → server.py
- `TestTextRetrieval` --uses--> `PaperIndex`  [INFERRED]
  tests/test_server.py → server.py
- `TestAtomParsing` --uses--> `PaperIndex`  [INFERRED]
  tests/test_server.py → server.py

## Hyperedges (group relationships)
- **PDF Indexing Pipeline (download to FTS5 storage)** — server_download_paper, server__index_downloaded_pdf, server_extract_text_from_pdf, server_chunk_pages, server_compute_content_hash, server_paperindex [INFERRED 0.95]
- **Read-only SQL Safety Gate** — server_analytics_sql, server_dataset_query, server__ensure_read_only_sql, test_server_testensurereadonlysql [INFERRED 0.85]
- **LLM-driven Access Control Policy Research** — paper_index_safe_guard, paper_index_ragent, paper_index_lmn, paper_index_llms_for_access_control [EXTRACTED 1.00]

## Communities (46 total, 17 thin omitted)

### Community 0 - "Paper Indexing Pipeline"
Cohesion: 0.06
Nodes (32): chunk_pages(), compute_content_hash(), _get_index(), get_paper_text(), index_all_papers(), _index_downloaded_pdf(), index_iam_project(), index_paper() (+24 more)

### Community 1 - "Text Extraction & Doc Fetch"
Cohesion: 0.07
Nodes (28): detect_heading(), extract_text_from_pdf(), fetch_cloud_doc_page(), Fetch a documentation page from AWS, GCP, or Microsoft Learn and return     plai, Extract text page-by-page from a PDF using PyMuPDF., Heuristic: detect section headings in academic papers., indexed_db(), Tests for the My Research MCP server. (+20 more)

### Community 2 - "CORE API Client"
Cohesion: 0.12
Nodes (15): _core_headers(), download_core_paper(), download_paper(), get_core_work(), _get_download_dir(), _parse_core_work(), Parse a CORE work into a clean dict., Search CORE — the world's largest aggregator of open access research,     with 2 (+7 more)

### Community 3 - "Project Concepts & README"
Cohesion: 0.1
Nodes (22): Release 1.0.0 - arXiv MCP Server, Release 2.0.0 - Multi-source expansion, Project Instructions for Claude, Contributing Guide, DuckDB Analytics Engine, fastembed ONNX Embeddings with DuckDB HNSW, FastMCP Server Architecture, My Research MCP Server (+14 more)

### Community 4 - "IAM Source Ingestion"
Cohesion: 0.11
Nodes (21): call_json, load_sources, main, parse_args, Native server.py Indexing Path, _get_index, _index_downloaded_pdf, chunk_pages (+13 more)

### Community 5 - "Read-Only SQL Guard"
Cohesion: 0.18
Nodes (3): _ensure_read_only_sql(), Return an error string if the SQL is not a safe read-only query., TestEnsureReadOnlySql

### Community 6 - "arXiv API Client"
Cohesion: 0.17
Nodes (7): get_paper_metadata(), _parse_entry(), _parse_feed(), Search the arXiv API for papers matching a query (metadata search).      This se, Fetch metadata for one or more papers by arXiv ID from the arXiv API.      Args:, search_arxiv(), TestAtomParsing

### Community 7 - "Crossref/DOI Client"
Cohesion: 0.14
Nodes (13): _clean_doi(), download_paper_by_doi(), get_doi_citation(), _parse_crossref(), Strip common URL/scheme prefixes from a DOI string., Parse a Crossref work item into a clean dict., Resolve a DOI to get full metadata from Crossref (articles) or DataCite (dataset, Search Crossref for scholarly works by query across all publishers     (ACM, IEE (+5 more)

### Community 8 - "DuckDB Analytics & Embeddings"
Cohesion: 0.13
Nodes (19): analytics_sql(), dataset_query(), _duckdb(), _duckdb_rows_to_json(), embed_chunks(), _embed_texts(), embedding_stats(), _ensure_embed_schema() (+11 more)

### Community 9 - "Cloud Docs Search (AWS/GCP/MS)"
Cohesion: 0.16
Nodes (9): _clamp(), Search the official AWS documentation (docs.aws.amazon.com).      Uses the publi, Search Google Cloud documentation via the Developer Knowledge API.      Requires, Search Microsoft Learn documentation (learn.microsoft.com).      Uses the public, Clamp an integer value to [lo, hi]., search_aws_docs(), search_gcp_docs(), search_microsoft_docs() (+1 more)

### Community 10 - "Sitemap & Link Parsing"
Cohesion: 0.17
Nodes (8): _extract_links(), _parse_sitemap(), Extract <loc> URLs from a sitemap (handles sitemap-index too)., Pull href URLs out of HTML and resolve relative paths against base_url., Build the candidate URL list for a project based on its config., _resolve_project_urls(), TestExtractLinks, TestParseSitemap

### Community 12 - "DSpace 8 Repositories"
Cohesion: 0.16
Nodes (11): _dspace8_get_item(), _dspace8_parse_item(), get_cornell_ecommons_item(), get_harvard_dash_item(), get_penn_scholarly_item(), Parse a DSpace 8 item into a clean dict., Get full metadata + bitstreams for a DSpace 8 item., Get full metadata and downloadable files for a Harvard DASH item.      Args: (+3 more)

### Community 13 - "MIT DSpace & IAM Search"
Cohesion: 0.17
Nodes (15): get_mit_dspace_item(), list_datasets(), Get full metadata for a specific item in MIT's DSpace repository.      Args:, Return a fresh OAuth access token from ADC or service account., Live search across ~23 OSS IAM documentation sites via Google     Programmable S, Full-text search over locally indexed IAM docs (populated by     `index_iam_proj, List data files (Parquet/CSV/JSON/NDJSON) under DUCKDB_DATASETS_DIR so you     c, search_iam_docs() (+7 more)

### Community 14 - "AI/IAM Research Concepts"
Cohesion: 0.15
Nodes (15): AI/ML Negative Impact on IAM, Overprivileged AI Agents Pattern, Copilot Permission Amplification Risk, AI Retrieval Tenant Isolation Failures, Agentic AI Security, Cedar Authorization Language, LLM Guardrails and Safety, LLMs for Access Control and Policy (+7 more)

### Community 15 - "OpenAlex Abstract Parser"
Cohesion: 0.21
Nodes (6): _openalex_abstract(), _parse_openalex_work(), Parse an OpenAlex work into a clean dict., Reconstruct abstract from OpenAlex's inverted index format., TestOpenAlexAbstract, TestParseOpenAlexWork

### Community 17 - "HTML & Paper Text Extraction"
Cohesion: 0.22
Nodes (6): _extract_main_text(), query_papers(), Full-text search across all indexed paper content., Return (title, plain_text) from an HTML page, stripping scripts/styles/tags., Full-text search across the content of all indexed arXiv PDFs.      This is the, TestExtractMainText

### Community 18 - "Paper Text Retrieval"
Cohesion: 0.24
Nodes (4): Get full or partial text of an indexed paper., Parse JSON with a fallback for corrupt or missing data., _safe_json_loads(), TestSafeJsonLoads

### Community 19 - "Paper Index Edge-Case Tests"
Cohesion: 0.18
Nodes (4): Indexing with no chunks should work., Results with more matches should rank higher., Two PaperIndex instances pointing to the same DB should work., TestPaperIndexEdgeCases

### Community 20 - "MIT DSpace Search"
Cohesion: 0.24
Nodes (7): _dspace_parse_item(), _dspace_search_field(), Parse a DSpace item with metadata into a clean dict., Search a single DSpace metadata field., Search MIT's DSpace institutional repository for technical reports,     theses,, search_mit_dspace(), TestDspaceParseItem

### Community 22 - "Link/Text Extraction Tests"
Cohesion: 0.22
Nodes (9): _extract_links, _extract_main_text, _parse_sitemap, fetch_cloud_doc_page, index_iam_project, TestExtractLinks, TestExtractMainText, TestParseSitemap (+1 more)

### Community 23 - "DSpace 8 Search (Cornell/Harvard/Penn)"
Cohesion: 0.25
Nodes (8): _dspace8_search(), Search a DSpace 8 repository., Search Harvard's DASH open-access repository for scholarly works.     58,000+ wo, Search Cornell's eCommons repository for scholarly works.     24,000+ works: str, Search UPenn's ScholarlyCommons repository for scholarly works.     43,000+ work, search_cornell_ecommons(), search_harvard_dash(), search_penn_scholarly()

### Community 24 - "Rate Limiter"
Cohesion: 0.39
Nodes (3): _make_rate_limiter(), Create a rate-limiter closure that enforces *delay* seconds between calls., TestMakeRateLimiter

### Community 25 - "GitHub Search & README Fetch"
Cohesion: 0.29
Nodes (7): fetch_github_readme(), _github_headers(), Search GitHub repositories — useful for finding reference     implementations, t, Search code across public GitHub repositories. Requires GITHUB_TOKEN.      Usefu, Fetch a repo's README as plain text.      Args:         repo: 'owner/name' (e.g., search_github_code(), search_github_repos()

### Community 26 - "OpenAlex Search"
Cohesion: 0.29
Nodes (7): get_openalex_work(), _openalex_params(), Search OpenAlex — a free, CC0 catalog of 250M+ scholarly works covering     all, Get full metadata for an OpenAlex work, including references and     related wor, Search OpenAlex for authors by name. Useful for disambiguating authors     and f, search_openalex(), search_openalex_authors()

### Community 27 - "Semantic Scholar Client"
Cohesion: 0.33
Nodes (6): get_semantic_scholar_paper(), Get detailed metadata for a paper from Semantic Scholar.      Args:         pape, Make a Semantic Scholar API request with retry on 429., Search Semantic Scholar for papers across all major academic sources     (arXiv,, search_semantic_scholar(), _ss_request()

### Community 29 - "IAM Source Ingest CLI"
Cohesion: 0.7
Nodes (4): call_json(), load_sources(), main(), parse_args()

### Community 30 - "arXiv Atom Parser"
Cohesion: 0.67
Nodes (3): _parse_entry, _parse_feed, TestAtomParsing

### Community 31 - "IAM Docs Search"
Cohesion: 0.67
Nodes (3): _iam_docs_sites_query, search_iam_docs, TestIamDocsSitesQuery

## Knowledge Gaps
- **150 isolated node(s):** `Parse JSON with a fallback for corrupt or missing data.`, `SQLite + FTS5 index for extracted PDF text.`, `Full-text search across all indexed paper content.`, `Get full or partial text of an indexed paper.`, `Convert natural language to FTS5 query.` (+145 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **17 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PaperIndex` connect `Paper Indexing Pipeline` to `Text Extraction & Doc Fetch`, `CORE API Client`, `Read-Only SQL Guard`, `arXiv API Client`, `Crossref/DOI Client`, `Cloud Docs Search (AWS/GCP/MS)`, `Sitemap & Link Parsing`, `Paper Index Tests`, `DSpace 8 Repositories`, `MIT DSpace & IAM Search`, `OpenAlex Abstract Parser`, `Full-Text Search Tests`, `HTML & Paper Text Extraction`, `Paper Text Retrieval`, `Paper Index Edge-Case Tests`, `MIT DSpace Search`, `FTS Query Builder Tests`, `Rate Limiter`, `IAM Docs Sites Query`?**
  _High betweenness centrality (0.212) - this node is a cross-community bridge._
- **Why does `TestPaperIndexing` connect `Paper Index Tests` to `Paper Indexing Pipeline`, `Text Extraction & Doc Fetch`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Why does `TestFullTextSearch` connect `Full-Text Search Tests` to `Paper Indexing Pipeline`, `Text Extraction & Doc Fetch`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Are the 31 inferred relationships involving `PaperIndex` (e.g. with `TestPaperIndexing` and `TestFullTextSearch`) actually correct?**
  _`PaperIndex` has 31 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `_clamp()` (e.g. with `.test_within_range()` and `.test_below_minimum()`) actually correct?**
  _`_clamp()` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `_ensure_read_only_sql()` (e.g. with `.test_select_allowed()` and `.test_select_with_whitespace()`) actually correct?**
  _`_ensure_read_only_sql()` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `_parse_feed()` (e.g. with `.test_parse_total_results()` and `.test_parse_arxiv_id()`) actually correct?**
  _`_parse_feed()` has 12 INFERRED edges - model-reasoned connections that need verification._