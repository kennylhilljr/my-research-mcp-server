"""Tests for evidence workflows, bounded content, and semantic caching."""

from __future__ import annotations

from datetime import datetime, timezone

from research_mcp.cache import SemanticCache
from research_mcp.models import EvidenceItem, Provenance, WorkSummary
from research_mcp.workflows import build_evidence_matrix, compare_records


def work(source: str, source_id: str, **changes) -> WorkSummary:
    values = {
        "document_id": f"{source}:{source_id}",
        "title": "Example",
        "authors": ["A. Author"],
        "published_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "identifiers": {"doi": "10.1000/example"},
        "provenance": Provenance(source=source, source_record_id=source_id),
    }
    values.update(changes)
    return WorkSummary(**values)


def test_compare_records_reports_disagreement_instead_of_picking_winner():
    records = [
        work("crossref", "1", title="Original title"),
        work("openalex", "W1", title="Changed title"),
    ]

    comparison = compare_records(records)

    assert comparison.canonical_identifier == "10.1000/example"
    assert comparison.disagreements[0].field == "title"
    assert comparison.disagreements[0].values == {
        "crossref": "Original title",
        "openalex": "Changed title",
    }


def test_evidence_matrix_keeps_page_and_passage_provenance():
    evidence = [
        EvidenceItem(
            document_id="arxiv:1",
            claim="The model improved accuracy.",
            passage="Accuracy improved by 4%.",
            page_start=7,
            page_end=7,
            source_url="https://arxiv.org/abs/1",
        )
    ]

    matrix = build_evidence_matrix(evidence, columns=["claim", "passage", "location"])

    assert matrix.rows[0]["location"] == "arxiv:1#pages=7-7"
    assert matrix.provenance_complete is True


def test_semantic_cache_invalidates_on_generation_or_model_change():
    cache = SemanticCache(max_entries=4)
    cache.put("query", {"source": "all"}, "model-a", 1, ["result"])

    assert cache.get("query", {"source": "all"}, "model-a", 1) == ["result"]
    assert cache.get("query", {"source": "all"}, "model-a", 2) is None
    assert cache.get("query", {"source": "all"}, "model-b", 1) is None


def test_semantic_cache_is_bounded_lru():
    cache = SemanticCache(max_entries=2)
    cache.put("one", {}, "m", 1, [1])
    cache.put("two", {}, "m", 1, [2])
    assert cache.get("one", {}, "m", 1) == [1]
    cache.put("three", {}, "m", 1, [3])

    assert cache.get("two", {}, "m", 1) is None
    assert cache.get("one", {}, "m", 1) == [1]
    assert cache.get("three", {}, "m", 1) == [3]
