"""WP3a: OpenAlex null-safety and DOI citation encoding."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from server import (  # noqa: E402
    _openalex_abstract,
    _parse_openalex_work,
    get_doi_citation,
    get_openalex_work,
    search_openalex,
    search_openalex_authors,
)


def _tool(fn):
    """Return the plain function behind an @mcp.tool() wrapper, if any."""
    return getattr(fn, "fn", fn)


def _json_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


class TestOpenAlexNulls:
    def test_primary_location_source_null(self):
        work = {
            "id": "https://openalex.org/W1",
            "title": "T",
            "primary_location": {"source": None, "landing_page_url": "https://x.org/p"},
        }
        result = _parse_openalex_work(work)
        assert result["venue"] == ""
        assert result["url"] == "https://x.org/p"

    def test_best_oa_location_null(self):
        result = _parse_openalex_work({"id": "W2", "best_oa_location": None})
        assert result["pdf_url"] == ""

    def test_all_nullable_fields_null(self):
        work = {
            "id": None,
            "doi": None,
            "title": None,
            "display_name": None,
            "authorships": None,
            "concepts": None,
            "topics": None,
            "best_oa_location": {"pdf_url": None},
            "primary_location": {"source": None, "landing_page_url": None},
            "open_access": None,
            "publication_date": None,
            "type": None,
            "cited_by_count": None,
            "abstract_inverted_index": None,
        }
        result = _parse_openalex_work(work)
        assert result["openalex_id"] == ""
        assert result["title"] == ""
        assert result["venue"] == ""
        assert result["url"] == ""
        assert result["pdf_url"] == ""
        assert result["is_oa"] is False
        assert result["oa_status"] == ""
        assert result["citation_count"] == 0
        assert result["authors"] == []
        assert result["publication_date"] == ""
        assert result["type"] == ""

    def test_null_entries_inside_lists(self):
        work = {
            "id": "W3",
            "authorships": [
                None,
                {"author": None, "institutions": None},
                {
                    "author": {"display_name": "Ana"},
                    "institutions": [None, {"display_name": None}, {"display_name": "MIT"}],
                },
            ],
            "concepts": [None, {"display_name": None}, {"display_name": "Psych"}],
            "topics": [None, {"display_name": "Emotion"}],
        }
        result = _parse_openalex_work(work)
        assert result["authors"] == ["Ana"]
        assert result["institutions"] == ["MIT"]
        assert result["concepts"] == ["Psych"]
        assert result["topics"] == ["Emotion"]

    def test_unknown_title_only_with_id(self):
        assert _parse_openalex_work({})["title"] == ""
        result = _parse_openalex_work({"id": "https://openalex.org/W9"})
        assert result["title"] == "[Unknown Work W9]"

    def test_abstract_null_positions(self):
        assert _openalex_abstract({"a": [0], "b": None}) == "a"

    def test_search_openalex_with_null_source(self):
        payload = {
            "meta": {"count": 1},
            "results": [{"id": "https://openalex.org/W1", "title": "X",
                         "primary_location": {"source": None}, "best_oa_location": None,
                         "open_access": None}],
        }
        with patch.object(server.requests, "get", return_value=_json_response(payload)):
            out = json.loads(_tool(search_openalex)("anything"))
        assert out["entries"][0]["venue"] == ""

    def test_get_openalex_work_null_refs(self):
        payload = {"id": "https://openalex.org/W1", "primary_location": {"source": None},
                   "referenced_works": [None, "https://openalex.org/W2"], "related_works": None}
        with patch.object(server.requests, "get", return_value=_json_response(payload)):
            out = json.loads(_tool(get_openalex_work)("W1"))
        assert out["referenced_works"] == ["W2"]
        assert out["related_works"] == []

    def test_search_authors_nulls(self):
        payload = {"meta": {"count": 1}, "results": [
            {"id": None, "display_name": None, "orcid": None,
             "last_known_institution": None, "summary_stats": None}]}
        with patch.object(server.requests, "get", return_value=_json_response(payload)):
            out = json.loads(_tool(search_openalex_authors)("x"))
        assert out["entries"][0]["orcid"] == ""
        assert out["entries"][0]["last_known_institution"] == ""


def _raw_response(body: bytes, content_type: str):
    resp = requests.Response()
    resp.status_code = 200
    resp._content = body
    resp.headers["Content-Type"] = content_type
    # Without a charset in Content-Type, requests guesses ISO-8859-1 for text/*.
    resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)
    return resp


class TestDoiCitationEncoding:
    CITATION = "Müller, J., & Núñez, A. (2020). Emotion recognition. Journal, 12, 100–110."

    def test_text_utf8_without_charset(self):
        resp = _raw_response(self.CITATION.encode("utf-8"), "text/x-bibliography")
        assert resp.encoding == "ISO-8859-1"  # sanity: would mojibake
        with patch.object(server.requests, "get", return_value=resp):
            out = _tool(get_doi_citation)("10.1234/abc")
        assert out == self.CITATION
        assert "–" in out and "Müller" in out and "Núñez" in out

    def test_citeproc_utf8(self):
        body = json.dumps({"title": "A – B", "author": [{"family": "Núñez"}]},
                          ensure_ascii=False).encode("utf-8")
        resp = _raw_response(body, "application/vnd.citationstyles.csl+json")
        with patch.object(server.requests, "get", return_value=resp):
            out = json.loads(_tool(get_doi_citation)("10.1234/abc", format="citeproc"))
        assert out["title"] == "A – B"
        assert out["author"][0]["family"] == "Núñez"
