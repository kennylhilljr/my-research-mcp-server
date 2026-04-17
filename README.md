# My Research MCP Server — Multi-Source Academic Search

An MCP server that searches across arXiv, Semantic Scholar, institutional repositories (MIT, Harvard, Cornell, Penn), OpenAlex, CORE, Crossref, cloud vendor docs, IAM documentation, and GitHub — downloads PDFs, extracts full text, indexes everything in SQLite FTS5, and provides DuckDB analytics and semantic vector search over your local paper library.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Data Sources                                │
│  arXiv · Semantic Scholar · MIT DSpace · Harvard DASH               │
│  Cornell eCommons · Penn ScholarlyCommons · OpenAlex · CORE         │
│  Crossref/Unpaywall · AWS/GCP/MS Docs · IAM Docs · GitHub          │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐
│  Search APIs │────▶│  Download    │────▶│  PDF Text Extraction │
│  (metadata)  │     │  PDFs        │     │  (PyMuPDF)           │
└──────────────┘     └──────────────┘     └──────────┬───────────┘
                                                     │
                      ┌──────────────────────────────┤
                      ▼                              ▼
┌──────────────────────────────┐  ┌──────────────────────────────────┐
│  SQLite FTS5 Index           │  │  DuckDB Analytics + Embeddings   │
│  • papers table (metadata)   │  │  • Read-only SQL over SQLite     │
│  • chunks table (text+pages) │  │  • Parquet/CSV/JSON datasets     │
│  • chunks_fts (BM25 search)  │  │  • fastembed vectors (HNSW)      │
└──────────────────────────────┘  └──────────────────────────────────┘
                      ▲                              ▲
                      └──────────────┬───────────────┘
                                     │
                              ┌──────┴──────┐
                              │  MCP Client │
                              │  (Claude)   │
                              └─────────────┘
