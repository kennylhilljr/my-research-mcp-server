"""Tests for the OSF and PsyArXiv search tools (SHARE index)."""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import (  # noqa: E402
    PSYARXIV_PUBLISHER,
    _parse_share_card,
    _share_year_filter,
    search_osf,
    search_psyarxiv,
)

CARD_META = {
    "@id": "https://osf.io/cjrpq",
    "resourceType": [{"@id": "Preprint"}],
    "publisher": [{"name": [{"@value": "PsyArXiv"}]}],
    "title": [{"@value": "A Qualitative Analysis of Gaslighting in Romantic Relationships"}],
    "creator": [
        {"name": [{"@value": "Willis Klein"}]},
        {"name": [{"@value": "Suzanne Wood"}]},
        {"identifier": [{"@value": "https://osf.io/nameless"}]},
    ],
    "dateCreated": [{"@value": "2022-06-04"}],
    "description": [{"@value": "Gaslighting is an understudied form of abuse."}],
    "keyword": [{"@value": "Gaslighting"}, {"@value": "intimate partner violence"}],
    "identifier": [
        {"@value": "https://doi.org/10.31234/osf.io/cjrpq"},
        {"@value": "https://osf.io/cjrpq"},
    ],
    "rights": [{"name": [{"@value": "CC-By Attribution 4.0 International"}]}],
}


def share_response(metas):
    """Build a SHARE index-card-search payload: ordered result refs plus included records."""
    included, refs = [], []
    for n, meta in enumerate(metas):
        included.append({"id": f"card-{n}", "type": "index-card",
                         "attributes": {"resourceMetadata": meta}})
        included.append({"id": f"res-{n}", "type": "search-result",
                         "relationships": {"indexCard": {"data": {"id": f"card-{n}"}}}})
        refs.append({"id": f"res-{n}", "type": "search-result"})
    return {
        "data": {
            "attributes": {"totalResultCount": len(metas)},
            "relationships": {"searchResultPage": {"data": refs}},
        },
        "included": included,
    }


