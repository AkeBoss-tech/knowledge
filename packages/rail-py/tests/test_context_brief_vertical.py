from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from krail.provider.v1 import FindRequest, SearchRequest
from rail import cli as rail_cli
from rail.bootstrap import bootstrap_future_project
from rail.context_brief import ContextBriefRequest, ProcessingVersion
from rail.local import LocalEngine
from rail.project import Project


FIXTURE = Path(__file__).parent / "fixtures" / "context_brief_vertical"
EVALUATED_AT = datetime(2026, 8, 7, 15, 30, tzinfo=UTC)


def _project(tmp_path: Path) -> tuple[Path, Project]:
    root = bootstrap_future_project(tmp_path, name="Northstar", slug="northstar")
    for directory in ("docs", "topics", "sources"):
        shutil.copytree(FIXTURE / directory, root / directory, dirs_exist_ok=True)
    return root, Project(slug="northstar", backend=LocalEngine(project_path=root))


def _adapter_request(project: Project) -> ContextBriefRequest:
    # GitHub-specific input is translated only at this fixture/adapter boundary.
    github = json.loads((FIXTURE / "adapter" / "github_issue.json").read_text(encoding="utf-8"))
    repository = project.provider.find(
        FindRequest(resource_type="document", identifiers=[github["repository_resource_id"]])
    ).hits[0].ref
    issue = project.provider.find(
        FindRequest(resource_type="topic", identifiers=[github["issue_resource_id"]])
    ).hits[0].ref
    return ContextBriefRequest(
        repository=repository,
        issue=issue,
        evaluated_at=EVALUATED_AT,
        query="Northstar linux-arm64 cache restore older prior exit code release",
        max_items=6,
        max_total_bytes=4096,
        authorization_omission=True,
    )


def test_realistic_vertical_replays_exact_digest_trace_conflicts_staleness_and_gaps(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    request = _adapter_request(project)

    first = project.provider.context_brief(request)
    second = project.context_brief(request)

    assert first == second
    assert first.brief_digest == second.brief_digest
    assert first.evidence.packet_id == second.evidence.packet_id
    assert first.evidence.model_dump_json() == second.evidence.model_dump_json()
    assert hashlib.sha256(first.evidence.model_dump_json().encode()).digest() == hashlib.sha256(
        second.evidence.model_dump_json().encode()
    ).digest()
    assert first.ranking_trace == second.ranking_trace
    assert first.conflicts
    assert "stale" in {item.status for item in first.freshness}
    assert "conflict-source-missing" in {item.code for item in first.gaps}
    assert all(item.source.verifies((root / item.source.resource_id).read_bytes()) for item in first.assertions)
    assert all(item.processing_versions == request.processing_versions for item in first.assertions)
    assert all(item.source.version.startswith("content:") for item in first.assertions)
    assert all(item.source.digest.startswith("sha256:") for item in first.assertions)


def test_source_and_processing_version_drift_are_explicit(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    request = _adapter_request(project)
    baseline = project.context_brief(request)
    changed_processing = project.context_brief(
        request.model_copy(
            update={
                "processing_versions": request.processing_versions
                + (ProcessingVersion(component="fixture-adapter", version="2.0.0"),)
            }
        )
    )

    assert changed_processing.brief_digest != baseline.brief_digest
    (root / request.issue.resource_id).write_text("# Drifted issue\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches"):
        project.context_brief(request)


def test_authorization_omission_is_constant_and_does_not_leak_identity_count_or_error(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    request = _adapter_request(project)
    visible = project.context_brief(request)
    serialized = visible.model_dump_json()

    assert visible.omissions[0].model_dump() == {
        "reason": "authorization-policy",
        "disclosure": "Additional evidence may exist but is not visible in this authorization context.",
    }
    assert "private-unrelated" not in serialized
    assert "Quasar" not in serialized
    assert "count" not in json.dumps(visible.omissions[0].model_dump())

    drifted = request.issue.model_copy(update={"digest": "sha256:" + "0" * 64})
    messages = []
    for omission in (False, True):
        with pytest.raises(ValueError) as error:
            project.context_brief(request.model_copy(update={"issue": drifted, "authorization_omission": omission}))
        messages.append(str(error.value))
    assert messages[0] == messages[1]


def test_pagination_binds_all_result_shaping_filters_and_payload_bounds(tmp_path: Path) -> None:
    root, project = _project(tmp_path)
    for index in range(3):
        (root / "sources" / f"page-{index}.md").write_text(
            f"# Page {index}\n\nNorthstar cursor-shape token.\n", encoding="utf-8"
        )
    first = project.provider.search(
        SearchRequest(query="Northstar cursor-shape token", resource_types=["source"], limit=1)
    )

    assert first.next_cursor
    with pytest.raises(ValueError, match="invalid for this search"):
        project.provider.search(
            SearchRequest(query="Northstar cursor-shape token", resource_types=["topic"], limit=1, cursor=first.next_cursor)
        )
    reordered = project.provider.search(
        SearchRequest(
            query="Northstar cursor-shape token",
            resource_types=["document", "source"],
            limit=1,
        )
    )
    if reordered.next_cursor:
        project.provider.search(
            SearchRequest(
                query="Northstar cursor-shape token",
                resource_types=["source", "document"],
                limit=1,
                cursor=reordered.next_cursor,
            )
        )

    bounded = project.context_brief(_adapter_request(project).model_copy(update={"max_items": 2, "max_total_bytes": 8}))
    assert len(bounded.evidence.items) == 2
    assert sum(len(item.excerpt.encode("utf-8")) for item in bounded.evidence.items) <= 8
    assert bounded.truncated is True


def test_python_provider_and_cli_context_brief_are_equivalent(tmp_path: Path, capsys) -> None:
    _root, project = _project(tmp_path)
    request = _adapter_request(project)
    expected = project.provider.context_brief(request).model_dump(mode="json")

    rail_cli.cmd_provider(
        project,
        argparse.Namespace(provider_command="context-brief", request=request.model_dump_json()),
    )

    assert json.loads(capsys.readouterr().out) == expected
