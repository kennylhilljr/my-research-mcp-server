# Research MCP: Fix Plan (2026-10-18)

Companion to `RESEARCH_MCP_MODERNIZATION_SPEC.md`. That spec is the long-range roadmap; this file is the short list of defects and quick wins found while using the server for a real research task on 2026-10-18. Every item below was reproduced against `main` on that date.

## Baseline (main, Python 3.10, mcp 1.30.0)

| Check | Result |
|---|---|
| `pytest` (all files) | Collection stops: 2 test modules import code that does not exist |
| `pytest` (those 2 ignored) | 231 passed, 2 failed |
| `ruff check .` | 31 errors, including 5 `invalid-syntax` in `research_mcp/prompts.py` |
| `import server` with mcp 2.x | Fails: `mcp.server.fastmcp` no longer exists |

Run the suite without writing caches into the repo:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
ruff check . --no-cache --exclude .venv
```

## How to split the work

`server.py` is a single 3,700-line file, so parallel agents should own separate regions and merge in this order: WP1, WP2, WP3, WP4, WP5, then the lint pass (WP1.3) last. One branch per work package.

| WP | Owns | Touches |
|---|---|---|
| WP1 Build and environment | `pyproject.toml`, `research_mcp/prompts.py`, lint | No `server.py` edits until the final lint pass |
| WP2 Test suite health | `tests/`, `research_mcp/repository.py` | None in `server.py` |
| WP3 Search and parsing bugs | `server.py` lines ~210-365, ~1640-1680, ~1860-1915 | PaperIndex, DOI citation, OpenAlex parser |
| WP4 Institutional repositories | `server.py` lines ~1060-1400 | MIT DSpace, DSpace 8 helpers |
| WP5 Expose existing adapters | New tool block in `server.py` (append near other tool sections), `CLAUDE.md` | New code only |

## WP1: Build and environment

### 1.1 Pin the MCP SDK below 2.0 (P0)
- **Problem:** `server.py:38` imports `from mcp.server.fastmcp import FastMCP`. That module is gone in mcp 2.x, and `pyproject.toml` allows `mcp>=1.0.0`, so a fresh install picks 2.x and the server will not start.
- **Fix:** `mcp>=1.0.0,<2`. Migrating to the 2.x API belongs to the modernization spec, not this plan.
- **Accept:** fresh venv, `pip install -e .[dev]`, then `python -c "import server"` succeeds and `mcp` resolves to 1.x.

### 1.2 Python 3.10 syntax error in prompts (P0)
- **Problem:** `research_mcp/prompts.py:44-47` uses an f-string with line breaks and reused quotes inside a replacement field. That is valid only on Python 3.12+, but `requires-python = ">=3.10"`. Ruff reports 5 `invalid-syntax` errors.
- **Fix:** build the string outside the f-string (or use a triple-quoted f-string) so it parses on 3.10. Do not raise `requires-python` unless Kenny decides to drop 3.10 and 3.11.
- **Accept:** `ruff check` shows no `invalid-syntax`; `python3.10 -c "import research_mcp.prompts"` works.

### 1.3 Remaining lint (P2, do last)
- 26 other findings: 15 E501, 7 I001, 3 F401, 1 F841. Run `ruff check --fix` for the 11 auto-fixable ones after all other branches merge, then hand-fix the rest.
- **Accept:** `ruff check .` exits 0.

## WP2: Test suite health

### 2.1 Contract tests for code that does not exist yet (P1)
- **Problem:** `tests/test_modern_mcp.py` imports `research_mcp.federation` and `research_mcp.server.build_mcp`; `tests/test_research_workflows.py` imports `EvidenceItem` from `research_mcp.models`. None of these exist on any branch, so collection fails and hides every other result.
- **Fix:** keep the tests (they document the modernization contract) but gate them: `pytest.importorskip("research_mcp.federation")` at the top of each module, or a `modern` marker skipped by default. Kenny decides whether they should instead be implemented now.
- **Accept:** plain `pytest` collects all modules; the gated ones report as skipped with a reason.

### 2.2 Legacy migration fails on naive timestamps (P1)
- **Problem:** `tests/test_modern_repository.py::test_migration_from_legacy_preserves_tables_and_reconciles` raises `ValueError: <name> must be timezone-aware`. The legacy SQLite rows store naive datetimes; the migration path in `research_mcp/repository.py` passes them straight to a validator that requires tz-aware values.
- **Fix:** in the migration, parse legacy timestamps as UTC before building models.
- **Accept:** the test passes; add a case with a naive legacy timestamp.

## WP3: Search and parsing bugs

### 3.1 `search_openalex` crashes on null nested fields (P0)
- **Problem:** `server.py` ~1905, inside `_parse_openalex_work`: `(work.get("primary_location") or {}).get("source", {}).get("display_name", "")`. OpenAlex returns `"source": null` for some works, so `.get("source", {})` yields `None` and the call raises `AttributeError: 'NoneType' object has no attribute 'get'`. Repro: `search_openalex("Machiavellianism emotion recognition Konrath")`.
- **Fix:** `((work.get("primary_location") or {}).get("source") or {}).get("display_name", "")`. Audit the rest of the function for the same `.get(key, {})` pattern on nullable fields.
- **Accept:** unit test with `primary_location.source = None` and with `best_oa_location = None`; the repro query returns results.

### 3.2 `test_empty_work` expectation (P1)
- **Problem:** `tests/test_server.py:869` expects `title == ""` for an empty work; the parser returns `"[Unknown Work ]"` because the fallback ignores an empty id.
- **Fix:** use the `[Unknown Work <id>]` fallback only when an id exists; otherwise return `""`.
- **Accept:** the test passes.

### 3.3 FTS5 query fails on hyphens and punctuation (P0)
- **Problem:** `PaperIndex.to_fts_query` (`server.py` ~351-363) passes bare tokens straight to `MATCH`. A hyphenated word such as `self-respect` is parsed by FTS5 as a column filter and fails with `no such column`.
- **Fix:** wrap each bare token as an FTS5 string literal (`"self-respect"`, doubling any embedded `"`). Keep the pass-through for queries that already use `AND`/`OR`/`NOT`/`NEAR(`, quoted phrases, or `*`.
- **Accept:** tests for hyphen, colon, apostrophe, and a mixed natural-language query; no FTS5 syntax errors.

