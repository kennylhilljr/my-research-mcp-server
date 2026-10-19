"""Tests for the source adapter MCP tools (Europe PMC, Crossref updates,
OpenCitations, ClinicalTrials.gov). All HTTP is mocked via server.requests.get."""

import asyncio
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import (  # noqa: E402
    check_retractions,
    get_citations,
    search_clinical_trials,
    search_europe_pmc,
)


def fake_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = json.dumps(payload).encode()
    resp.json.return_value = payload
    return resp


def run(coro):
    return json.loads(asyncio.run(coro))


class Router:
    """Return canned responses keyed on URL substrings (first match wins)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, params=None, **kwargs):
        self.calls.append((url, params or {}))
        for needle, response in self.routes:
            if needle(url, params or {}) if callable(needle) else needle in url:
                return response
        return fake_response({}, status_code=404)


EUROPE_PMC_PAYLOAD = {
    "nextCursorMark": "AoE123",
    "resultList": {"result": [{
        "id": "36000001",
        "pmid": "36000001",
        "pmcid": "PMC9000001",
        "doi": "10.1000/gaslight",
        "title": "Gaslighting in intimate relationships",
        "authorString": "Klein W, Wood S.",
        "firstPublicationDate": "2022-06-04",
        "abstractText": "<h4>Background</h4>Gaslighting is a form of abuse.",
        "meshHeadingList": {"meshHeading": [
            {"descriptorName": "Intimate Partner Violence"},
            {"descriptorName": "Humans"},
        ]},
    }]},
}


class TestSearchEuropePmc:
    def test_returns_entries_with_mesh(self):
        router = Router([("europepmc", fake_response(EUROPE_PMC_PAYLOAD))])
        with patch("server.requests.get", side_effect=router):
            out = run(search_europe_pmc("gaslighting", max_results=5, open_access_only=True))
        entry = out["entries"][0]
        assert out["source"] == "Europe PMC" and out["results_count"] == 1
        assert entry["mesh_headings"] == ["Intimate Partner Violence", "Humans"]
        assert entry["pmid"] == "36000001" and entry["doi"] == "10.1000/gaslight"
        assert entry["published"] == "2022-06-04"
        assert entry["abstract"] == "BackgroundGaslighting is a form of abuse."
        assert out["next_cursor"]
        params = router.calls[0][1]
        assert params["pageSize"] == 5 and params["resultType"] == "core"
        assert "OPEN_ACCESS:Y" in params["query"]

    def test_http_error_returns_error_json(self):
        with patch("server.requests.get", return_value=fake_response({}, status_code=503)):
            out = run(search_europe_pmc("gaslighting"))
        assert "error" in out and "503" in out["error"]


WORK_WITH_CORRECTION = {"message": {
    "DOI": "10.1037/a0017838",
    "title": ["The doormat effect"],
    "type": "journal-article",
    "updated-by": [{
        "DOI": "10.1037/a0020156", "type": "correction", "label": "Correction",
        "source": "publisher",
        "updated": {"date-parts": [[2010, 6, 21]], "date-time": "2010-06-21T00:00:00Z"},
    }],
}}
NOTICES = {"message": {"items": [{
    "DOI": "10.1037/a0020156",
    "update-to": [{
        "DOI": "10.1037/A0017838", "type": "correction", "label": "Correction",
        "updated": {"date-parts": [[2010, 6, 21]]},
    }],
}]}}
RETRACTED_NOTICES = {"message": {"items": [{
    "DOI": "10.9999/notice",
    "update-to": [{
        "DOI": "10.9999/bad", "type": "retraction", "label": "Retraction",
        "source": "retraction-watch", "updated": {"date-parts": [[2021, 3]]},
    }],
}]}}


def crossref_router(work, notices, openalex):
    return Router([
        (lambda u, p: "api.crossref.org" in u and "filter" in p, notices),
        ("api.crossref.org/works/", work),
        ("api.openalex.org", openalex),
    ])


class TestCheckRetractions:
    def test_reports_correction_once(self):
        router = crossref_router(
            fake_response(WORK_WITH_CORRECTION), fake_response(NOTICES),
            fake_response({"is_retracted": False}),
        )
        with patch("server.requests.get", side_effect=router):
            out = run(check_retractions(["https://doi.org/10.1037/a0017838"]))
        result = out["results"][0]
        assert result["doi"] == "10.1037/a0017838"
        assert result["status"] == "corrected"
        assert result["openalex_is_retracted"] is False
        assert result["updated_by"] == [{
            "type": "correction", "label": "Correction", "date": "2010-06-21",
            "notice_doi": "10.1037/a0020156", "target_doi": "10.1037/a0017838",
            "source": "publisher",
        }]
        assert out["flagged"] == ["10.1037/a0017838"]
        assert router.calls[1][1]["filter"] == "updates:10.1037/a0017838"

    def test_retraction_from_notice_search_and_openalex(self):
        work = fake_response({"message": {"DOI": "10.9999/bad", "title": ["Bad"]}})
        router = crossref_router(work, fake_response(RETRACTED_NOTICES),
                                 fake_response({"is_retracted": True}))
        with patch("server.requests.get", side_effect=router):
            out = run(check_retractions(["10.9999/bad"]))
        result = out["results"][0]
        assert result["status"] == "retracted"
        assert result["updated_by"][0]["source"] == "retraction-watch"
        assert result["updated_by"][0]["date"] == "2021-03"

    def test_unknown_doi_is_not_found(self):
        with patch("server.requests.get", return_value=fake_response({}, status_code=404)):
            out = run(check_retractions(["10.0000/nope"]))
        assert out["results"][0]["status"] == "not_found"

    def test_validation_errors(self):
        assert "error" in run(check_retractions([]))
        assert "error" in run(check_retractions([f"10.1/{n}" for n in range(26)]))


OC_CITATIONS = [{
    "oci": "1-2",
    "citing": "omid:br/1 openalex:W1 doi:10.1007/s12671-022-01940-5",
    "cited": "omid:br/2 pmid:20438221 doi:10.1037/a0017838",
    "creation": "2022", "timespan": "P12Y", "journal_sc": "no", "author_sc": "yes",
}]
OC_REFERENCES = [{
    "oci": "2-3",
    "citing": "omid:br/2 doi:10.1037/a0017838",
    "cited": "omid:br/3 doi:10.1037/0022-3514.82.6.956 pmid:12051575",
    "creation": "2010", "timespan": "P8Y", "journal_sc": "yes", "author_sc": "no",
}]


class TestGetCitations:
    def test_both_directions(self):
        router = Router([
            ("citation-count", fake_response([{"count": "154"}])),
            ("reference-count", fake_response([{"count": "40"}])),
            ("/citations/", fake_response(OC_CITATIONS)),
            ("/references/", fake_response(OC_REFERENCES)),
        ])
        with patch("server.requests.get", side_effect=router):
            out = run(get_citations("doi:10.1037/a0017838", max_results=5))
        assert out["citations_count"] == 154 and out["references_count"] == 40
        citing = out["citations"][0]
        assert citing["doi"] == "10.1007/s12671-022-01940-5"
        assert citing["identifiers"]["openalex"] == "W1"
        assert citing["author_self_citation"] is True
        cited = out["references"][0]
        assert cited["doi"] == "10.1037/0022-3514.82.6.956"
        assert cited["identifiers"]["pmid"] == "12051575"
        assert all("api.opencitations.net" in url for url, _ in router.calls)
        assert {"page": 1, "page_size": 5} in [params for _, params in router.calls]

    def test_errors(self):
        assert "error" in run(get_citations("10.1/x", direction="sideways"))
        with patch("server.requests.get", return_value=fake_response({}, status_code=500)):
            out = run(get_citations("10.1/x", direction="citations"))
        assert "error" in out and out["doi"] == "10.1/x"


CT_PAYLOAD = {
    "nextPageToken": "tok2",
    "studies": [{"protocolSection": {
        "identificationModule": {"nctId": "NCT01234567", "briefTitle": "Narcissism study"},
        "statusModule": {"overallStatus": "COMPLETED", "startDateStruct": {"date": "2019-04"}},
        "designModule": {"phases": ["NA"]},
        "descriptionModule": {"briefSummary": "A trial."},
        "contactsLocationsModule": {"overallOfficials": [{"name": "Dr. Smith"}]},
        "referencesModule": {"references": [
            {"pmid": "123", "type": "RESULT", "citation": "Smith 2021"},
        ]},
    }}],
}


class TestSearchClinicalTrials:
    def test_returns_studies_and_maps_filters(self):
        router = Router([("clinicaltrials.gov", fake_response(CT_PAYLOAD))])
        with patch("server.requests.get", side_effect=router):
            out = run(search_clinical_trials(
                "narcissism", max_results=3, condition="personality disorder",
                status="completed", phase="phase 2",
            ))
        study = out["entries"][0]
        assert study["nct_id"] == "NCT01234567"
        assert study["overall_status"] == "COMPLETED"
        assert study["start_date"] == "2019-04-01"
        assert study["publications"][0]["pmid"] == "123"
        assert study["url"] == "https://clinicaltrials.gov/study/NCT01234567"
        assert out["next_cursor"]
        params = router.calls[0][1]
        assert params["pageSize"] == 3
        assert params["query.cond"] == "personality disorder"
        assert params["filter.overallStatus"] == "COMPLETED"
        assert params["query.advanced"] == "AREA[Phase]PHASE2"

    def test_http_error_returns_error_json(self):
        with patch("server.requests.get", return_value=fake_response({}, status_code=429)):
            out = run(search_clinical_trials("narcissism"))
        assert "error" in out and "429" in out["error"]
