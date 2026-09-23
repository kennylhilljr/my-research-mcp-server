"""Crossref Retraction Watch and Crossmark signal adapter."""

from typing import Any

from research_mcp.models import SearchRequest, WorkSummary

from .base import BaseAdapter, digest_id, parse_datetime


class RetractionWatchAdapter(BaseAdapter):
    name = "retraction_watch"
    api_url = "https://api.crossref.org/works"

    def _normalize(self, record: dict[str, Any]) -> WorkSummary:
        doi = str(record.get("DOI") or record.get("doi") or "")
        raw_title = record.get("title") or "Untitled"
        title = raw_title[0] if isinstance(raw_title, list) and raw_title else str(raw_title)
        status = str(record.get("type") or record.get("update-type") or "unknown").lower()
        updated = record.get("updated", {})
        updated_at = updated.get("date-time") if isinstance(updated, dict) else None
        return WorkSummary(
            document_id=digest_id(self.name, doi),
            title=title,
            authors=[],
            published_at=parse_datetime(updated_at),
            identifiers={"doi": doi} if doi else {},
            provenance=self.provenance(doi, f"https://doi.org/{doi}" if doi else None),
            extensions={
                "retraction_watch": {"status": status, "publisher": record.get("publisher")}
            },
        )

    async def search(self, request: SearchRequest):
        cursor = self.validate_request(request)
        params = {
            "query": request.query.strip(),
            "rows": request.page_size,
            "cursor": cursor.get("cursor", "*"),
        }
        filters = []
        if request.filters.get("relation_type"):
            filters.append(f"relation.type:{request.filters['relation_type']}")
        if request.filters.get("updated_since"):
            filters.append(f"from-update-date:{request.filters['updated_since']}")
        if filters:
            params["filter"] = ",".join(filters)
        payload = self.get_json(self.api_url, params=params)
        message = payload.get("message", {}) if isinstance(payload, dict) else {}
        records = message.get("items", [])
        items = [
            self._normalize(item) for item in records[: request.page_size] if isinstance(item, dict)
        ]
        warnings = []
        for item in items:
            status = item.extensions["retraction_watch"]["status"]
            if status in {"retraction", "retracted", "expression-of-concern", "correction"}:
                warnings.append(f"{status.upper()}: {item.identifiers.get('doi', item.title)}")
        next_cursor = message.get("next-cursor")
        return self.page(
            items, next_state={"cursor": next_cursor} if next_cursor else None, warnings=warnings
        )

    async def get_work(self, source_id: str):
        if not source_id or len(source_id) > 300:
            raise ValueError("invalid DOI")
        payload = self.get_json(f"{self.api_url}/{source_id}")
        record = payload.get("message", {})
        return self._normalize(record)

    async def list_documents(self, source_id: str):
        return []
