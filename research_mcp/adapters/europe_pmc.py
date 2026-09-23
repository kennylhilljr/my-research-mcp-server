"""Europe PMC literature adapter."""

from typing import Any
from urllib.parse import quote

from research_mcp.models import SearchRequest, WorkSummary

from .base import BaseAdapter, digest_id, parse_datetime


class EuropePMCAdapter(BaseAdapter):
    name = "europe_pmc"
    api_url = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def _normalize(self, record: dict[str, Any]) -> WorkSummary:
        source_id = str(record.get("id") or record.get("pmid") or record.get("pmcid") or "")
        identifiers = {
            key: str(record[field])
            for key, field in (("pmid", "pmid"), ("pmcid", "pmcid"), ("doi", "doi"))
            if record.get(field)
        }
        headings = record.get("meshHeadingList", {}).get("meshHeading", [])
        mesh = [str(item["descriptorName"]) for item in headings if item.get("descriptorName")]
        return WorkSummary(
            document_id=digest_id(self.name, source_id),
            title=str(record.get("title") or "Untitled"),
            authors=[
                part.strip()
                for part in str(record.get("authorString") or "").split(",")
                if part.strip()
            ],
            published_at=parse_datetime(record.get("firstPublicationDate")),
            identifiers=identifiers,
            abstract_excerpt=record.get("abstractText"),
            provenance=self.provenance(
                source_id, f"https://europepmc.org/article/{quote(source_id, safe=':')}"
            ),
            extensions={"europe_pmc": {"mesh_headings": mesh}},
        )

    async def search(self, request: SearchRequest):
        cursor = self.validate_request(request)
        query_parts = [request.query.strip()]
        publication_types = request.filters.get("publication_types", [])
        if publication_types:
            if not isinstance(publication_types, list):
                raise ValueError("publication_types must be a list")
            clauses = [f'PUB_TYPE:\"{str(value)[:100]}\"' for value in publication_types]
            query_parts.append("(" + " OR ".join(clauses) + ")")
        if request.filters.get("date_from") or request.filters.get("date_to"):
            start = request.filters.get("date_from", "1000-01-01")
            end = request.filters.get("date_to", "3000-12-31")
            query_parts.append(f"FIRST_PDATE:[{start} TO {end}]")
        if request.filters.get("open_access") is True:
            query_parts.append("OPEN_ACCESS:Y")
        if request.filters.get("include_preprints") is False:
            query_parts.append("NOT SRC:PPR")
        params = {
            "query": " AND ".join(query_parts),
            "format": "json",
            "resultType": "core",
            "pageSize": request.page_size,
        }
        if cursor.get("mark"):
            params["cursorMark"] = cursor["mark"]
        payload = self.get_json(f"{self.api_url}/search", params=params)
        records = (
            payload.get("resultList", {}).get("result", []) if isinstance(payload, dict) else []
        )
        items = [
            self._normalize(item) for item in records[: request.page_size] if isinstance(item, dict)
        ]
        mark = None
        if isinstance(payload, dict):
            mark = payload.get("nextCursorMark") or payload.get("request", {}).get("cursorMark")
        return self.page(items, next_state={"mark": mark} if mark else None)

    async def get_work(self, source_id: str):
        if not source_id or len(source_id) > 200:
            raise ValueError("invalid Europe PMC identifier")
        payload = self.get_json(
            f"{self.api_url}/search",
            params={
                "query": f"EXT_ID:{source_id}",
                "format": "json",
                "resultType": "core",
                "pageSize": 1,
            },
        )
        records = payload.get("resultList", {}).get("result", [])
        if not records:
            raise LookupError(f"Europe PMC work not found: {source_id}")
        return self._normalize(records[0])

    async def list_documents(self, source_id: str):
        return []
