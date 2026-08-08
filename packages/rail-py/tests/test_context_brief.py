from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from krail.epistemic_history import EpistemicHistory, OperationContext
from krail.provider.v1 import SearchRequest
from rail.bootstrap import bootstrap_future_project
from rail.context_brief import ContextBriefRequest, ProcessingVersion
from rail.local import LocalEngine
from rail.project import Project


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def _project(tmp_path: Path) -> tuple[Path, Project]:
    root = bootstrap_future_project(tmp_path, name="Context Project", slug="context-project")
    repository = root / "docs" / "repository.md"
    repository.parent.mkdir(exist_ok=True)
    repository.write_text("---\nfreshness: fresh\n---\n# Repository\n\nContext service repository.\n", encoding="utf-8")
    issue = root / "topics" / "issue.md"
    issue.write_text(
        "---\nfreshness: fresh\nconflicts_with:\n  - sources/ci.md\n---\n# Failing build\n\nThe release build fails on Linux.\n",
        encoding="utf-8",
    )
    source = root / "sources" / "ci.md"
    source.write_text(
        "---\nfreshness: stale\nconflicts_with:\n  - topics/issue.md\n---\n# CI result\n\nThe release build succeeds on Linux. Failing build release Linux.\n",
        encoding="utf-8",
    )
    return root, Project(slug="context-project", backend=LocalEngine(project_path=root))


def _request(project: Project) -> ContextBriefRequest:
    provider = project.provider
    repository = provider.search(SearchRequest(query="Context service repository", limit=1)).hits[0].ref
    issue = provider.search(SearchRequest(query="release build fails Linux", limit=1)).hits[0].ref
    return ContextBriefRequest(
        repository=repository,
        issue=issue,
        evaluated_at=NOW,
        query="release build Linux",
        max_items=4,
        max_total_bytes=8192,
        authorization_omission=True,
        operation_context=OperationContext(operation_id="op-7", correlation_id="corr-3", causation_id="cause-2"),
    )


def test_context_brief_is_bounded_exact_and_reproducible(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    request = _request(project)

    first = project.context_brief(request)
    second = project.context_brief(request)

    assert first == second
    assert first.brief_digest == second.brief_digest
    assert first.evidence.packet_id == second.evidence.packet_id
    assert first.ranking_trace == second.ranking_trace
    assert len(first.evidence.items) <= request.max_items
    assert sum(len(item.excerpt.encode("utf-8")) for item in first.evidence.items) <= request.max_total_bytes
    assert all(assertion.source.verifies((_root / assertion.source.resource_id).read_bytes()) for assertion in first.assertions)
    assert all(assertion.processing_versions for assertion in first.assertions)
    assert {item.status for item in first.freshness} >= {"fresh", "stale"}
    assert first.conflicts
    assert first.omissions[0].model_dump() == {
        "reason": "authorization-policy",
        "disclosure": "Additional evidence may exist but is not visible in this authorization context.",
    }
    serialized = first.model_dump_json()
    assert "hidden" not in serialized
    assert "count" not in serialized
    assert first.domain_event_ref.operation_id == "op-7"
    assert first.domain_event_ref.event_digest == first.brief_digest


def test_context_brief_rejects_drifted_exact_inputs(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    request = _request(project)
    (root / request.issue.resource_id).write_text("# Changed issue\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no longer matches"):
        project.context_brief(request)


def test_context_brief_preserves_both_exact_inputs_under_tight_byte_bound(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    request = _request(project).model_copy(update={"max_total_bytes": 2, "max_items": 2})

    brief = project.context_brief(request)

    assert [item.source for item in brief.evidence.items] == [request.repository, request.issue]
    assert sum(len(item.excerpt.encode("utf-8")) for item in brief.evidence.items) == 2
    assert brief.truncated is True


def test_context_brief_and_provider_cursor_are_bound_to_request_context(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    request = _request(project)
    baseline = project.context_brief(request)
    changed_query = project.context_brief(request.model_copy(update={"query": "different release query"}))
    changed_processing = project.context_brief(
        request.model_copy(
            update={
                "processing_versions": request.processing_versions
                + (ProcessingVersion(component="issue-profile", version="fixture.v2"),)
            }
        )
    )

    assert baseline.brief_digest != changed_query.brief_digest
    assert baseline.brief_digest != changed_processing.brief_digest
    assert baseline.ranking_trace != changed_query.ranking_trace

    first_page = project.provider.search(SearchRequest(query=request.query, limit=1))
    if first_page.next_cursor:
        with pytest.raises(ValueError, match="invalid for this search"):
            project.provider.search(SearchRequest(query="different release query", limit=1, cursor=first_page.next_cursor))


def test_context_brief_is_standalone_without_operation_context(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    request = _request(project).model_copy(update={"operation_context": None, "authorization_omission": False})

    brief = project.context_brief(request)

    assert brief.domain_event_ref.operation_id is None
    assert brief.domain_event_ref.correlation_id is None
    assert brief.domain_event_ref.causation_id is None
    assert brief.omissions == ()


def test_context_brief_history_is_explicit_and_idempotent(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    brief = project.context_brief(_request(project))
    service = project._backend.knowledge.application.context_briefs

    first = service.record(brief, retention_until=NOW + timedelta(days=30))
    second = service.record(brief, retention_until=NOW + timedelta(days=30))

    assert first == second
    assert len(EpistemicHistory(root).list()) == 1
    assert first.domain_event_ref().operation_id == "op-7"
    assert first.details is not None


def test_correction_supersession_retention_and_erasure_are_deterministic(tmp_path: Path) -> None:
    root, _project_value = _project(tmp_path)
    history = EpistemicHistory(root)
    original = history.append(
        kind="semantic-change",
        subject_digest="sha256:" + "1" * 64,
        details={"meaning": "draft"},
        created_at=NOW,
        retention_until=NOW + timedelta(days=1),
    )
    correction = history.correct(original.record_id, details={"meaning": "corrected"}, created_at=NOW + timedelta(minutes=1))
    supersession = history.supersede(correction.record_id, details={"meaning": "replacement"}, created_at=NOW + timedelta(minutes=2))

    records = history.list()
    assert [item.sequence for item in records] == [1, 2, 3]
    assert records[0].status == "corrected"
    assert records[0].replaced_by == correction.record_id
    assert records[1].status == "superseded"
    assert records[1].replaced_by == supersession.record_id
    assert correction.corrects == original.record_id
    assert supersession.supersedes == correction.record_id

    erased = history.enforce_retention(as_of=NOW + timedelta(days=2))
    assert [item.record_id for item in erased] == [original.record_id]
    tombstone = history.list()[0]
    assert tombstone.status == "erased"
    assert tombstone.details is None
    assert tombstone.detail_digest == original.detail_digest
    assert history.enforce_retention(as_of=NOW + timedelta(days=2)) == []

    explicit = history.erase(supersession.record_id, erased_at=NOW + timedelta(days=3), reason="subject-request")
    repeated = history.erase(supersession.record_id, erased_at=NOW + timedelta(days=4), reason="ignored-after-erasure")
    assert explicit == repeated
    assert explicit.domain_event_ref().event_digest == explicit.record_id
    assert explicit.details is None


def test_epistemic_history_rejects_non_json_details_and_paths_outside_project(tmp_path: Path) -> None:
    root, _project_value = _project(tmp_path)
    history = EpistemicHistory(root)

    with pytest.raises(TypeError):
        history.append(
            kind="semantic-change",
            subject_digest="sha256:" + "2" * 64,
            details={"unstable": {"unordered"}},
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="inside the KRAIL project"):
        EpistemicHistory(root, relative_path="../../outside.json")