```

**Pipeline**: Search any source → Download PDF → Extract text (PyMuPDF) → Chunk with overlap & heading detection → Index in SQLite FTS5 → Query with BM25 ranking / DuckDB SQL / semantic vector search

## Tools (47 total)

### arXiv API
| Tool | Description |
|------|-------------|
| `search_arxiv` | Search arXiv's catalog with full query syntax |
| `get_paper_metadata` | Fetch metadata by arXiv ID |

### Download & Index
| Tool | Description |
|------|-------------|
| `download_paper` | Download arXiv PDF + auto-index full text |
| `index_paper` | Manually index/re-index a single paper |
| `index_all_papers` | Batch-index all PDFs in the download directory |

### Full-Text Search
| Tool | Description |
|------|-------------|
| `query_papers` | **Full-text search across all indexed paper content** — finds specific passages, methods, results, equations |
| `get_paper_text` | Retrieve full text or specific pages of an indexed paper |

### Management
| Tool | Description |
|------|-------------|
| `list_indexed_papers` | List all papers in the index with stats |
| `remove_paper` | Remove a paper from the index |
| `index_stats` | Get index statistics |

### Semantic Scholar
| Tool | Description |
|------|-------------|
| `search_semantic_scholar` | Cross-repository search across arXiv, PubMed, ACM, IEEE, Springer, etc. |
| `get_semantic_scholar_paper` | Get detailed metadata, citations, and open-access PDF links |

### Institutional Repositories
| Tool | Description |
|------|-------------|
| `search_mit_dspace` | Search MIT's 60,000+ works (theses, reports, articles) |
| `get_mit_dspace_item` | Get full metadata for an MIT DSpace item |
| `search_harvard_dash` | Search Harvard's 58,000+ open-access works |
| `get_harvard_dash_item` | Get full metadata for a Harvard DASH item |
| `search_cornell_ecommons` | Search Cornell's 24,000+ works (CS, engineering, policy) |
| `get_cornell_ecommons_item` | Get full metadata for a Cornell eCommons item |
| `search_penn_scholarly` | Search UPenn's 43,000+ works (AI ethics, governance) |
| `get_penn_scholarly_item` | Get full metadata for a Penn ScholarlyCommons item |

### DOI / Crossref
| Tool | Description |
|------|-------------|
| `resolve_doi` | Resolve a DOI to full metadata via Crossref + DataCite |
| `search_crossref` | Search 150M+ works in Crossref by query, author, year |
| `get_doi_citation` | Get formatted citation (BibTeX, APA, RIS, etc.) via content negotiation |
| `download_paper_by_doi` | Find and download open-access PDF by DOI (Unpaywall + Semantic Scholar) |

### OpenAlex
| Tool | Description |
|------|-------------|
| `search_openalex` | Search 250M+ works in OpenAlex (free, CC0 catalog) |
| `get_openalex_work` | Get full metadata for an OpenAlex work |
| `search_openalex_authors` | Search for authors with publication stats |

### CORE
| Tool | Description |
|------|-------------|
| `search_core` | Search 200M+ open-access works from 11,000+ repositories |
| `get_core_work` | Get full metadata for a CORE work |
| `download_core_paper` | Download PDF from CORE + auto-index |

### Cloud Vendor Documentation
| Tool | Description |
|------|-------------|
| `search_aws_docs` | Search official AWS documentation |
| `search_gcp_docs` | Search Google Cloud documentation (requires API key) |
| `search_microsoft_docs` | Search Microsoft Learn documentation |
| `fetch_cloud_doc_page` | Fetch and extract text from a cloud doc page |

### IAM Documentation
| Tool | Description |
|------|-------------|
| `search_iam_docs` | Live search across ~23 OSS IAM doc sites (Keycloak, Ory, OPA, etc.) |
| `index_iam_project` | Crawl and index an IAM project's docs for full-text search |
| `search_iam_index` | Search locally indexed IAM documentation |
| `list_iam_indexed` | List all indexed IAM projects |

### GitHub
| Tool | Description |
|------|-------------|
| `search_github_repos` | Search GitHub repositories by topic, language, stars |
| `search_github_code` | Search code across public repos (requires token) |
| `fetch_github_readme` | Fetch a repository's README |

### Analytics & Embeddings
| Tool | Description |
|------|-------------|
| `analytics_sql` | Run read-only SQL over the paper index via DuckDB |
| `list_datasets` | List Parquet/CSV/JSON files available for querying |
| `dataset_query` | Run SQL over external dataset files via DuckDB |
| `embedding_stats` | Show embedding coverage stats |
| `embed_chunks` | Embed paper chunks with local ONNX model (fastembed) |
| `semantic_search` | Vector similarity search over embedded chunks |

## Quick Start

### 1. Install dependencies

```bash
pip install mcp requests pymupdf duckdb google-auth fastembed
```

Or install as a package:

```bash
pip install -e .        # production
pip install -e ".[dev]" # with pytest + ruff
```

### 2. Run

```bash
python server.py                              # stdio (Claude Desktop / Claude Code)
python server.py --transport sse --port 8080  # HTTP/SSE
```

### 3. Configure Claude Desktop

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "my-research": {
      "command": "python",
      "args": ["/absolute/path/to/server.py"],
      "env": {
        "ARXIV_DOWNLOAD_DIR": "~/arxiv-papers",
        "UNPAYWALL_EMAIL": "you@example.com",
        "CORE_API_KEY": "your-core-api-key",
        "GOOGLE_DEVKNOWLEDGE_API_KEY": "your-google-api-key",
        "GOOGLE_PSE_CX": "your-pse-cx-id",
        "GITHUB_TOKEN": "ghp_your_token"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add my-research python /absolute/path/to/server.py
```

## Query Syntax

### arXiv API search (`search_arxiv`)
```
ti:transformer AND cat:cs.CL
au:vaswani AND ti:attention
(cat:cs.AI OR cat:cs.CL) AND all:large language model
```

