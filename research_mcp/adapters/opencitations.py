"""OpenCitations graph adapter."""

from typing import Any
from urllib.parse import quote

from research_mcp.models import SearchRequest, WorkSummary

from .base import BaseAdapter, digest_id, parse_datetime


class OpenCitationsAdapter(BaseAdapter):
    name = "opencitations"
    api_url = "https://opencitations.net/index/api/v2"

    def _normalize(self, record: dict[str, Any], direction: str) -> WorkSummary:
        result_doi = str(
            record.get("citing") if direction == "citations" else record.get("cited") or ""
        )
        return WorkSummary(
            document_id=digest_id(self.name, result_doi),
            title=f"Citation graph record for {result_doi}",
            authors=[],
            published_at=parse_datetime(record.get("creation")),
            identifiers={"doi": result_doi} if result_doi else {},
            provenance=self.provenance(
                result_doi, f"https://doi.org/{result_doi}" if result_doi else None
            ),
            extensions={"opencitations": dict(record)},
        )

    async def search(self, request: SearchRequest):
        cursor = self.validate_request(request)
        direction = str(request.filters.get("direction", "citations"))
        if direction not in {"citations", "references"}:
            raise ValueError("direction must be citations or references")
        page_number = cursor.get("page", 1)
        if not isinstance(page_number, int) or page_number < 1:
            raise ValueError("invalid page cursor")
        endpoint = "citations" if direction == "citations" else "references"
        payload = self.get_json(
            f"{self.api_url}/{endpoint}/doi:{quote(request.query.strip(), safe='')}",
            params={"page": page_number, "rows": request.page_size},
        )
        records = payload if isinstance(payload, list) else []
        items = [
            self._normalize(item, direction)
            for item in records[: request.page_size]
            if isinstance(item, dict)
        ]
        next_state = {"page": page_number + 1} if len(records) >= request.page_size else None
        return self.page(items, next_state=next_state)

    async def get_work(self, source_id: str):
        result = await self.search(
            SearchRequest(query=source_id, filters={"direction": "citations"}, page_size=1)
        )
        if not result.items:
            raise LookupError(f"OpenCitations record not found: {source_id}")
        return result.items[0]

    async def list_documents(self, source_id: str):
        return []
