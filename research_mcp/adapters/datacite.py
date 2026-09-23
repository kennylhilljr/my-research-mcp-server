"""Searchable DataCite research artifact adapter."""

from typing import Any
from urllib.parse import parse_qs, urlparse

from research_mcp.models import SearchRequest, WorkSummary

from .base import BaseAdapter, digest_id, parse_datetime


class DataCiteAdapter(BaseAdapter):
    name = "datacite"
    api_url = "https://api.datacite.org/dois"

    def _normalize(self, record: dict[str, Any]) -> WorkSummary:
        attributes = record.get("attributes", {})
        doi = str(attributes.get("doi") or record.get("id") or "")
        titles = attributes.get("titles", [])
        title = next((item.get("title") for item in titles if item.get("title")), "Untitled")
        descriptions = attributes.get("descriptions", [])
        abstract = next(
            (
                item.get("description")
                for item in descriptions
                if item.get("descriptionType") == "Abstract"
            ),
            None,
        )
        creators = [
            str(item.get("name")) for item in attributes.get("creators", []) if item.get("name")
        ]
        return WorkSummary(
            document_id=digest_id(self.name, doi),
            title=str(title),
            authors=creators,
            published_at=parse_datetime(attributes.get("published") or attributes.get("created")),
            identifiers={"doi": doi} if doi else {},
            abstract_excerpt=abstract,
            provenance=self.provenance(
                doi, attributes.get("url") or (f"https://doi.org/{doi}" if doi else None)
            ),
            extensions={
                "datacite": {
                    "resource_type": attributes.get("types", {}).get("resourceTypeGeneral"),
                    "rights": attributes.get("rightsList", []),
                }
            },
        )

    @staticmethod
    def _next_page(payload: dict[str, Any]) -> int | None:
        next_url = payload.get("links", {}).get("next")
        if not next_url:
            return None
        raw = parse_qs(urlparse(str(next_url)).query).get("page[number]", [None])[0]
        try:
            page = int(raw)
        except (TypeError, ValueError):
            return None
        return page if page > 0 else None

    async def search(self, request: SearchRequest):
        cursor = self.validate_request(request)
        page_number = cursor.get("page", 1)
        if not isinstance(page_number, int) or page_number < 1:
            raise ValueError("invalid page cursor")
        params = {
            "query": request.query.strip(),
            "page[size]": request.page_size,
            "page[number]": page_number,
        }
        resource_type = request.filters.get("resource_type")
        if resource_type:
            params["resource-type-id"] = str(resource_type)
        creators = request.filters.get("creators")
        if creators:
            creator_values = creators if isinstance(creators, list) else [str(creators)]
            params["query"] += " " + " ".join(creator_values)
        if request.filters.get("date_from") or request.filters.get("date_to"):
            start = request.filters.get("date_from", "")
            end = request.filters.get("date_to", "")
            params["published"] = f"{start},{end}"
        payload = self.get_json(self.api_url, params=params)
        records = payload.get("data", []) if isinstance(payload, dict) else []
        items = [
            self._normalize(item) for item in records[: request.page_size] if isinstance(item, dict)
        ]
        next_page = self._next_page(payload) if isinstance(payload, dict) else None
        return self.page(items, next_state={"page": next_page} if next_page else None)

    async def get_work(self, source_id: str):
        if not source_id or len(source_id) > 300:
            raise ValueError("invalid DOI")
        payload = self.get_json(f"{self.api_url}/{source_id}")
        return self._normalize(payload.get("data", {}))

    async def list_documents(self, source_id: str):
        return []