### Full-text content search (`query_papers`)
```
gradient descent convergence            # implicit AND
"self-supervised learning"              # exact phrase
attention AND mechanism                 # explicit AND
transformer OR attention                # OR
NEAR(policy gradient, 10)              # proximity (within 10 tokens)
reinforc*                               # prefix matching
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ARXIV_DOWNLOAD_DIR` | `~/arxiv-papers` | PDF storage + default DB location |
| `ARXIV_DB_PATH` | `<DOWNLOAD_DIR>/arxiv_index.db` | SQLite FTS5 database path |
| `ARXIV_CHUNK_SIZE` | `1500` | Characters per text chunk |
| `ARXIV_CHUNK_OVERLAP` | `200` | Overlap between chunks for context continuity |
| `UNPAYWALL_EMAIL` | `research-mcp@example.com` | Your real email for Unpaywall OA lookups |
| `OPENALEX_EMAIL` | _(empty)_ | Email for OpenAlex polite pool (higher rate limits) |
| `CORE_API_KEY` | _(empty)_ | Free API key from [core.ac.uk](https://core.ac.uk/services/api) |
| `GOOGLE_DEVKNOWLEDGE_API_KEY` | _(unset)_ | Google API key (enables GCP docs + IAM PSE search) |
| `GOOGLE_PSE_CX` | _(unset)_ | Programmable Search Engine ID for IAM doc search |
| `IAM_DOCS_SEARCH_PROVIDER` | `auto` | Force IAM search provider: `vertex`, `brave`, `serpapi`, `google`, or `auto` |
| `VERTEX_AI_PROJECT` | _(unset)_ | GCP project ID for Vertex AI Search |
| `VERTEX_AI_LOCATION` | `global` | Vertex AI Search location |
| `VERTEX_AI_IAM_ENGINE_ID` | _(unset)_ | Vertex AI Search engine ID for IAM docs |
| `BRAVE_SEARCH_API_KEY` | _(unset)_ | Brave Search API key (IAM docs fallback) |
| `SERPAPI_API_KEY` | _(unset)_ | SerpAPI key (IAM docs fallback) |
| `GITHUB_TOKEN` | _(unset)_ | GitHub PAT — required for code search, optional for repo search |
| `DUCKDB_DATASETS_DIR` | `~/research-datasets` | Root directory for Parquet/CSV/JSON dataset files |
| `EMBED_MODEL_NAME` | `BAAI/bge-small-en-v1.5` | fastembed ONNX model for semantic search |
| `EMBED_DIM` | `384` | Embedding vector dimensions (must match model) |
| `EMBED_DB_PATH` | `<DOWNLOAD_DIR>/embeddings.duckdb` | DuckDB file for vector embeddings |

## How Indexing Works

1. **Text extraction** — PyMuPDF reads every page of the PDF
2. **Heading detection** — Heuristics identify section headings (numbered sections, ALL-CAPS, common academic titles)
3. **Chunking** — Text is split into ~1500-char overlapping segments, each tagged with page range and nearest heading
4. **Content hashing** — SHA-256 hash detects when a PDF changes and needs re-indexing
5. **FTS5 indexing** — Porter stemming + Unicode tokenization enables fuzzy, stemmed search with BM25 ranking
6. **Triggers** — SQLite triggers keep the FTS index in sync on every insert/update/delete

## Example Session

```
You: Search arXiv for papers on "policy-as-code" in software engineering
  -> search_arxiv("all:policy-as-code", category="cs.SE")

You: Also check Semantic Scholar and OpenAlex for broader coverage
  -> search_semantic_scholar("policy-as-code", fields_of_study="Computer Science")
  -> search_openalex("policy-as-code")

You: Download the top result and that DOI from the Crossref hit
  -> download_paper("2401.xxxxx")    # auto-indexes full text
  -> download_paper_by_doi("10.1145/3649835")

You: What do these papers say about OPA Rego validation?
  -> query_papers("OPA Rego validation")
  Returns: matching text passages with page numbers and headings

You: Search the OPA docs for Rego policy testing
  -> search_iam_docs("Rego policy testing")

You: Run some analytics on my indexed papers
  -> analytics_sql("SELECT COUNT(*) AS total, SUBSTR(published,1,4) AS year FROM papers_db.papers GROUP BY 2 ORDER BY 2 DESC")

You: Find semantically similar passages to "access control policy evaluation"
  -> embed_chunks()           # embed any new chunks
  -> semantic_search("access control policy evaluation")
```

## License

MIT
