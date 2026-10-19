"""Tests for the DSpace 8 repository tools (MIT, Harvard, Cornell, Penn)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from server import (  # noqa: E402
    DSPACE8_REPOS,
    _dspace8_build_query,
    _dspace8_get_item,
    _dspace8_search,
    _dspace8_type_clause,
    get_mit_dspace_item,
    search_harvard_dash,
    search_mit_dspace,
)

UUID = "e2cd2586-47a1-4cc1-99c8-34158769a6fb"


def _resp(status=200, payload=None, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.json.return_value = payload if payload is not None else {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status} Client Error", response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _search_payload(items, total=None):
    return {
        "_embedded": {
            "searchResult": {
                "page": {"totalElements": total if total is not None else len(items)},
                "_embedded": {
                    "objects": [
                        {"_embedded": {"indexableObject": it}} for it in items
                    ]
                },
            }
        }
    }


MIT_ITEM = {
    "uuid": UUID,
    "handle": "1721.1/55948",
    "name": "Narcissism And Leadership",
    "metadata": {
        "dc.title": [{"value": "Narcissism And Leadership: A Review And Research Agenda"}],
        "dc.contributor.author": [{"value": "Rosenthal, Seth A."}],
        "dc.type": [{"value": "Working Paper"}],
    },
}


# ── Repository registry ─────────────────────────────────────────────────

class TestRepoRegistry:
    def test_mit_uses_dspace8_api(self):
        mit = DSPACE8_REPOS["mit"]
        assert mit["api_base"] == "https://dspace.mit.edu/server/api"
        assert mit["web_base"] == "https://dspace.mit.edu"

    def test_dspace6_code_path_removed(self):
        assert not hasattr(server, "DSPACE_MIT_BASE")
        assert not hasattr(server, "_dspace_search_field")
        assert not hasattr(server, "_dspace_parse_item")


# ── item_type / search_field mapping ────────────────────────────────────

class TestTypeClause:
    def test_scholarly_default_includes_articles_and_theses(self):
        clause = _dspace8_type_clause("scholarly")
        assert clause.startswith("dc.type:(")
        assert "article" in clause
        assert "thesis" in clause
        assert "dissertation" in clause
        assert '"working paper"' in clause
        assert "other" not in clause.lower()

    def test_any_disables_filter(self):
        assert _dspace8_type_clause("any") == ""
        assert _dspace8_type_clause("ANY") == ""
        assert _dspace8_type_clause("") == ""
        assert _dspace8_type_clause(None) == ""

    def test_presets(self):
        assert _dspace8_type_clause("article") == "dc.type:(article)"
        assert _dspace8_type_clause("Thesis") == "dc.type:(thesis OR dissertation)"
        assert _dspace8_type_clause("dataset") == "dc.type:(dataset)"

    def test_literal_type_phrase(self):
        assert _dspace8_type_clause("Technical Report") == 'dc.type:("Technical Report")'

    def test_literal_strips_quotes(self):
        assert _dspace8_type_clause('Bad"Type') == 'dc.type:("Bad Type")'


class TestBuildQuery:
    def test_default_scholarly(self):
        q = _dspace8_build_query("romance scam")
        assert q.startswith("(romance scam) AND dc.type:(")

    def test_any_is_plain_query(self):
        assert _dspace8_build_query("romance scam", item_type="any") == "romance scam"

    def test_search_fields(self):
        assert _dspace8_build_query("narcissism", "title", "any") == "dc.title:(narcissism)"
        assert (_dspace8_build_query("narcissism", "abstract", "any")
                == "dc.description.abstract:(narcissism)")
        assert (_dspace8_build_query("Rosenthal", "author", "any")
                == "dc.contributor.author:(Rosenthal)")
        assert _dspace8_build_query("ethics", "subject", "any") == "dc.subject:(ethics)"

    def test_field_and_type_combined(self):
        q = _dspace8_build_query("narcissism", "title", "article")
        assert q == "(dc.title:(narcissism)) AND dc.type:(article)"

    def test_empty_query_type_only(self):
        assert _dspace8_build_query("", item_type="article") == "dc.type:(article)"
        assert _dspace8_build_query("  ", item_type="any") == ""

    def test_invalid_search_field(self):
        with pytest.raises(ValueError):
            _dspace8_build_query("x", "fulltext")


# ── _dspace8_search ─────────────────────────────────────────────────────

class TestDspace8Search:
    def test_sends_item_type_in_query_param(self):
        payload = _search_payload([MIT_ITEM])
        with patch("server.requests.get", return_value=_resp(200, payload)) as g:
            result = _dspace8_search("harvard", "romance scam", 5)
        url = g.call_args.args[0]
        params = g.call_args.kwargs["params"]
        assert url == "https://dash.harvard.edu/server/api/discover/search/objects"
        assert params["dsoType"] == "ITEM"
        assert params["size"] == 5
        assert params["query"].startswith("(romance scam) AND dc.type:(")
        assert result["item_type"] == "scholarly"
        assert result["results_count"] == 1

    def test_any_sends_raw_query(self):
        with patch("server.requests.get", return_value=_resp(200, _search_payload([]))) as g:
            _dspace8_search("penn", "ai ethics", 3, item_type="any")
        assert g.call_args.kwargs["params"]["query"] == "ai ethics"

    def test_http_error_returns_json_error(self):
        resp = _resp(429, headers={"Retry-After": "900"})
        with patch("server.requests.get", return_value=resp):
            result = _dspace8_search("cornell", "x", 3)
        assert result["status_code"] == 429
        assert "error" in result
        assert result["retry_after"] == "900"
        assert result["source"] == "Cornell eCommons"

    def test_network_error_returns_json_error(self):
        with patch("server.requests.get", side_effect=requests.ConnectionError("boom")):
            result = _dspace8_search("mit", "x", 3)
        assert result["status_code"] is None
        assert "boom" in result["error"]

    def test_invalid_search_field_no_request(self):
        with patch("server.requests.get") as g:
            result = _dspace8_search("mit", "x", 3, search_field="bogus")
        assert "error" in result
        g.assert_not_called()


# ── MIT tools ───────────────────────────────────────────────────────────

class TestMitTools:
    def test_search_mit_dspace_routes_to_dspace8(self):
        payload = _search_payload([MIT_ITEM], 95)
        with patch("server.requests.get", return_value=_resp(200, payload)) as g:
            out = json.loads(search_mit_dspace("narcissism", max_results=3, search_field="title"))
        assert g.call_args.args[0] == "https://dspace.mit.edu/server/api/discover/search/objects"
        assert g.call_args.kwargs["params"]["query"].startswith("(dc.title:(narcissism)) AND ")
        assert out["source"] == "DSpace@MIT"
        assert out["total_results"] == 95
        entry = out["entries"][0]
        assert entry["uuid"] == UUID
        assert entry["authors"] == ["Rosenthal, Seth A."]
        assert entry["handle_url"] == "https://dspace.mit.edu/handle/1721.1/55948"

    def test_search_mit_clamps_max_results(self):
        with patch("server.requests.get", return_value=_resp(200, _search_payload([]))) as g:
            search_mit_dspace("x", max_results=500)
        assert g.call_args.kwargs["params"]["size"] == 100

    def test_get_mit_dspace_item(self):
        responses = [
            _resp(200, MIT_ITEM),
            _resp(200, {"_embedded": {"bundles": [{"name": "ORIGINAL", "uuid": "b1"}]}}),
            _resp(200, {"_embedded": {"bitstreams": [
                {"name": "paper.pdf", "sizeBytes": 10, "uuid": "f1",
                 "metadata": {"dc.format.mimetype": [{"value": "application/pdf"}]}},
            ]}}),
        ]
        with patch("server.requests.get", side_effect=responses) as g:
            out = json.loads(get_mit_dspace_item(UUID))
        assert g.call_args_list[0].args[0] == f"https://dspace.mit.edu/server/api/core/items/{UUID}"
        assert out["title"].startswith("Narcissism And Leadership")
        assert out["bitstreams"][0]["retrieve_link"] == (
            "https://dspace.mit.edu/server/api/core/bitstreams/f1/content"
        )


class TestDspace8Bitstreams:
    """DSpace 8 keeps a file's MIME type on its format record, not in metadata."""

    def _get(self, bitstream):
        responses = [
            _resp(200, MIT_ITEM),
            _resp(200, {"_embedded": {"bundles": [{"name": "ORIGINAL", "uuid": "b1"}]}}),
            _resp(200, {"_embedded": {"bitstreams": [bitstream]}}),
        ]
        with patch("server.requests.get", side_effect=responses) as g:
            result = _dspace8_get_item("harvard", UUID)
        return result, g

    def test_mime_type_from_embedded_format(self):
        result, g = self._get({
            "name": "thesis.pdf", "sizeBytes": 5, "uuid": "f1", "metadata": {},
            "_embedded": {"format": {"mimetype": "application/pdf",
                                     "shortDescription": "Adobe PDF"}},
        })
        assert result["bitstreams"][0]["mime_type"] == "application/pdf"
        assert result["bitstreams"][0]["format"] == "Adobe PDF"
        assert g.call_args_list[2].kwargs["params"] == {"embed": "format"}

    def test_mime_type_falls_back_to_metadata(self):
        result, _ = self._get({
            "name": "old.pdf", "sizeBytes": 5, "uuid": "f2",
            "metadata": {"dc.format.mimetype": [{"value": "application/pdf"}]},
        })
        assert result["bitstreams"][0]["mime_type"] == "application/pdf"
        assert result["bitstreams"][0]["format"] == ""


