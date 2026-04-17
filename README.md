# My Research MCP Server

A multi-source academic research platform built on the [Model Context Protocol](https://modelcontextprotocol.io/). Searches, downloads, indexes, and queries scholarly content from 13 source categories through 47 tools — backed by SQLite FTS5 full-text search, DuckDB analytics, and local semantic vector search.

## Data Sources

### Academic & Scholarly

| Source | Domain | Scale | Description |
|--------|--------|-------|-------------|
| **arXiv** | arxiv.org | 2.4M+ preprints | Open-access preprints in physics, math, CS, biology, finance, and more. Full search + PDF download + indexing. |
| **Semantic Scholar** | api.semanticscholar.org | 200M+ papers | Cross-repository search spanning arXiv, PubMed, ACM, IEEE, Springer, Elsevier, and thousands more. Citation graphs and open-access PDF links. |
| **OpenAlex** | openalex.org | 250M+ works | Free CC0 catalog of scholarly works, authors, institutions, and concepts. Aggregates from Crossref, PubMed, institutional repos, and more. |
| **CORE** | core.ac.uk | 200M+ works | Largest aggregator of open-access research from 11,000+ repositories worldwide. Best for technical reports, working papers, theses, and grey literature. |
| **Crossref** | api.crossref.org | 150M+ works | DOI registration agency. Metadata for journal articles, conference proceedings, books, and datasets. Citation-formatted output (BibTeX, APA, RIS). |
| **Unpaywall** | api.unpaywall.org | 50M+ OA articles | Open-access PDF discovery layer. Finds legal free PDFs for DOIs across publisher repos, preprint servers, and institutional archives. |

### Institutional Repositories

| Source | Domain | Scale | Description |
|--------|--------|-------|-------------|
| **MIT DSpace** | dspace.mit.edu | 60,000+ works | MIT's institutional repository. Theses, technical reports, white papers, and peer-reviewed articles across all departments. DSpace v6 REST API. |
| **Harvard DASH** | dash.harvard.edu | 58,000+ works | Harvard's open-access repository. Articles, working papers, theses, and case studies. DSpace 8 REST API. |
| **Cornell eCommons** | ecommons.cornell.edu | 24,000+ works | Cornell's institutional repository. Strong in CS, engineering, and policy research. Theses, articles, technical reports, datasets. DSpace 8 REST API. |
| **Penn ScholarlyCommons** | repository.upenn.edu | 43,000+ works | UPenn's institutional repository. Articles, theses, datasets, and conference papers. Strong in AI ethics, governance, and policy. DSpace 8 REST API. |

### Cloud Vendor Documentation

| Source | Domain | Auth | Description |
|--------|--------|------|-------------|
| **AWS Docs** | docs.aws.amazon.com | None | Full-text search over AWS documentation. Uses the public search proxy that powers the AWS docs site. |
| **Google Cloud Docs** | cloud.google.com | API key | Google Cloud documentation via the Developer Knowledge API. Chunked document search. |
| **Microsoft Learn** | learn.microsoft.com | None | Microsoft Learn documentation, training, and reference content. Uses the public search endpoint. |

### IAM & Identity Documentation

Searched via Google Programmable Search Engine (PSE), Vertex AI Search, Brave Search, or SerpAPI. The PSE/Vertex corpus covers these 26 domains:

| Category | Sites |
|----------|-------|
| **Identity Providers / Auth Servers** | keycloak.org, ory.sh, supertokens.com, authelia.com, goauthentik.io, zitadel.com, logto.io, casdoor.org, authgear.com, kanidm.github.io, freeipa.org, syncope.apache.org |
| **Authorization Engines** | openpolicyagent.org, cerbos.dev, authzed.com (SpiceDB), casbin.org, permify.co, topaz.sh, warrant.dev |
| **Zero-Trust / Proxies / Secrets** | pomerium.com, developer.hashicorp.com (Vault), infisical.com |
| **Standards & Explainers** | webauthn.guide, jwt.io, datatracker.ietf.org, zanzibar.academy |

Crawlable projects for local FTS indexing: Keycloak, Ory (Kratos/Hydra/Keto/Oathkeeper), Open Policy Agent, HashiCorp Vault, Apache Syncope, FreeIPA.

### Code & Repositories

| Source | Domain | Auth | Description |
|--------|--------|------|-------------|
| **GitHub Repos** | api.github.com | Optional | Search repositories by topic, language, stars. Token raises rate limit from 60 to 5,000 req/hr. |
| **GitHub Code** | api.github.com | Required | Search code across all public repositories. Requires a GitHub PAT with public-repo read access. |

---

## Architecture

### System Context

```
                           ┌──────────────────────────┐
                           │       MCP Client         │
                           │   (Claude Desktop /      │
                           │    Claude Code / IDE)     │
                           └────────────┬─────────────┘
                                        │ MCP Protocol
                                        │ (stdio / SSE)
                           ┌────────────▼─────────────┐
                           │  My Research MCP Server   │
                           │  ┌─────────────────────┐  │
                           │  │  47 Tools (FastMCP)  │  │
                           │  └─────────────────────┘  │
                           └──┬────────┬──────────┬────┘
                              │        │          │
             ┌────────────────┤        │          ├────────────────┐
             ▼                ▼        ▼          ▼                ▼
      ┌──────────┐    ┌──────────┐  ┌─────┐  ┌────────┐   ┌──────────┐
      │ Academic  │    │ Institu- │  │Cloud│  │  IAM   │   │  GitHub  │
      │   APIs    │    │  tional  │  │Docs │  │  Docs  │   │   API    │
      │          │    │  Repos   │  │     │  │        │   │          │
      │ arXiv    │    │ MIT      │  │ AWS │  │ Google │   │ Repos    │
      │ Sem.Sch. │    │ Harvard  │  │ GCP │  │  PSE / │   │ Code     │
      │ OpenAlex │    │ Cornell  │  │ MS  │  │ Vertex │   │ READMEs  │
      │ CORE     │    │ Penn     │  │Learn│  │ Brave  │   │          │
      │ Crossref │    │          │  │     │  │ SerpAPI│   │          │
      │ Unpaywall│    │          │  │     │  │        │   │          │
      └──────────┘    └──────────┘  └─────┘  └────────┘   └──────────┘
```

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        My Research MCP Server                          │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                         FastMCP Layer                             │  │
│  │  47 @mcp.tool() functions — search, download, index, query, SQL  │  │
│  └───────┬──────────┬──────────────┬──────────────┬─────────────────┘  │
│          │          │              │              │                     │
│          ▼          ▼              ▼              ▼                     │
│  ┌─────────────┐ ┌────────┐ ┌──────────┐ ┌────────────────────────┐   │
│  │  HTTP       │ │Paper   │ │ DuckDB   │ │  fastembed             │   │
│  │  Clients    │ │Index   │ │ Engine   │ │  Embeddings            │   │
│  │             │ │        │ │          │ │                        │   │
│  │ requests    │ │SQLite  │ │Analytics │ │ ONNX model             │   │
│  │ + rate      │ │FTS5    │ │SQL over  │ │ (BAAI/bge-small-en)    │   │
│  │ limiters    │ │+ BM25  │ │SQLite +  │ │ HNSW cosine search     │   │
│  │ (3s arXiv,  │ │        │ │datasets  │ │ in DuckDB              │   │
│  │  1s S2,     │ │papers  │ │(Parquet, │ │                        │   │
│  │  6.5s CORE) │ │chunks  │ │CSV, JSON)│ │ embeddings.duckdb      │   │
│  └─────────────┘ │chunks_ │ └──────────┘ └────────────────────────┘   │
│                  │fts     │                                            │
│                  └────────┘                                            │
│                                                                         │
│                  ┌────────────────────────────────────────────────┐     │
│                  │                 Filesystem                     │     │
│                  │  ~/arxiv-papers/            (PDFs + SQLite DB) │     │
│                  │  ~/research-datasets/       (Parquet/CSV/JSON) │     │
│                  └────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

### Sequence: Search → Download → Index → Query

```
MCP Client          MCP Server             External API        Filesystem
    │                    │                       │                  │
    │  search_arxiv()    │                       │                  │
    │───────────────────>│   GET /api/query      │                  │
    │                    │──────────────────────->│                  │
    │                    │   Atom XML response    │                  │
    │                    │<──────────────────────-│                  │
    │   JSON results     │                       │                  │
    │<───────────────────│                       │                  │
    │                    │                       │                  │
    │  download_paper()  │                       │                  │
    │───────────────────>│   GET /pdf/{id}       │                  │
    │                    │──────────────────────->│                  │
    │                    │   PDF bytes (stream)   │                  │
    │                    │<──────────────────────-│                  │
    │                    │                       │   Write PDF      │
    │                    │                       │─────────────────>│
    │                    │                       │                  │
    │                    │──── PyMuPDF extract ──────────────────-->│
    │                    │<─── pages[] ────────────────────────────-│
    │                    │                       │                  │
    │                    │──── chunk_pages() ────>│                  │
    │                    │──── SQLite upsert ────────────────────-->│
    │   JSON (indexed)   │                       │                  │
    │<───────────────────│                       │                  │
    │                    │                       │                  │
    │  query_papers()    │                       │                  │
    │───────────────────>│                       │                  │
    │                    │── FTS5 MATCH query ──────────────────-->│
    │                    │<── BM25-ranked rows ────────────────────│
    │   JSON results     │                       │                  │
    │<───────────────────│                       │                  │
```

### Sequence: Semantic Vector Search

```
MCP Client          MCP Server             fastembed            DuckDB
    │                    │                       │                  │
    │  embed_chunks()    │                       │                  │
    │───────────────────>│                       │                  │
    │                    │── fetch unembedded ──────────────────-->│
    │                    │<── chunk texts ─────────────────────────│
    │                    │   encode(texts)        │                  │
    │                    │──────────────────────->│                  │
    │                    │   float[][] vectors    │                  │
    │                    │<──────────────────────-│                  │
    │                    │── INSERT embeddings ─────────────────-->│
    │   JSON (count)     │                       │                  │
    │<───────────────────│                       │                  │
    │                    │                       │                  │
    │ semantic_search()  │                       │                  │
    │───────────────────>│   encode(query)       │                  │
    │                    │──────────────────────->│                  │
    │                    │   query vector         │                  │
    │                    │<──────────────────────-│                  │
    │                    │── cosine similarity ─────────────────-->│
    │                    │<── ranked chunks ───────────────────────│
    │   JSON results     │                       │                  │
    │<───────────────────│                       │                  │
```

---

## Tools (47 total)

### arXiv API (2 tools)
| Tool | Description |
|------|-------------|
| `search_arxiv` | Search arXiv's catalog with full query syntax (`ti:`, `au:`, `cat:`, `all:`) |
| `get_paper_metadata` | Fetch metadata by arXiv ID |

### Download & Index (3 tools)
| Tool | Description |
|------|-------------|
| `download_paper` | Download PDF from arXiv + auto-index full text |
| `index_paper` | Manually index/re-index a single paper |
| `index_all_papers` | Batch-index all PDFs in the download directory |

### Full-Text Search (2 tools)
| Tool | Description |
|------|-------------|
| `query_papers` | **Full-text search across all indexed paper content** — finds specific passages, methods, results, equations |
| `get_paper_text` | Retrieve full text or specific pages of an indexed paper |

### Management (3 tools)
| Tool | Description |
|------|-------------|
| `list_indexed_papers` | List all papers in the index with stats |
| `remove_paper` | Remove a paper from the index |
| `index_stats` | Get index statistics |

### Semantic Scholar (2 tools)
| Tool | Description |
|------|-------------|
| `search_semantic_scholar` | Cross-repository search across arXiv, PubMed, ACM, IEEE, Springer, and more |
| `get_semantic_scholar_paper` | Get detailed metadata, citations, and open-access PDF links by paper ID or DOI |

### Institutional Repositories (8 tools)
| Tool | Description |
|------|-------------|
| `search_mit_dspace` | Search MIT's 60,000+ works (theses, reports, articles) |
| `get_mit_dspace_item` | Get full metadata + downloadable files for an MIT DSpace item |
| `search_harvard_dash` | Search Harvard's 58,000+ open-access works |
| `get_harvard_dash_item` | Get full metadata for a Harvard DASH item |
| `search_cornell_ecommons` | Search Cornell's 24,000+ works (CS, engineering, policy) |
| `get_cornell_ecommons_item` | Get full metadata for a Cornell eCommons item |
| `search_penn_scholarly` | Search UPenn's 43,000+ works (AI ethics, governance) |
| `get_penn_scholarly_item` | Get full metadata for a Penn ScholarlyCommons item |

### DOI / Crossref (4 tools)
| Tool | Description |
|------|-------------|
| `resolve_doi` | Resolve a DOI to full metadata via Crossref + DataCite |
| `search_crossref` | Search 150M+ works in Crossref by query, author, year |
| `get_doi_citation` | Get formatted citation (BibTeX, APA, RIS, etc.) via content negotiation |
| `download_paper_by_doi` | Find and download open-access PDF by DOI (Unpaywall + Semantic Scholar) |

### OpenAlex (3 tools)
| Tool | Description |
|------|-------------|
| `search_openalex` | Search 250M+ works in OpenAlex (free, CC0 catalog) |
| `get_openalex_work` | Get full metadata for an OpenAlex work |
| `search_openalex_authors` | Search for authors with publication stats |

### CORE (3 tools)
| Tool | Description |
|------|-------------|
| `search_core` | Search 200M+ open-access works from 11,000+ repositories |
| `get_core_work` | Get full metadata for a CORE work |
| `download_core_paper` | Download PDF from CORE + auto-index |

### Cloud Vendor Documentation (4 tools)
| Tool | Description |
|------|-------------|
| `search_aws_docs` | Search official AWS documentation (docs.aws.amazon.com) |
| `search_gcp_docs` | Search Google Cloud documentation via Developer Knowledge API |
| `search_microsoft_docs` | Search Microsoft Learn documentation (learn.microsoft.com) |
| `fetch_cloud_doc_page` | Fetch and extract plain text from a cloud documentation page |

### IAM Documentation (4 tools)
| Tool | Description |
|------|-------------|
| `search_iam_docs` | Live search across 26 OSS IAM documentation sites (see Data Sources table) |
| `index_iam_project` | Crawl and index an IAM project's docs for offline full-text search |
| `search_iam_index` | Search locally indexed IAM documentation |
| `list_iam_indexed` | List all indexed IAM projects with stats |

### GitHub (3 tools)
| Tool | Description |
|------|-------------|
| `search_github_repos` | Search GitHub repositories by topic, language, stars |
| `search_github_code` | Search code across public repos (requires GITHUB_TOKEN) |
| `fetch_github_readme` | Fetch a repository's README as plain text |

### Analytics & Embeddings (6 tools)
| Tool | Description |
|------|-------------|
| `analytics_sql` | Run read-only SQL over the paper index via DuckDB |
| `list_datasets` | List Parquet/CSV/JSON files available for querying |
| `dataset_query` | Run SQL over external dataset files via DuckDB |
| `embedding_stats` | Show embedding coverage stats |
| `embed_chunks` | Embed paper chunks with local ONNX model (fastembed) |
| `semantic_search` | Vector similarity search over embedded paper chunks |

---

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
You: Search for papers on "policy-as-code" across multiple sources
  -> search_arxiv("all:policy-as-code", category="cs.SE")
  -> search_semantic_scholar("policy-as-code", fields_of_study="Computer Science")
  -> search_openalex("policy-as-code")

You: Download the top result and that DOI from the Crossref hit
  -> download_paper("2401.xxxxx")              # arXiv PDF, auto-indexed
  -> download_paper_by_doi("10.1145/3649835")  # OA PDF via Unpaywall

You: What do these papers say about OPA Rego validation?
  -> query_papers("OPA Rego validation")
  Returns: matching text passages with page numbers and headings

You: Search the OPA docs for Rego policy testing
  -> search_iam_docs("Rego policy testing")

You: Check how AWS and Azure handle policy-as-code
  -> search_aws_docs("policy as code CloudFormation Guard")
  -> search_microsoft_docs("Azure Policy definition")

You: Run some analytics on my indexed papers
  -> analytics_sql("SELECT SUBSTR(published,1,4) AS year, COUNT(*) AS n
                     FROM papers_db.papers GROUP BY 1 ORDER BY 1 DESC")

You: Find semantically similar passages to "access control policy evaluation"
  -> embed_chunks()
  -> semantic_search("access control policy evaluation")
```

## License

MIT
