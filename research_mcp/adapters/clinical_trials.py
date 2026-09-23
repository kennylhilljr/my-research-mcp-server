"""ClinicalTrials.gov v2 adapter."""

from typing import Any

from research_mcp.models import SearchRequest, WorkSummary

from .base import BaseAdapter, digest_id, parse_datetime


class ClinicalTrialsAdapter(BaseAdapter):
    name = "clinical_trials"
    api_url = "https://clinicaltrials.gov/api/v2/studies"

    def _normalize(self, record: dict[str, Any]) -> WorkSummary:
        protocol = record.get("protocolSection", {})
        identification = protocol.get("identificationModule", {})
        contacts = protocol.get("contactsLocationsModule", {})
        status = protocol.get("statusModule", {})
        design = protocol.get("designModule", {})
        references = protocol.get("referencesModule", {}).get("references", [])
        nct_id = str(identification.get("nctId") or "")
        return WorkSummary(
            document_id=digest_id(self.name, nct_id),
            title=str(
                identification.get("briefTitle")
                or identification.get("officialTitle")
                or "Untitled trial"
            ),
            authors=[
                str(item["name"])
                for item in contacts.get("overallOfficials", [])
                if item.get("name")
            ],
            published_at=parse_datetime(status.get("startDateStruct", {}).get("date")),
            identifiers={"nct": nct_id} if nct_id else {},
            abstract_excerpt=protocol.get("descriptionModule", {}).get("briefSummary"),
            provenance=self.provenance(
                nct_id, f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None
            ),
            extensions={
                "clinical_trials": {
                    "overall_status": status.get("overallStatus"),
                    "phases": design.get("phases", []),
                    "publications": [item for item in references if isinstance(item, dict)],
                }
            },
        )

    async def search(self, request: SearchRequest):
        cursor = self.validate_request(request)
        params = {
            "query.term": request.query.strip(),
            "pageSize": request.page_size,
            "format": "json",
        }
        if cursor.get("page_token"):
            params["pageToken"] = cursor["page_token"]
        filter_map = {
            "conditions": "query.cond",
            "interventions": "query.intr",
            "status": "filter.overallStatus",
        }
        for source_key, parameter in filter_map.items():
            value = request.filters.get(source_key)
            if value:
                params[parameter] = ",".join(value) if isinstance(value, list) else str(value)
        if request.filters.get("phase"):
            params["query.advanced"] = f"AREA[Phase]{request.filters['phase']}"
        payload = self.get_json(self.api_url, params=params)
        records = payload.get("studies", []) if isinstance(payload, dict) else []
        items = [
            self._normalize(item) for item in records[: request.page_size] if isinstance(item, dict)
        ]
        token = payload.get("nextPageToken") if isinstance(payload, dict) else None
        return self.page(items, next_state={"page_token": token} if token else None)

    async def get_work(self, source_id: str):
        if not source_id.startswith("NCT") or len(source_id) > 20:
            raise ValueError("invalid NCT identifier")
        return self._normalize(self.get_json(f"{self.api_url}/{source_id}"))

    async def list_documents(self, source_id: str):
        return []