# ── _dspace8_get_item error handling ────────────────────────────────────

class TestDspace8GetItemErrors:
    @pytest.mark.parametrize("status", [401, 404])
    def test_http_error_returns_json(self, status):
        with patch("server.requests.get", return_value=_resp(status)):
            result = _dspace8_get_item("harvard", UUID)
        assert result["status_code"] == status
        assert result["uuid"] == UUID
        assert result["source"] == "Harvard DASH"
        assert f"HTTP {status}" in result["error"]
        assert "hint" in result

    def test_tool_wrapper_returns_json_string_on_401(self):
        with patch("server.requests.get", return_value=_resp(401)):
            out = json.loads(get_mit_dspace_item(UUID))
        assert out["status_code"] == 401
        assert out["uuid"] == UUID

    def test_request_exception_returns_json(self):
        with patch("server.requests.get", side_effect=requests.Timeout("timed out")):
            result = _dspace8_get_item("penn", UUID)
        assert result["status_code"] is None
        assert result["uuid"] == UUID
        assert "timed out" in result["error"]

    def test_invalid_uuid_rejected_without_request(self):
        with patch("server.requests.get") as g:
            result = _dspace8_get_item("mit", "../../discover/search")
        assert result["status_code"] is None
        assert "Invalid item UUID" in result["error"]
        g.assert_not_called()

    def test_bitstream_failure_still_returns_item(self):
        responses = [_resp(200, MIT_ITEM), _resp(500)]
        with patch("server.requests.get", side_effect=responses):
            result = _dspace8_get_item("mit", UUID)
        assert result["uuid"] == UUID
        assert result["bitstreams"] == []


def test_harvard_tool_passes_item_type():
    with patch("server.requests.get", return_value=_resp(200, _search_payload([]))) as g:
        search_harvard_dash("romance scam", item_type="any")
    assert g.call_args.kwargs["params"]["query"] == "romance scam"
