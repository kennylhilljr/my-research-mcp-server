import asyncio

import pytest

from research_mcp.adapters.base import AdapterError, decode_cursor, encode_cursor
from research_mcp.adapters.clinical_trials import ClinicalTrialsAdapter
from research_mcp.adapters.datacite import DataCiteAdapter
from research_mcp.adapters.europe_pmc import EuropePMCAdapter
from research_mcp.adapters.opencitations import OpenCitationsAdapter
from research_mcp.adapters.retractions import RetractionWatchAdapter
from research_mcp.models import SearchRequest


class FakeResponse:
    def __init__(self, payload, *, status_code=200, content=None):
        self._payload = payload
        self.status_code = status_code
        self.content = content if content is not None else b"{}"

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def run(awaitable):
    return asyncio.run(awaitable)


def request(**kwargs):
    values = {"query": "cancer", "page_size": 10}
    values.update(kwargs)
    return SearchRequest(**values)


def test_cursor_round_trip_and_tamper_rejection():
    cursor = encode_cursor("europe_pmc", {"mark": "abc"})
    assert decode_cursor(cursor, "europe_pmc") == {"mark": "abc"}
    with pytest.raises(ValueError):
        decode_cursor(cursor, "datacite")
    with pytest.raises(ValueError):
        decode_cursor("not-base64", "europe_pmc")


def test_europe_pmc_normalizes_identifiers_mesh_and_cursor():
    client = FakeClient(
        FakeResponse(
            {
                "request": {"cursorMark": "next"},
                "resultList": {
                    "result": [
                        {
                            "id": "MED:123",
                            "pmid": "123",
                            "pmcid": "PMC456",
                            "doi": "10.1/test",
                            "title": "Trial result",
                            "authorString": "A One, B Two",
                            "firstPublicationDate": "2024-01-02",
                            "abstractText": "Evidence",
                            "meshHeadingList": {"meshHeading": [{"descriptorName": "Cancer"}]},
                        }
                    ]
                },
            }
        )
    )
    page = run(EuropePMCAdapter(client).search(request()))
    item = page.items[0]
    assert item.identifiers == {"pmid": "123", "pmcid": "PMC456", "doi": "10.1/test"}
    assert item.extensions["europe_pmc"]["mesh_headings"] == ["Cancer"]
    assert item.provenance.source == "europe_pmc"
    assert decode_cursor(page.next_cursor, "europe_pmc") == {"mark": "next"}
    assert client.calls[0][1]["params"]["pageSize"] == 10


def test_retraction_watch_surfaces_prominent_warning():
    client = FakeClient(
        FakeResponse(
            {
                "message": {
                    "items": [
                        {
                            "DOI": "10.1/retracted",
                            "title": ["Retracted paper"],
                            "type": "retraction",
                            "publisher": "Journal",
                            "updated": {"date-time": "2025-01-01T00:00:00Z"},
                        }
                    ],
                    "next-cursor": "cursor-2",
                }
            }
        )
    )
    page = run(RetractionWatchAdapter(client).search(request(query="10.1/retracted")))
    assert page.items[0].extensions["retraction_watch"]["status"] == "retraction"
    assert any("RETRACTION" in warning for warning in page.warnings)
    assert decode_cursor(page.next_cursor, "retraction_watch") == {"cursor": "cursor-2"}


def test_clinical_trials_normalizes_nct_and_publication_links():
    payload = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {"nctId": "NCT123", "briefTitle": "Study"},
                    "contactsLocationsModule": {"overallOfficials": [{"name": "Dr A"}]},
                    "statusModule": {"startDateStruct": {"date": "2023-04"}},
                    "referencesModule": {"references": [{"pmid": "99", "type": "RESULT"}]},
                }
            }
        ],
        "nextPageToken": "token-2",
    }
    page = run(ClinicalTrialsAdapter(FakeClient(FakeResponse(payload))).search(request()))
    item = page.items[0]
    assert item.identifiers["nct"] == "NCT123"
    assert item.extensions["clinical_trials"]["publications"][0]["pmid"] == "99"
    assert decode_cursor(page.next_cursor, "clinical_trials") == {"page_token": "token-2"}


def test_opencitations_keeps_graph_provenance_distinct():
    payload = [{"citing": "10.2/citing", "cited": "10.1/source", "creation": "2022"}]
    page = run(
        OpenCitationsAdapter(FakeClient(FakeResponse(payload))).search(
            request(query="10.1/source", filters={"direction": "citations"})
        )
    )
    assert page.items[0].identifiers["doi"] == "10.2/citing"
    assert page.items[0].provenance.source == "opencitations"
    assert page.items[0].extensions["opencitations"]["cited"] == "10.1/source"


def test_datacite_normalizes_artifact_and_links_offset_cursor():
    payload = {
        "data": [
            {
                "id": "10.5/data",
                "attributes": {
                    "doi": "10.5/data",
                    "titles": [{"title": "Dataset"}],
                    "creators": [{"name": "A Researcher"}],
                    "published": "2024-02-03",
                    "descriptions": [
                        {"description": "Reusable data", "descriptionType": "Abstract"}
                    ],
                    "types": {"resourceTypeGeneral": "Dataset"},
                    "url": "https://example.org/data",
                },
            }
        ],
        "links": {"next": "https://api.datacite.org/dois?page[number]=2&page[size]=10"},
    }
    page = run(DataCiteAdapter(FakeClient(FakeResponse(payload))).search(request()))
    assert page.items[0].identifiers["doi"] == "10.5/data"
    assert page.items[0].extensions["datacite"]["resource_type"] == "Dataset"
    assert decode_cursor(page.next_cursor, "datacite") == {"page": 2}


@pytest.mark.parametrize("adapter", [EuropePMCAdapter, ClinicalTrialsAdapter, DataCiteAdapter])
def test_adapters_reject_invalid_page_size(adapter):
    with pytest.raises(ValueError):
        run(adapter(FakeClient()).search(request(page_size=51)))


def test_adapter_rejects_oversized_and_error_responses():
    too_large = FakeResponse({}, content=b"x" * 1001)
    with pytest.raises(AdapterError, match="response exceeded"):
        run(EuropePMCAdapter(FakeClient(too_large), max_response_bytes=1000).search(request()))

    with pytest.raises(AdapterError, match="status 429"):
        run(EuropePMCAdapter(FakeClient(FakeResponse({}, status_code=429))).search(request()))