### 3.4 `query_papers` returns no snippet (P2)
- **Problem:** results carry the full ~1,500-character chunk as `content` and no `snippet` field. Clients and agents that read `snippet` see empty results, which is how this was first reported.
- **Fix:** add a `snippet` field using FTS5 `snippet(chunks_fts, <content column index>, '[', ']', '...', 32)`; keep `content` for compatibility.
- **Accept:** each result has a non-empty `snippet` containing the matched term.

### 3.5 `get_doi_citation` garbles non-ASCII text (P1)
- **Problem:** `server.py` ~1664-1674 returns `resp.text` from DOI content negotiation. The `text/x-bibliography` response often omits a charset, `requests` falls back to ISO-8859-1, and en-dashes in page ranges come back as mojibake.
- **Fix:** set `resp.encoding = "utf-8"` before reading `resp.text` (the CSL citation service returns UTF-8).
- **Accept:** a mocked UTF-8 response with an en-dash and an accented author name round-trips intact.

## WP4: Institutional repositories

### 4.1 MIT DSpace is dead (P0)
- **Problem:** `DSPACE_MIT_BASE = "https://dspace.mit.edu/rest"` (`server.py:1064`) targets the DSpace 6 REST API, which MIT retired; `/rest/filtered-items` returns 404. As of 2026-10-18, `https://dspace.mit.edu/server/api` reports DSpace 8.2, and `/server/api/discover/search/objects` returns 200.
- **Fix:** add an `"mit"` entry to `DSPACE8_REPOS` (`server.py:1233`, `api_base = https://dspace.mit.edu/server/api`, `web_base = https://dspace.mit.edu`) and route `search_mit_dspace` and `get_mit_dspace_item` through the existing `_dspace8_*` helpers, the same way Harvard, Cornell and Penn work. Remove the DSpace 6 code path and its tests once replaced. Keep the two tool names so callers do not break.
- **Accept:** live search for `narcissism` returns MIT items with titles, authors and handle URLs; item fetch by UUID works.

