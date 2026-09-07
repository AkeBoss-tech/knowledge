from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from krail.provider.v1 import ResourceRef
from rail.authorized_context import (
    AuthorizedContextPacket,
    HostedAccessContextAuthorizer,
    assemble_authorized_context,
)
from rail.context_brief import ContextBriefRequest
from rail.hosted.access import (
    AccessClaims,
    AccessContextAuthority,
    MemoryRevocationRegistry,
)
from rail.bootstrap import bootstrap_future_project
from rail.local import LocalEngine
from rail.project import Project


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def _project(tmp_path: Path) -> tuple[Path, Project]:
    root = bootstrap_future_project(tmp_path, name="Authorized", slug="authorized")
    repository = root / "docs" / "repository.md"
    repository.parent.mkdir(parents=True, exist_ok=True)
    repository.write_text("# Repository\n\nVisible project context.\n", encoding="utf-8")
    issue = root / "topics" / "issue.md"
    issue.parent.mkdir(parents=True, exist_ok=True)
    issue.write_text("# Issue\n\nVisible issue context.\n", encoding="utf-8")
    hidden = root / "sources" / "private.md"
    hidden.write_text("# Private\n\nShould never be returned.\n", encoding="utf-8")
    return root, Project(slug="authorized", backend=LocalEngine(project_path=root))


def _claims(repository: ResourceRef, issue: ResourceRef) -> AccessClaims:
    return AccessClaims(
        issuer="https://control.example.test",
        tenant_id="tenant-a",
        project_id="project-a",
        subject="user/alice",
        delegator="user/alice",
        delegation_id="delegation/context",
        capability_id="krail.context-brief",
        capability_version="1.0.0",
        capability_digest=DIGEST,
        actions=("context.read",),
        source_ids=(repository.resource_id, issue.resource_id),
        classifications=("internal",),
        policy_digest=DIGEST,
        issued_at=NOW - timedelta(minutes=2),
        not_before=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
        nonce="context-nonce",
    )


def test_authorized_packet_filters_candidates_and_binds_auth_digest(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    repository = project.provider._ref("docs/repository.md")
    issue = project.provider._ref("topics/issue.md")
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"key-1": b"context-test-key"},
        issuer="https://control.example.test",
        revocations=revocations,
    )
    context = authority.issue(_claims(repository, issue), key_id="key-1")
    authorizer = HostedAccessContextAuthorizer(
        authority, context, exact_refs=(repository, issue), clock=lambda: NOW
    )
    request = ContextBriefRequest(
        repository=repository,
        issue=issue,
        evaluated_at=NOW,
        query="Visible context",
        max_items=4,
        max_total_bytes=4096,
    )

    packet = assemble_authorized_context(project._backend.knowledge.application.context_briefs, request, authorizer)

    assert packet.authorization_digest == context.context_digest
    assert packet.packet_digest.startswith("sha256:")
    assert all(item.source.exact_key in {repository.exact_key, issue.exact_key} for item in packet.context.evidence.items)
    assert "Should never be returned" not in packet.model_dump_json()


def test_authorized_packet_rechecks_revocation_before_return(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    repository = project.provider._ref("docs/repository.md")
    issue = project.provider._ref("topics/issue.md")
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"key-1": b"context-test-key"},
        issuer="https://control.example.test",
        revocations=revocations,
    )
    context = authority.issue(_claims(repository, issue), key_id="key-1")
    authorizer = HostedAccessContextAuthorizer(
        authority, context, exact_refs=(repository, issue), clock=lambda: NOW
    )
    revocations.revoke_context(context.context_digest, revoked_at=NOW)
    request = ContextBriefRequest(repository=repository, issue=issue, evaluated_at=NOW)

    with pytest.raises(PermissionError, match="context access denied"):
        assemble_authorized_context(project._backend.knowledge.application.context_briefs, request, authorizer)


def test_authorized_packet_rejects_tampered_packet_digest(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    repository = project.provider._ref("docs/repository.md")
    issue = project.provider._ref("topics/issue.md")
    authority = AccessContextAuthority(
        {"key-1": b"context-test-key"}, issuer="https://control.example.test"
    )
    context = authority.issue(_claims(repository, issue), key_id="key-1")
    authorizer = HostedAccessContextAuthorizer(authority, context, exact_refs=(repository, issue), clock=lambda: NOW)
    packet = assemble_authorized_context(
        project._backend.knowledge.application.context_briefs,
        ContextBriefRequest(repository=repository, issue=issue, evaluated_at=NOW),
        authorizer,
    )
    with pytest.raises(ValueError, match="packet digest"):
        AuthorizedContextPacket.model_validate(
            {**packet.model_dump(mode="python"), "packet_digest": "sha256:" + "0" * 64}
        )


def test_authorized_context_rejects_version_or_digest_mutation(tmp_path: Path) -> None:
    _root, project = _project(tmp_path)
    repository = project.provider._ref("docs/repository.md")
    issue = project.provider._ref("topics/issue.md")
    authority = AccessContextAuthority(
        {"key-1": b"context-test-key"}, issuer="https://control.example.test"
    )
    context = authority.issue(_claims(repository, issue), key_id="key-1")
    authorizer = HostedAccessContextAuthorizer(
        authority, context, exact_refs=(repository, issue), clock=lambda: NOW
    )
    mutated_issue = issue.model_copy(update={"version": "content:" + "0" * 64})

    with pytest.raises(PermissionError, match="context access denied"):
        authorizer.authorize(mutated_issue)
