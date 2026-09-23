"""Contract tests for the modern MCP application surface."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from research_mcp.config import Settings
from research_mcp.errors import ErrorCode, ResearchError
from research_mcp.federation import deduplicate_works
from research_mcp.jobs import JobService, JobState
from research_mcp.models import Provenance, WorkSummary
from research_mcp.prompts import PROMPT_NAMES
from research_mcp.server import build_mcp


def run(coro):
    return asyncio.run(coro)


def make_work(*, source: str, source_id: str, doi: str = "", title: str = "A study"):
    identifiers = {"doi": doi} if doi else {}
    return WorkSummary(
        document_id=f"{source}:{source_id}",
        title=title,
        authors=["A. Researcher"],
        identifiers=identifiers,
        provenance=Provenance(source=source, source_record_id=source_id),
    )


def test_remote_settings_force_dangerous_features_off(tmp_path: Path):
    settings = Settings(
        data_root=tmp_path / "data",
        dataset_root=tmp_path / "datasets",
        remote_mode=True,
        enable_expert_sql=True,
        allow_custom_paths=True,
    )

    assert settings.enable_expert_sql is False
    assert settings.allow_custom_paths is False
    assert settings.host == "127.0.0.1"


def test_settings_reject_data_root_as_filesystem_root():
    with pytest.raises(ValueError, match="data_root"):
        Settings(data_root=Path("/"), dataset_root=Path("/tmp/research-datasets"))


def test_research_error_has_stable_public_shape():
    error = ResearchError(
        ErrorCode.INVALID_ARGUMENT,
        "page_size must be between 1 and 50",
        field="page_size",
    )

    payload = error.to_dict()
    assert payload["code"] == "INVALID_ARGUMENT"
    assert payload["retryable"] is False
    assert payload["field"] == "page_size"
    assert payload["correlation_id"]


def test_deduplicate_works_prefers_doi_and_preserves_provenance():
    first = make_work(source="crossref", source_id="1", doi="10.1/example")
    second = make_work(source="openalex", source_id="W1", doi="https://doi.org/10.1/EXAMPLE")

    result = deduplicate_works([first, second])

    assert len(result) == 1
    assert {p.source for p in result[0].provenance_records} == {"crossref", "openalex"}


def test_deduplicate_works_keeps_ambiguous_title_matches_separate():
    first = make_work(source="crossref", source_id="1", title="Shared title")
    second = make_work(source="openalex", source_id="2", title="Shared title")

    assert len(deduplicate_works([first, second])) == 2


def test_job_service_reports_progress_and_cancellation():
    jobs = JobService()
    job = jobs.create("index", total=10)
    jobs.update(job.job_id, completed=3, message="indexed three")
    jobs.cancel(job.job_id)

    current = jobs.get(job.job_id)
    assert current.state is JobState.CANCELLED
    assert current.completed == 3
    assert current.progress == pytest.approx(0.3)


def test_job_service_rejects_progress_regression():
    jobs = JobService()
    job = jobs.create("embed", total=10)
    jobs.update(job.job_id, completed=5)

    with pytest.raises(ResearchError) as exc:
        jobs.update(job.job_id, completed=4)
    assert exc.value.code is ErrorCode.CONFLICT


def test_mcp_discovery_exposes_typed_tools_resources_and_prompts(tmp_path: Path):
    mcp = build_mcp(
        Settings(data_root=tmp_path / "data", dataset_root=tmp_path / "datasets")
    )

    tools = run(mcp.list_tools())
    tool_by_name = {tool.name: tool for tool in tools}
    assert {
        "search_works",
        "compare_work_records",
        "create_research_job",
        "get_research_job",
        "cancel_research_job",
    } <= set(tool_by_name)
    assert tool_by_name["search_works"].outputSchema["type"] == "object"
    assert tool_by_name["search_works"].outputSchema["properties"]["items"]["type"] == "array"

    templates = run(mcp.list_resource_templates())
    template_uris = {str(template.uriTemplate) for template in templates}
    assert "research://documents/{document_id}" in template_uris
    assert "research://documents/{document_id}/pages/{start}/{end}" in template_uris
    assert "research://documents/{document_id}/provenance" in template_uris
    assert "research://collections/{collection_id}" in template_uris
    assert "research://jobs/{job_id}" in template_uris

    prompts = run(mcp.list_prompts())
    assert {prompt.name for prompt in prompts} == set(PROMPT_NAMES)


def test_all_modern_tools_have_structured_output_schema(tmp_path: Path):
    mcp = build_mcp(
        Settings(data_root=tmp_path / "data", dataset_root=tmp_path / "datasets")
    )

    for tool in run(mcp.list_tools()):
        assert tool.outputSchema is not None, tool.name
        assert tool.outputSchema.get("type") == "object", tool.name
        assert not (
            set(tool.outputSchema.get("properties", {})) == {"result"}
            and tool.outputSchema["properties"]["result"].get("type") == "string"
        ), tool.name

