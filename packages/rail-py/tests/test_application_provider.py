from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from krail.provider.v1 import (
    DescribeTypesRequest,
    ExplainRequest,
    FindRequest,
    GetResourceRequest,
    IntegrityRequest,
    LineageRequest,
    Provider,
    ProviderInfoRequest,
    RetrieveEvidenceRequest,
    SearchRequest,
)
from rail import cli as rail_cli
from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime
from rail.local import LocalEngine
from rail.project import Project


def _project(tmp_path: Path) -> tuple[Path, Project]:
    root = bootstrap_future_project(tmp_path, name="Provider Project", slug="provider-project")
    return root, Project(slug="provider-project", backend=LocalEngine(project_path=root))


def test_application_service_owns_capture_promotion_and_retrieval_path(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    runtime = project._backend.knowledge

    captured = runtime.capture(text="Provider seam evidence has exact source trace.", title="Provider seam")
    promoted = runtime.inbox_promote(captured["path"], topic="provider-seam")
    searched = runtime.search("exact source trace", limit=3, rag=False)
    evidence = project.provider.retrieve_evidence(
        RetrieveEvidenceRequest(query="exact source trace", max_items=3, max_total_bytes=4096)
    ).evidence
    topic_ref = next(item.source for item in evidence.items if item.source.resource_id == "topics/provider-seam.md")
    lineage = project.provider.lineage(LineageRequest(ref=topic_ref, max_depth=1, max_nodes=2))
    bounded_lineage = project.provider.lineage(LineageRequest(ref=topic_ref, max_depth=0, max_nodes=1))

    assert runtime.application is runtime.application
    assert promoted["topic"]["path"] == "topics/provider-seam.md"
    assert searched["hits"][0]["path"] == "topics/provider-seam.md"
    assert evidence.items[0].source.resource_id == "topics/provider-seam.md"
    assert evidence.items[0].source.verifies((root / "topics/provider-seam.md").read_bytes())
    assert len(evidence.items) <= 3
    assert sum(len(item.excerpt.encode("utf-8")) for item in evidence.items) <= 4096
    assert {node.resource_type for node in lineage.nodes} == {"topic", "capture"}
    assert lineage.edges[0].relation == "promoted-into"
    assert bounded_lineage.nodes == [topic_ref]
    assert bounded_lineage.truncated is True


def test_provider_exact_reads_detect_version_drift_and_integrity_reports_it(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    topic = root / "topics" / "exact.md"
    topic.write_text("# Exact\n\nOriginal bytes.\n", encoding="utf-8")
    provider = project.provider
    hit = provider.search(SearchRequest(query="Original bytes", limit=1)).hits[0]

    complete = provider.get_resource(GetResourceRequest(ref=hit.ref)).resource
    assert complete.ref.verifies(complete.content)

    topic.write_text("# Exact\n\nChanged bytes.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches"):
        provider.get_resource(GetResourceRequest(ref=hit.ref))
    status = provider.integrity(IntegrityRequest(refs=[hit.ref]))
    assert status.status == "fail"
    assert status.findings[0].code == "exact-reference-mismatch"


def test_all_bounded_provider_reads_return_public_contract_models(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    topic = root / "topics" / "operations.md"
    topic.write_text("# Operations\n\nAll provider operations use exact evidence.\n", encoding="utf-8")
    provider = project.provider
    searched = provider.search(SearchRequest(query="exact evidence", limit=2))
    ref = searched.hits[0].ref

    described = provider.describe_types(DescribeTypesRequest())
    found = provider.find(FindRequest(resource_type="topic", identifiers=[ref.resource_id, "topics/missing.md"]))
    explained = provider.explain(ExplainRequest(question="What is exact?", refs=[ref], max_evidence_items=1))
    lineage = provider.lineage(LineageRequest(ref=ref, max_depth=1, max_nodes=2))

    assert {item.resource_type for item in described.types} >= {"topic", "capture"}
    assert found.hits[0].ref == ref
    assert found.missing_identifiers == ["topics/missing.md"]
    assert explained.evidence.items[0].source == ref
    assert lineage.root == ref
    assert lineage.nodes == [ref]


def test_provider_negotiates_capabilities_and_reports_version_skew(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("rail.application._distribution_version", lambda: "1.1.12")
    _root, project = _project(tmp_path)
    provider = project.provider

    assert isinstance(provider, Provider)
    current = provider.provider_info(ProviderInfoRequest(consumer_version="1.9.0"))
    skewed = provider.provider_info(ProviderInfoRequest(consumer_version="2.0.0"))

    assert current.compatible is True
    assert set(current.capabilities) == {
        "describe_types", "search", "find", "get_resource", "retrieve_evidence", "explain", "lineage", "integrity"
    }
    assert skewed.compatible is False
    assert "version skew" in skewed.diagnostic


def test_provider_reports_installed_distribution_skew(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("rail.application._distribution_version", lambda: "0.2.1")
    _root, project = _project(tmp_path)

    result = project.provider.provider_info(ProviderInfoRequest(consumer_version="1.1.12"))

    assert result.compatible is False
    assert result.provider_version == "1.1.12"
    assert result.installed_distribution_version == "0.2.1"
    assert "installation skew" in result.diagnostic


def test_provider_cursor_is_opaque_query_bound_and_bounded(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    for index in range(3):
        (root / "topics" / f"page-{index}.md").write_text(f"# Page {index}\n\nshared paging term\n", encoding="utf-8")

    first = project.provider.search(SearchRequest(query="shared paging term", limit=1))
    assert len(first.hits) == 1
    assert first.next_cursor
    second = project.provider.search(SearchRequest(query="shared paging term", limit=1, cursor=first.next_cursor))
    assert second.hits[0].ref != first.hits[0].ref
    with pytest.raises(ValueError, match="invalid for this search"):
        project.provider.search(SearchRequest(query="different query", limit=1, cursor=first.next_cursor))
    with pytest.raises(ValueError, match="invalid for this search"):
        project.provider.search(
            SearchRequest(query="shared paging term", resource_types=["source"], limit=1, cursor=first.next_cursor)
        )


def test_python_and_cli_provider_search_are_equivalent(tmp_path: Path, capsys) -> None:
    root, project = _project(tmp_path)
    (root / "topics" / "equivalence.md").write_text("# Equivalence\n\nshared application behavior\n", encoding="utf-8")
    request = SearchRequest(query="shared application behavior", limit=2)
    expected = project.provider.search(request).model_dump(mode="json")

    rail_cli.cmd_provider(
        project,
        argparse.Namespace(provider_command="search", query=request.query, type=None, limit=2, cursor=None),
    )

    assert json.loads(capsys.readouterr().out) == expected


def test_krail_version_is_available_without_loading_a_project(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["krail", "--version"])
    rail_cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"name": "KRAIL", "version": "1.1.12"}