### 4.2 DSpace item fetch raises instead of returning an error (P1)
- **Problem:** `_dspace8_get_item` (`server.py` ~1318-1328) calls `resp.raise_for_status()` without handling it, so a bad or private UUID crashes the tool call (Harvard returned 401 for an invalid UUID on 2026-10-18).
- **Fix:** catch `requests.HTTPError` and return the same JSON error shape the other tools use, including status code and UUID.
- **Accept:** test with a mocked 401 and 404.

### 4.3 Repository search noise (P3, optional)
- **Problem:** keyword search across DSpace returns non-scholarly items (Harvard `romance scam` returns a film-archive calendar).
- **Fix:** optional `item_type` parameter mapped to DSpace discovery filters, defaulting to articles and theses.
- **Accept:** default search excludes `Other` items; the filter is documented.

## WP5: Expose the adapters that already exist (P1)

- **Problem:** `research_mcp/adapters/` contains working, tested adapters for Europe PMC, Crossref retraction relations, OpenCitations, DataCite and ClinicalTrials.gov (`tests/test_source_adapters.py`), but none is registered as an MCP tool. The server still exposes the same 47 tools, so agents can reach these sources only by importing Python directly.
- **Fix:** register thin tools in the existing style (JSON string returns, same docstring format):
  - `search_europe_pmc(query, max_results)`: covers PubMed, PMC and preprints, and already returns MeSH headings. This removes the need for a separate PubMed connector.
  - `check_retractions(dois)`: per-DOI Crossref `updated-by` / `update-to` relations plus OpenAlex `is_retracted`. The adapter's keyword `search` is not enough for this; use the per-DOI lookup.
  - `get_citations(doi)` via OpenCitations (citing and cited works).
  - `search_clinical_trials(query, ...)`.
  - DataCite is already reached through `resolve_doi`; do not add a duplicate tool.
- **Accept:** each new tool appears in the MCP tool list, has a mocked-HTTP unit test, and passes one live smoke query; `CLAUDE.md` tool inventory and count are updated.

## Configuration notes (no code change)

- `search_core` needs `CORE_API_KEY`; document it as required in the README rather than optional.
- `search_iam_docs` (Google PSE, Brave, SerpAPI fallbacks) is scheduled for decommission; leave it alone.

## External APIs evaluated on 2026-10-18 (none recommended)

| API | Verdict | Reason |
|---|---|---|
| Crossref | Already in | `search_crossref`, `resolve_doi`, `get_doi_citation` |
| OpenAlex | Already in | `search_openalex`, `get_openalex_work`, `search_openalex_authors` |
| PubMed / NCBI E-utilities | Skip | WP5's Europe PMC tool covers PubMed and MeSH |
| Sentino Personality API | Skip | Scores personality from text; no literature retrieval. API access starts at the $99/month tier; the API page gives no accuracy or validity data. |
| Receptiviti | Skip | LIWC-style text scoring; web API starts around $500/month with a 3-month minimum. Not a research source. |
| PsyPack | Skip | Clinical assessment delivery for clinics and apps; enterprise sales only |
| Psychologie et Serenite API | Skip | Metadata for one practitioner's CBT blog articles; 100 requests/hour, attribution required, no formal license |

A source worth considering later is PsyArXiv (psychology preprints) through the OSF API. Verify its search capabilities before scoping a tool.

## Done when

- [ ] `pip install -e .[dev]` in a fresh venv, `python -c "import server"` works
- [ ] `pytest` collects every module and passes (gated modern tests show as skipped)
- [ ] `ruff check .` exits 0
- [ ] Live smoke: `search_openalex` repro query, `query_papers("self-respect")`, `search_mit_dspace("narcissism")`, `search_europe_pmc("gaslighting")`, `check_retractions(["10.1037/a0017838"])` (should report the 2010 correction)
- [ ] `CLAUDE.md` tool count and inventory match the registered tools