def mock_get(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


class TestParseShareCard:
    def test_full_record(self):
        entry = _parse_share_card(CARD_META)
        assert entry["id"] == "cjrpq"
        assert entry["title"].startswith("A Qualitative Analysis")
        assert entry["authors"] == ["Willis Klein", "Suzanne Wood"]
        assert entry["date_created"] == "2022-06-04"
        assert entry["doi"] == "10.31234/osf.io/cjrpq"
        assert entry["download_url"] == "https://osf.io/cjrpq/download"
        assert entry["keywords"] == ["Gaslighting", "intimate partner violence"]
        assert entry["license"].startswith("CC-By")
        assert entry["resource_type"] == "Preprint"
        assert entry["publisher"] == "PsyArXiv"

    def test_registration_has_no_download_url(self):
        meta = {"@id": "https://osf.io/zxqyd", "resourceType": [{"@id": "Registration"}]}
        entry = _parse_share_card(meta)
        assert entry["resource_type"] == "Registration"
        assert entry["download_url"] == ""

    def test_empty_record(self):
        entry = _parse_share_card({})
        assert entry["id"] == ""
        assert entry["title"] == ""
        assert entry["authors"] == []
        assert entry["doi"] == ""
        assert entry["download_url"] == ""

    def test_doi_from_same_as(self):
        meta = {"@id": "https://osf.io/abcde",
                "identifier": [{"@value": "https://osf.io/abcde"}],
                "sameAs": [{"@id": "https://doi.org/10.31234/osf.io/abcde"}]}
        assert _parse_share_card(meta)["doi"] == "10.31234/osf.io/abcde"


class TestSearchPsyarxiv:
    @patch("server.requests.get")
    def test_results_keep_search_order(self, get):
        second = dict(CARD_META, **{"@id": "https://osf.io/zzzzz",
                                    "title": [{"@value": "Second"}]})
        get.return_value = mock_get(share_response([CARD_META, second]))
        out = json.loads(search_psyarxiv("gaslighting", max_results=5))
        assert out["results_count"] == 2
        assert [e["id"] for e in out["entries"]] == ["cjrpq", "zzzzz"]
        params = get.call_args.kwargs["params"]
        assert params["cardSearchFilter[publisher]"] == PSYARXIV_PUBLISHER
        assert params["page[size]"] == 5
        assert "sort" not in params

    @patch("server.requests.get")
    def test_newest_sort_and_clamp(self, get):
        get.return_value = mock_get(share_response([]))
        out = json.loads(search_psyarxiv("narcissism", max_results=500, sort="newest"))
        params = get.call_args.kwargs["params"]
        assert params["sort"] == "-dateCreated"
        assert params["page[size]"] == 100
        assert out["entries"] == []

    @patch("server.requests.get")
    def test_bad_sort_makes_no_request(self, get):
        out = json.loads(search_psyarxiv("x", sort="oldest"))
        assert "error" in out
        get.assert_not_called()


AFTER = "cardSearchFilter[dateCreated][after]"
BEFORE = "cardSearchFilter[dateCreated][before]"


class TestYearFilter:
    def test_no_years_adds_no_params(self):
        assert _share_year_filter() == ({}, None)

    def test_inclusive_range_maps_to_exclusive_operators(self):
        params, error = _share_year_filter(2020, 2024)
        assert error is None
        assert params == {AFTER: "2019", BEFORE: "2025"}

    def test_single_year(self):
        params, error = _share_year_filter(2024, 2024)
        assert error is None
        assert params == {AFTER: "2023", BEFORE: "2025"}

    @pytest.mark.parametrize("year_from,year_to,fragment", [
        (2025, 2020, "less than or equal"),
        (1800, None, "year_from must be between"),
        (None, 99999, "year_to must be between"),
        ("2024", None, "year_from must be an integer"),
        (True, None, "year_from must be an integer"),
    ])
    def test_invalid(self, year_from, year_to, fragment):
        params, error = _share_year_filter(year_from, year_to)
        assert params == {}
        assert fragment in error

    def test_next_year_allowed(self):
        next_year = datetime.now(timezone.utc).year + 1
        assert _share_year_filter(None, next_year)[1] is None


class TestSearchPsyarxivYears:
    @patch("server.requests.get")
    def test_year_from_only(self, get):
        get.return_value = mock_get(share_response([]))
        search_psyarxiv("narcissism", year_from=2024)
        params = get.call_args.kwargs["params"]
        assert params[AFTER] == "2023"
        assert BEFORE not in params
        assert params["cardSearchFilter[publisher]"] == PSYARXIV_PUBLISHER

    @patch("server.requests.get")
    def test_year_to_only(self, get):
        get.return_value = mock_get(share_response([]))
        search_psyarxiv("narcissism", year_to=2019)
        params = get.call_args.kwargs["params"]
        assert params[BEFORE] == "2020"
        assert AFTER not in params

    @patch("server.requests.get")
    def test_no_years_no_date_params(self, get):
        get.return_value = mock_get(share_response([]))
        search_psyarxiv("narcissism")
        params = get.call_args.kwargs["params"]
        assert AFTER not in params and BEFORE not in params

    @patch("server.requests.get")
    def test_reversed_range_makes_no_request(self, get):
        out = json.loads(search_psyarxiv("x", year_from=2025, year_to=2020))
        assert set(out) == {"error"}
        get.assert_not_called()


class TestSearchOsf:
    @patch("server.requests.get")
    def test_year_range(self, get):
        get.return_value = mock_get(share_response([]))
        search_osf("romance scam", resource_type="registration",
                   year_from=2020, year_to=2022)
        params = get.call_args.kwargs["params"]
        assert params[AFTER] == "2019"
        assert params[BEFORE] == "2023"
        assert params["cardSearchFilter[resourceType]"] == "Registration"

    @patch("server.requests.get")
    def test_out_of_range_year_makes_no_request(self, get):
        out = json.loads(search_osf("x", year_from=1066))
        assert set(out) == {"error"}
        get.assert_not_called()

    @patch("server.requests.get")
    def test_registration_filter_without_provider(self, get):
        get.return_value = mock_get(share_response([]))
        json.loads(search_osf("romance scam victims", resource_type="registration"))
        params = get.call_args.kwargs["params"]
        assert params["cardSearchFilter[resourceType]"] == "Registration"
        assert "cardSearchFilter[publisher]" not in params

    @patch("server.requests.get")
    def test_provider_maps_to_publisher(self, get):
        get.return_value = mock_get(share_response([]))
        search_osf("narcissism", provider="PsyArXiv")
        params = get.call_args.kwargs["params"]
        assert params["cardSearchFilter[publisher]"] == PSYARXIV_PUBLISHER
        assert params["cardSearchFilter[resourceType]"] == "Preprint"

    @patch("server.requests.get")
    def test_any_type_excludes_files(self, get):
        get.return_value = mock_get(share_response([CARD_META]))
        out = json.loads(search_osf("gaslighting", resource_type="any"))
        types = get.call_args.kwargs["params"]["cardSearchFilter[resourceType]"]
        assert types == "Preprint,Registration,Project"
        assert out["entries"][0]["id"] == "cjrpq"

    @patch("server.requests.get")
    def test_bad_resource_type_makes_no_request(self, get):
        out = json.loads(search_osf("x", resource_type="file"))
        assert "error" in out
        get.assert_not_called()
