# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

# My Research MCP Server

Multi-source academic research platform exposing **47 tools** across **13 categories** via [MCP](https://modelcontextprotocol.io/).

## Quick Commands

```bash
python server.py                            # Run (stdio, for Claude Desktop/Code)
python server.py --transport sse --port 8080 # Run (HTTP/SSE)
make dev                                     # Install with dev deps
make test                                    # pytest -v --tb=short
make lint                                    # ruff check .
make format                                  # ruff format .
```

## File Map

| File | Purpose |
|------|---------|
| `server.py` (~3,700 lines) | All 47 tools, PaperIndex class, helpers, entrypoint |
| `tests/test_server.py` | Test suite |
| `pyproject.toml` | Package config, dependencies, ruff/pytest settings |
| `PAPER_INDEX.md` | Documentation on the paper indexing system |
| `Makefile` | Dev shortcuts (install, test, lint, run) |

## Architecture

```
FastMCP server
├── PaperIndex (SQLite FTS5) — papers + chunks tables, BM25 full-text search
├── DuckDB analytics — read-only SQL over the SQLite index + external datasets
├── fastembed vectors — ONNX embeddings stored in DuckDB with HNSW cosine search
└── HTTP clients — arXiv, Semantic Scholar, DSpace (MIT/Harvard/Cornell/Penn),
                   Crossref, Unpaywall, OpenAlex, CORE, AWS/GCP/MS docs,
                   IAM docs (Google PSE / Vertex AI / Brave / SerpAPI), GitHub
```

## Tool Inventory (47 tools)

### arXiv (2)
`search_arxiv`, `get_paper_metadata`

### Download & Index (3)
`download_paper`, `index_paper`, `index_all_papers`

### Full-Text Search (2)
`query_papers`, `get_paper_text`

### Management (3)
`list_indexed_papers`, `remove_paper`, `index_stats`

### Semantic Scholar (2)
`search_semantic_scholar`, `get_semantic_scholar_paper`

### Institutional Repositories (8)
`search_mit_dspace`, `get_mit_dspace_item`,
`search_harvard_dash`, `get_harvard_dash_item`,
`search_cornell_ecommons`, `get_cornell_ecommons_item`,
`search_penn_scholarly`, `get_penn_scholarly_item`

### DOI / Crossref (4)
`resolve_doi`, `search_crossref`, `get_doi_citation`, `download_paper_by_doi`

### OpenAlex (3)
`search_openalex`, `get_openalex_work`, `search_openalex_authors`

### CORE (3)
`search_core`, `get_core_work`, `download_core_paper`

### Cloud Docs (4)
`search_aws_docs`, `search_gcp_docs`, `search_microsoft_docs`, `fetch_cloud_doc_page`

### IAM Docs (4)
`search_iam_docs`, `index_iam_project`, `search_iam_index`, `list_iam_indexed`

### GitHub (3)
`search_github_repos`, `search_github_code`, `fetch_github_readme`

### Analytics & Embeddings (6)
`analytics_sql`, `list_datasets`, `dataset_query`,
`embedding_stats`, `embed_chunks`, `semantic_search`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ARXIV_DOWNLOAD_DIR` | `~/arxiv-papers` | PDF storage + default DB location |
| `ARXIV_DB_PATH` | `<DOWNLOAD_DIR>/arxiv_index.db` | SQLite FTS5 database |
| `ARXIV_CHUNK_SIZE` | `1500` | Chars per text chunk |
| `ARXIV_CHUNK_OVERLAP` | `200` | Overlap between chunks |
| `UNPAYWALL_EMAIL` | `research-mcp@example.com` | Real email for Unpaywall OA lookups |
| `OPENALEX_EMAIL` | _(empty)_ | Email for OpenAlex polite pool |
| `CORE_API_KEY` | _(empty)_ | Free API key from core.ac.uk |
| `GOOGLE_DEVKNOWLEDGE_API_KEY` | _(unset)_ | Google API key (GCP docs + IAM PSE) |
| `GOOGLE_PSE_CX` | _(unset)_ | Programmable Search Engine ID for IAM docs |
| `IAM_DOCS_SEARCH_PROVIDER` | `auto` | Force provider: `vertex`, `brave`, `serpapi`, `google`, or `auto` |
| `VERTEX_AI_PROJECT` | _(unset)_ | GCP project for Vertex AI Search |
| `VERTEX_AI_LOCATION` | `global` | Vertex AI location |
| `VERTEX_AI_IAM_ENGINE_ID` | _(unset)_ | Vertex AI Search engine ID |
| `BRAVE_SEARCH_API_KEY` | _(unset)_ | Brave Search API key (IAM docs fallback) |
| `SERPAPI_API_KEY` | _(unset)_ | SerpAPI key (IAM docs fallback) |
| `GITHUB_TOKEN` | _(unset)_ | GitHub PAT (required for code search, optional for repo search) |
| `DUCKDB_DATASETS_DIR` | `~/research-datasets` | Root dir for Parquet/CSV/JSON datasets |
| `EMBED_MODEL_NAME` | `BAAI/bge-small-en-v1.5` | fastembed ONNX model name |
| `EMBED_DIM` | `384` | Embedding vector dimensions |
| `EMBED_DB_PATH` | `<DOWNLOAD_DIR>/embeddings.duckdb` | DuckDB file for embeddings |

## Key Constants

| Constant | Value | Notes |
|----------|-------|-------|
| `RATE_LIMIT_SECONDS` | 3.0 | arXiv API delay |
| Semantic Scholar delay | 1.0s | Between API calls |
| CORE delay | 6.5s | ~10 req/min free tier |
| `CHUNK_SIZE` | 1500 | Default chars per chunk |
| `CHUNK_OVERLAP` | 200 | Default overlap |

## Dependencies

- `mcp>=1.0.0` — FastMCP server framework
- `requests>=2.28.0` — HTTP client
- `pymupdf>=1.23.0` — PDF text extraction (imported as `fitz`)
- `duckdb>=1.0.0` — Analytics SQL engine + vector search
- `google-auth>=2.0.0` — Vertex AI authentication
- `fastembed>=0.3.0` — Local ONNX text embeddings
