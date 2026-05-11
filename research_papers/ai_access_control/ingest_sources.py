#!/usr/bin/env python3
"""Index the AI access-control source set with my-research-mcp-server.

This script intentionally uses the repo's native `server.py` functions instead
of an HTTP API. arXiv sources are downloaded directly by arXiv ID; DOI sources
use the server's Unpaywall/Semantic Scholar/Crossref fallback path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
SOURCES_FILE = SCRIPT_DIR / "sources.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--priority",
        type=int,
        choices=(1, 2),
        help="Only ingest sources at or above this priority, e.g. --priority 1.",
    )
    parser.add_argument(
        "--download-dir",
        help="Override paper download directory. Defaults to ARXIV_DOWNLOAD_DIR or ~/arxiv-papers.",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Download PDFs without extracting/indexing full text.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be ingested without downloading anything.",
    )
    return parser.parse_args()


def load_sources() -> list[dict[str, Any]]:
    return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))


def call_json(fn, *args, **kwargs) -> dict[str, Any]:
    raw = fn(*args, **kwargs)
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def main() -> int:
    args = parse_args()
    os.chdir(REPO_ROOT)
    sys.path.insert(0, str(REPO_ROOT))

    import server  # noqa: PLC0415

    sources = load_sources()
    if args.priority:
        sources = [s for s in sources if int(s.get("priority", 99)) <= args.priority]

    results: list[dict[str, Any]] = []
    auto_index = not args.no_index

    for source in sources:
        title = source["title"]
        arxiv_id = source.get("arxiv_id")
        doi = source.get("doi")

        if args.dry_run:
            method = "download_paper" if arxiv_id else "download_paper_by_doi" if doi else "metadata_only"
            print(f"[dry-run] {method}: {title}")
            results.append({"title": title, "status": "dry_run", "method": method, "source": source})
            continue

        print(f"Indexing: {title}")
        try:
            if arxiv_id:
                result = call_json(
                    server.download_paper,
                    arxiv_id,
                    auto_index=auto_index,
                    download_dir=args.download_dir,
                )
                method = "download_paper"
            elif doi:
                result = call_json(
                    server.download_paper_by_doi,
                    doi,
                    auto_index=auto_index,
                    download_dir=args.download_dir,
                )
                method = "download_paper_by_doi"
            else:
                result = {
                    "status": "skipped",
                    "reason": "No arxiv_id or DOI available for native PDF indexing.",
                    "url": source.get("url"),
                }
                method = "metadata_only"
        except Exception as exc:  # Keep ingest moving across flaky sources.
            result = {"status": "error", "error": str(exc)}
            method = "error"

        results.append({"title": title, "method": method, "source": source, "result": result})

    if args.dry_run:
        print(f"Dry run complete. sources={len(results)}")
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = SCRIPT_DIR / f"ingest_results_{timestamp}.json"
    try:
        output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    except PermissionError:
        fallback_path = Path.cwd() / f"ingest_results_{timestamp}.json"
        fallback_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        output_path = fallback_path

    indexed = sum(1 for item in results if item.get("result", {}).get("indexed"))
    downloaded = sum(1 for item in results if item.get("result", {}).get("status") == "downloaded")
    errored = sum(1 for item in results if item.get("result", {}).get("status") == "error")
    skipped = sum(1 for item in results if item.get("result", {}).get("status") == "skipped")

    print(
        f"Done. downloaded={downloaded} indexed={indexed} "
        f"skipped={skipped} errored={errored} results={output_path}"
    )
    return 0 if errored == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
