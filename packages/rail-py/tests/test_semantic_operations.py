from __future__ import annotations

import hashlib
import json
import argparse
import time
from types import SimpleNamespace
from pathlib import Path
from datetime import UTC, datetime, timedelta

import pytest
from krail.provider.semantic import (
    AssembleCrossSourceEvidenceRequest,
    CompareObservationsRequest,
    ExplainConflictRequest,
    GetEntityRequest,
    ListOntologyPackagesRequest,
    ListOntologyProposalHistoryRequest,
    OperationBudget,
    ResolveEntityRequest,
    SemanticReadScope,
    TraverseRelationshipsRequest,
    semantic_transport_size,
)
from krail.provider.v1 import ResourceRef
from rail.capability_publication import semantic_operations_descriptor
from rail.application import local_semantic_actor, local_semantic_policy_digest
from rail import cli as rail_cli
from rail.knowledge import KnowledgeRuntime
from rail.semantic import (
    Alias,
    Conflict,
    Entity,
    Fact,
    MemorySemanticStore,
    ObservedStructure,
    OntologyInductionService,
    PostgresSemanticStore,
    SemanticOperationsService,
    SemanticRepository,
    SemanticPack,
    SemanticType,
)
from rail.semantic.models import (
    DraftAuthorship,
    EvidenceProvenance,
    FactObject,
    OntologyChangeOperation,
    OntologyChangeSet,
    canonical_digest,
)
from rail.semantic.packs import PackSignature, PackSignatureVerification
from rail.semantic.operations import (
    AuthorizedSemanticScope,
    authorize_semantic_scope_from_claims,
    semantic_scope_authorizer_from_context,
)
from rail.hosted.access import (
    AccessClaims,
    AccessContextAuthority,
    MemoryRevocationRegistry,
)
from rail.semantic.repository import SemanticRow


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
AUTHORITY = "https://github.example.test"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def provenance(resource_id: str, *, authority: str = AUTHORITY) -> EvidenceProvenance:
    return EvidenceProvenance(
        evidence=(
            ResourceRef(
                authority=authority,
                resource_type="issue",
                resource_id=resource_id,
                version="etag:v1",
                digest=DIGEST_A,
            ),
        ),
        processing_version="semantic-kernel/1.0.0",
        processing_digest=DIGEST_B,
        observed_at=NOW,
    )


VISIBLE_SOURCE_IDS = (
    *(
        "type/" + item
        for item in (
            "software.repository",
            "software.issue",
            "software.change",
            "software.pull-request",
            "software.ci-check",
            "software.contains",
            "software.produces",
            "software.verified-by",
            "software.status",
        )
    ),
    "repo",
    "issue",
    "change",
    "pr",
    "ci",
    "alias/42",
    "alias/repo-42",
    "fact/repo-issue",
    "fact/issue-change",
    "fact/change-pr",
    "fact/pr-ci",
    "fact/ci-pass",
    "fact/ci-fail",
    "conflict/ci-status",
    "fact/public-to-hidden",
    "issue/43",
    "merged-visible",
    "alias/hidden-visible",
    "pack-source",
    "proposal-source",
)


def scope(
    *, authorities=(AUTHORITY,), policy_digest=DIGEST_A, source_ids=VISIBLE_SOURCE_IDS
) -> SemanticReadScope:
    body = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "subject_id": "user/alice",
        "allowed_authorities": list(authorities),
        "allowed_resource_types": [],
        "allowed_classifications": ["internal"],
        "allowed_sources": [
            {
                "contract": "krail.semantic-operations.v1",
                "source": ResourceRef(
                    authority=AUTHORITY,
                    resource_type="issue",
                    resource_id=item,
                    version="etag:v1",
                    digest=DIGEST_A,
                ).model_dump(mode="json"),
                "source_id": item,
                "classification": "internal",
            }
            for item in source_ids
        ],
        "policy_digest": policy_digest,
    }
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return SemanticReadScope(**body, scope_digest=digest)


def seeded_repository() -> SemanticRepository:
    repo = SemanticRepository(
        MemorySemanticStore(), tenant_id="tenant-a", project_id="project-a"
    )
    for type_id, kind in (
        ("software.repository", "entity"),
        ("software.issue", "entity"),
        ("software.change", "entity"),
        ("software.pull-request", "entity"),
        ("software.ci-check", "entity"),
        ("software.contains", "relationship"),
        ("software.produces", "relationship"),
        ("software.verified-by", "relationship"),
        ("software.status", "relationship"),
    ):
        repo.put_type(
            SemanticType(
                tenant_id="tenant-a",
                project_id="project-a",
                type_id=type_id,
                type_kind=kind,
                label=type_id,
                description=type_id,
                pack_id="software-change",
                pack_version="1.0.0",
                provenance=provenance("type/" + type_id),
                revision=1,
            ),
            expected_revision=0,
            at=NOW,
        )
    pack_values = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "pack_id": "software-change",
        "version": "1.0.0",
        "type_ids": (
            "software.repository",
            "software.issue",
            "software.change",
            "software.pull-request",
            "software.ci-check",
            "software.contains",
            "software.produces",
            "software.verified-by",
            "software.status",
        ),
        "mappings": (),
        "provenance": provenance("pack-source"),
        "revision": 1,
    }
    dummy_signature = PackSignature(
        issuer="https://trust.example.test",
        key_id="key-1",
        algorithm="external-attestation",
        signed_digest=DIGEST_A,
        signature="fixture",
        signed_at=NOW,
    )
    content_digest = canonical_digest(
        SemanticPack.model_construct(
            **pack_values,
            content_digest=DIGEST_A,
            signature=dummy_signature,
            signature_verification=None,
        ).model_dump(
            mode="json",
            exclude={"content_digest", "signature", "signature_verification"},
        )
    )
    signature = PackSignature(
        issuer="https://trust.example.test",
        key_id="key-1",
        algorithm="external-attestation",
        signed_digest=content_digest,
        signature="fixture",
        signed_at=NOW,
    )
    verification = PackSignatureVerification(
        status="trusted",
        request_digest=DIGEST_A,
        issuer=signature.issuer,
        key_id=signature.key_id,
        algorithm=signature.algorithm,
        verifier_id="fixture-verifier",
        verifier_version="1.0.0",
        verifier_digest=DIGEST_A,
        trust_policy_digest=DIGEST_B,
        reason_code="signature-verified",
        verified_at=NOW,
    )
    pack = SemanticPack(
        **pack_values,
        content_digest=content_digest,
        signature=signature,
        signature_verification=verification,
    )
    repo._save("semantic_pack", pack.pack_id, pack, expected_revision=0, at=NOW)
    ids = {
        "repo": "krail+semantic://github.example.test/repository/acme",
        "issue": "krail+semantic://github.example.test/issue/42",
        "change": "krail+semantic://github.example.test/change/abc123",
        "pr": "krail+semantic://github.example.test/pull-request/9",
        "ci": "krail+semantic://github.example.test/ci/check-7",
    }
    types = {
        "repo": "software.repository",
        "issue": "software.issue",
        "change": "software.change",
        "pr": "software.pull-request",
        "ci": "software.ci-check",
    }
    for name, entity_id in ids.items():
        repo.put_entity(
            Entity(
                tenant_id="tenant-a",
                project_id="project-a",
                entity_id=entity_id,
                type_id=types[name],
                canonical_name=name.upper(),
                provenance=provenance(name),
                revision=1,
            ),
            expected_revision=0,
            at=NOW,
        )
    repo.put_alias(
        Alias(
            tenant_id="tenant-a",
            project_id="project-a",
            alias_id="issue-42",
            entity_id=ids["issue"],
            value="#42",
            normalized_value="42",
            provenance=provenance("alias/42"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    edges = (
        ("repo-issue", ids["repo"], ids["issue"], "software.contains"),
        ("issue-change", ids["issue"], ids["change"], "software.produces"),
        ("change-pr", ids["change"], ids["pr"], "software.produces"),
        ("pr-ci", ids["pr"], ids["ci"], "software.verified-by"),
    )
    for fact_id, subject, target, relationship in edges:
        repo.put_fact(
            Fact(
                tenant_id="tenant-a",
                project_id="project-a",
                fact_id=fact_id,
                subject_entity_id=subject,
                relationship_type_id=relationship,
                object=FactObject(entity_id=target),
                assertion_kind="observation",
                confidence=1,
                provenance=provenance("fact/" + fact_id),
                revision=1,
            ),
            expected_revision=0,
            at=NOW,
        )
    for fact_id, value in (("ci-pass", "passed"), ("ci-fail", "failed")):
        repo.put_fact(
            Fact(
                tenant_id="tenant-a",
                project_id="project-a",
                fact_id=fact_id,
                subject_entity_id=ids["ci"],
                relationship_type_id="software.status",
                object=FactObject(literal=value),
                assertion_kind="observation",
                confidence=1,
                provenance=provenance("fact/" + fact_id),
                revision=1,
            ),
            expected_revision=0,
            at=NOW,
        )
    repo.put_conflict(
        Conflict(
            tenant_id="tenant-a",
            project_id="project-a",
            conflict_id="ci-status",
            fact_ids=("ci-pass", "ci-fail"),
            reason="contradictory-value",
            provenance=provenance("conflict/ci-status"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    return repo


def authorized_scope(value: SemanticReadScope) -> AuthorizedSemanticScope:
    # Fixed authoritative source/classification metadata, independent of the
    # caller's requested grants.
    permitted = {
        ResourceRef(
            authority=AUTHORITY,
            resource_type="issue",
            resource_id=item,
            version="etag:v1",
            digest=DIGEST_A,
        ).exact_key
        for item in VISIBLE_SOURCE_IDS
    }
    return AuthorizedSemanticScope(
        tenant_id=value.tenant_id,
        project_id=value.project_id,
        subject_id=value.subject_id,
        policy_digest=value.policy_digest,
        authorization_context_digest=DIGEST_B,
        request_scope_digest=value.scope_digest,
        evidence_keys=frozenset(
            item.source.exact_key
            for item in value.allowed_sources
            if item.source.exact_key in permitted
            and item.source_id == item.source.resource_id
            and item.classification == "internal"
        ),
    )


def service(repo=None, *, clock=None) -> SemanticOperationsService:
    return SemanticOperationsService(
        repo or seeded_repository(),
        cursor_key=b"semantic-test-cursor-key-32-bytes!!",
        authorize_scope=authorized_scope,
        **({"clock": clock} if clock is not None else {}),
    )


def test_repository_issue_change_pr_ci_vertical_is_bounded_and_grounded():
    operations = service()
    resolved = operations.resolve_entity(
        ResolveEntityRequest(scope=scope(), query="42")
    )
    assert resolved.candidates[0].match == "alias"
    issue_id = resolved.candidates[0].entity.entity_id
    fetched = operations.get_entity(GetEntityRequest(scope=scope(), entity_id=issue_id))
    assert fetched.entity and fetched.aliases[0].value == "#42"
    traversal = operations.traverse_relationships(
        TraverseRelationshipsRequest(
            scope=scope(),
            root_entity_id="krail+semantic://github.example.test/repository/acme",
            direction="outgoing",
            budget=OperationBudget(
                max_depth=8, max_nodes=20, max_edges=20, max_items=20
            ),
        )
    )
    assert [hop.relationship.fact_id for hop in traversal.hops] == [
        "repo-issue",
        "issue-change",
        "change-pr",
        "pr-ci",
    ]
    evidence = operations.assemble_cross_source_evidence(
        AssembleCrossSourceEvidenceRequest(
            scope=scope(), entity_ids=(issue_id,), decision="Assess issue delivery"
        )
    )
    assert evidence.statements and all(
        statement.citations for statement in evidence.statements
    )
    assert all(ref.authority == AUTHORITY for ref in evidence.citations)
    for statement in evidence.statements:
        assert (
            statement.fact.lineage.semantic_type_id
            == statement.fact.relationship_type_id
        )
        assert statement.fact.lineage.semantic_type_revision == 1
        assert statement.fact.lineage.pack_id == "software-change"
        assert statement.fact.lineage.pack_version == "1.0.0"
        assert statement.fact.lineage.pack_digest.startswith("sha256:")


def test_policy_shaping_omits_whole_records_without_hidden_counts_or_ids():
    repo = seeded_repository()
    hidden = Entity(
        tenant_id="tenant-a",
        project_id="project-a",
        entity_id="krail+semantic://private.example.test/issue/secret",
        type_id="software.issue",
        canonical_name="SECRET",
        provenance=provenance("secret", authority="https://private.example.test"),
        revision=1,
    )
    repo.put_entity(hidden, expected_revision=0, at=NOW)
    same_authority_hidden = Entity(
        tenant_id="tenant-a",
        project_id="project-a",
        entity_id="krail+semantic://github.example.test/issue/source-denied",
        type_id="software.issue",
        canonical_name="SOURCE-DENIED",
        provenance=provenance("same-authority-hidden"),
        revision=1,
    )
    repo.put_entity(same_authority_hidden, expected_revision=0, at=NOW)
    repo.put_entity(
        Entity(
            tenant_id="tenant-a",
            project_id="project-a",
            entity_id="krail+semantic://github.example.test/issue/merged-visible",
            type_id="software.issue",
            canonical_name="MERGED-VISIBLE",
            state="merged",
            merged_into=same_authority_hidden.entity_id,
            provenance=provenance("merged-visible"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    repo.put_alias(
        Alias(
            tenant_id="tenant-a",
            project_id="project-a",
            alias_id="hidden-visible-alias",
            entity_id=same_authority_hidden.entity_id,
            value="PRIVATE-ALIAS",
            normalized_value="private-alias",
            provenance=provenance("alias/hidden-visible"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    repo.put_fact(
        Fact(
            tenant_id="tenant-a",
            project_id="project-a",
            fact_id="public-to-hidden",
            subject_entity_id="krail+semantic://github.example.test/issue/42",
            relationship_type_id="software.produces",
            object=FactObject(entity_id=hidden.entity_id),
            assertion_kind="observation",
            confidence=1,
            provenance=provenance("fact/public-to-hidden"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    result = service(repo).resolve_entity(
        ResolveEntityRequest(scope=scope(), query="SECRET")
    )
    assert not result.candidates
    projection = result.model_dump_json()
    assert "private.example" not in projection and "secret" not in projection
    fetched = service(repo).get_entity(
        GetEntityRequest(
            scope=scope(), entity_id="krail+semantic://github.example.test/issue/42"
        )
    )
    assert "private.example" not in fetched.model_dump_json()
    denied_same_authority = service(repo).resolve_entity(
        ResolveEntityRequest(scope=scope(), query="SOURCE-DENIED")
    )
    assert not denied_same_authority.candidates
    assert "source-denied" not in denied_same_authority.model_dump_json()
    denied_merged = service(repo).resolve_entity(
        ResolveEntityRequest(scope=scope(), query="MERGED-VISIBLE")
    )
    assert not denied_merged.candidates
    assert "source-denied" not in denied_merged.model_dump_json()
    denied_alias = service(repo).resolve_entity(
        ResolveEntityRequest(scope=scope(), query="private-alias")
    )
    assert not denied_alias.candidates
    assert "source-denied" not in denied_alias.model_dump_json()
    denied_pack_lineage = service(repo).resolve_entity(
        ResolveEntityRequest(
            scope=scope(
                source_ids=tuple(
                    item for item in VISIBLE_SOURCE_IDS if item != "pack-source"
                )
            ),
            query="42",
        )
    )
    assert not denied_pack_lineage.candidates
    assert denied_pack_lineage.omissions.present
    with pytest.raises(ValueError, match="scope digest"):
        SemanticReadScope(
            **{**scope().model_dump(mode="python"), "subject_id": "attacker"}
        )


def test_live_scope_authorizer_is_rechecked_and_revocation_fails_closed():
    active = True

    def authorize(value):
        if not active:
            raise PermissionError("semantic scope is unavailable")
        return authorized_scope(value)

    operations = SemanticOperationsService(
        seeded_repository(),
        cursor_key=b"semantic-test-cursor-key-32-bytes!!",
        authorize_scope=authorize,
    )
    request = ResolveEntityRequest(scope=scope(), query="42")
    assert operations.resolve_entity(request).candidates
    active = False
    with pytest.raises(PermissionError, match="unavailable"):
        operations.resolve_entity(request)


def test_phase4_claim_binding_rejects_same_authority_source_and_classification_lies():
    requested = scope(source_ids=("issue",))
    claims = SimpleNamespace(
        tenant_id="tenant-a",
        project_id="project-a",
        subject="user/alice",
        policy_digest=DIGEST_A,
        actions=("capture.read",),
        source_ids=frozenset(("issue",)),
        classifications=frozenset(("internal",)),
    )
    exact = authorize_semantic_scope_from_claims(
        requested,
        claims,
        authorization_context_digest=DIGEST_B,
        resolve_source=lambda _ref: ("issue", "internal"),
    )
    assert exact.evidence_keys == frozenset(
        (requested.allowed_sources[0].source.exact_key,)
    )
    source_lie = authorize_semantic_scope_from_claims(
        requested,
        claims,
        authorization_context_digest=DIGEST_B,
        resolve_source=lambda _ref: ("different-source", "internal"),
    )
    classification_lie = authorize_semantic_scope_from_claims(
        requested,
        claims,
        authorization_context_digest=DIGEST_B,
        resolve_source=lambda _ref: ("issue", "restricted"),
    )
    assert not source_lie.evidence_keys and not classification_lie.evidence_keys


def test_signed_phase4_context_is_verified_and_revocation_rechecked_per_graph_read():
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"key-1": b"phase4-semantic-test-key"},
        issuer="https://control.example.test",
        revocations=revocations,
    )
    claims = AccessClaims(
        issuer="https://control.example.test",
        tenant_id="tenant-a",
        project_id="project-a",
        subject="user/alice",
        delegator="user/alice",
        delegation_id="delegation/semantic",
        capability_id="krail.semantic-operations",
        capability_version="1.0.0",
        capability_digest=DIGEST_A,
        actions=("capture.read",),
        source_ids=VISIBLE_SOURCE_IDS,
        classifications=("internal",),
        policy_digest=DIGEST_A,
        issued_at=NOW - timedelta(minutes=2),
        not_before=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
        nonce="semantic-nonce",
    )
    context = authority.issue(claims, key_id="key-1")
    operations = SemanticOperationsService(
        seeded_repository(),
        cursor_key=b"semantic-test-cursor-key-32-bytes!!",
        authorize_scope=semantic_scope_authorizer_from_context(
            authority,
            context,
            resolve_source=lambda ref: (ref.resource_id, "internal"),
            clock=lambda: NOW,
        ),
    )
    request = ResolveEntityRequest(scope=scope(), query="42")
    assert operations.resolve_entity(request).candidates
    revocations.revoke_context(context.context_digest, revoked_at=NOW)
    with pytest.raises(PermissionError):
        operations.resolve_entity(request)


def test_cursor_binds_operation_scope_and_snapshot_and_budget_gaps_are_typed():
    repo = seeded_repository()
    operations = service(repo)
    first = operations.traverse_relationships(
        TraverseRelationshipsRequest(
            scope=scope(),
            root_entity_id="krail+semantic://github.example.test/repository/acme",
            direction="outgoing",
            budget=OperationBudget(
                max_depth=8, max_nodes=20, max_edges=20, max_items=1
            ),
        )
    )
    assert first.truncated and first.next_cursor and first.gaps[0].code == "item-budget"
    with pytest.raises(ValueError, match="cursor"):
        operations.resolve_entity(
            ResolveEntityRequest(scope=scope(), query="42", cursor=first.next_cursor)
        )
    repo.put_entity(
        Entity(
            tenant_id="tenant-a",
            project_id="project-a",
            entity_id="krail+semantic://github.example.test/issue/hidden-cursor-change",
            type_id="software.issue",
            canonical_name="HIDDEN-CURSOR",
            provenance=provenance("hidden-cursor-source"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    unchanged_visible = operations.traverse_relationships(
        TraverseRelationshipsRequest(
            scope=scope(),
            root_entity_id="krail+semantic://github.example.test/repository/acme",
            direction="outgoing",
            cursor=first.next_cursor,
            budget=OperationBudget(
                max_depth=8, max_nodes=20, max_edges=20, max_items=1
            ),
        )
    )
    assert unchanged_visible.hops and all(
        gap.code != "cursor-stale" for gap in unchanged_visible.gaps
    )
    assert unchanged_visible.trace.snapshot_digest == first.trace.snapshot_digest
    repo.put_entity(
        Entity(
            tenant_id="tenant-a",
            project_id="project-a",
            entity_id="krail+semantic://github.example.test/issue/43",
            type_id="software.issue",
            canonical_name="ISSUE-43",
            provenance=provenance("issue/43"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    stale = operations.traverse_relationships(
        TraverseRelationshipsRequest(
            scope=scope(),
            root_entity_id="krail+semantic://github.example.test/repository/acme",
            direction="outgoing",
            cursor=first.next_cursor,
            budget=OperationBudget(
                max_depth=8, max_nodes=20, max_edges=20, max_items=1
            ),
        )
    )
    assert stale.truncated and stale.gaps[0].code == "cursor-stale" and not stale.hops


def test_compare_and_conflict_explanation_never_hide_partial_truth():
    operations = service()
    compared = operations.compare_observations(
        CompareObservationsRequest(scope=scope(), fact_ids=("ci-pass", "ci-fail"))
    )
    assert compared.differences[0].code == "conflict"
    explained = operations.explain_conflict(
        ExplainConflictRequest(scope=scope(), conflict_id="ci-status")
    )
    assert explained.state == "open" and {item.fact_id for item in explained.facts} == {
        "ci-pass",
        "ci-fail",
    }


def test_hidden_conflict_resolution_fact_id_is_never_projected():
    repo = seeded_repository()
    repo.put_fact(
        Fact(
            tenant_id="tenant-a",
            project_id="project-a",
            fact_id="hidden-resolution",
            subject_entity_id="krail+semantic://github.example.test/ci/check-7",
            relationship_type_id="software.status",
            object=FactObject(literal="passed"),
            assertion_kind="observation",
            confidence=1,
            provenance=provenance("hidden-resolution-source"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    current = repo.get("conflict", "ci-status")
    repo.put_conflict(
        Conflict.model_validate(
            {
                **current.model_dump(mode="python"),
                "state": "resolved",
                "resolution_fact_id": "hidden-resolution",
                "revision": 2,
            }
        ),
        expected_revision=1,
        at=NOW,
    )
    explained = service(repo).explain_conflict(
        ExplainConflictRequest(scope=scope(), conflict_id="ci-status")
    )
    assert explained.state == "resolved"
    assert explained.resolution_fact_id is None
    assert any(gap.code == "partial-evidence" for gap in explained.gaps)
    assert "hidden-resolution" not in explained.model_dump_json()


def test_proposal_history_is_policy_bound_and_never_auto_publishes():
    repo = seeded_repository()
    induction = OntologyInductionService(repo)
    change = induction.build_change_set(
        change_set_id="change-1",
        package_id="software-change",
        operations=(
            OntologyChangeOperation(
                operation_id="op-1",
                operation="add",
                target_kind="concept",
                target_id="software.release",
                after={"label": "Release"},
            ),
        ),
        authorship=DraftAuthorship(
            actor_id="user/alice", actor_kind="human", policy_digest=DIGEST_A
        ),
        created_at=NOW,
    )
    version = induction.build_version(
        package_id="software-change",
        version="2.0.0-proposal",
        observations=(
            ObservedStructure(
                tenant_id="tenant-a",
                project_id="project-a",
                observation_id="observation-1",
                source=provenance("proposal-source").evidence[0],
                structure_kind="record",
                path="$.issue",
                sample_digest=DIGEST_A,
                occurrence_count=1,
                provenance=provenance("proposal-source"),
            ),
        ),
        change_set=change,
        provenance=provenance("proposal-source"),
    )
    induction.propose(version, change, proposed_at=NOW)
    visible = service(repo).list_ontology_proposal_history(
        ListOntologyProposalHistoryRequest(scope=scope(), package_id="software-change")
    )
    assert visible.proposals[0].state == "proposed"
    assert visible.proposals[0].proposed_version == version.version
    assert visible.proposals[0].proposal_content_digest == version.content_digest
    assert visible.proposals[0].provenance.evidence == version.provenance.evidence
    denied = service(repo).list_ontology_proposal_history(
        ListOntologyProposalHistoryRequest(
            scope=scope(policy_digest=DIGEST_B), package_id="software-change"
        )
    )
    assert not denied.proposals
    denied_source = service(repo).list_ontology_proposal_history(
        ListOntologyProposalHistoryRequest(
            scope=scope(
                source_ids=tuple(
                    item for item in VISIBLE_SOURCE_IDS if item != "proposal-source"
                )
            ),
            package_id="software-change",
        )
    )
    assert (
        not denied_source.proposals
        and "proposal-source" not in denied_source.model_dump_json()
    )


def test_reviewed_package_projection_binds_exact_version_and_change_digests():
    repo = seeded_repository()
    induction = OntologyInductionService(repo)
    operation = OntologyChangeOperation(
        operation_id="op-1",
        operation="add",
        target_kind="concept",
        target_id="software.release",
        after={"label": "Release"},
    )
    authorship = DraftAuthorship(
        actor_id="user/alice", actor_kind="human", policy_digest=DIGEST_A
    )
    change = induction.build_change_set(
        change_set_id="change-1",
        package_id="software-change",
        operations=(operation,),
        authorship=authorship,
        created_at=NOW,
    )
    observation = ObservedStructure(
        tenant_id="tenant-a",
        project_id="project-a",
        observation_id="observation-1",
        source=provenance("proposal-source").evidence[0],
        structure_kind="record",
        path="$.issue",
        sample_digest=DIGEST_A,
        occurrence_count=1,
        provenance=provenance("proposal-source"),
    )
    version = induction.build_version(
        package_id="software-change",
        version="2.0.0-proposal",
        observations=(observation,),
        change_set=change,
        provenance=provenance("proposal-source"),
    )
    induction.propose(version, change, proposed_at=NOW)
    in_review = induction.begin_review("software-change", at=NOW)
    review_digest = induction.review_decision_digest(
        in_review, version, reviewer="user/reviewer", accepted=True
    )
    induction.review(
        "software-change",
        reviewer="user/reviewer",
        review_digest=review_digest,
        accepted=True,
        at=NOW,
    )
    visible = service(repo).list_ontology_packages(
        ListOntologyPackagesRequest(scope=scope())
    )
    assert visible.packages[0].version_content_digest == version.content_digest

    change_key = (
        "tenant-a", "project-a", "ontology_change_set", change.change_set_id
    )
    durable_change = repo.store._rows.pop(change_key)
    missing_change = service(repo).list_ontology_packages(
        ListOntologyPackagesRequest(scope=scope())
    )
    assert not missing_change.packages
    repo.store._rows[change_key] = durable_change

    different_change = induction.build_change_set(
        change_set_id="change-2",
        package_id="software-change",
        operations=(operation.model_copy(update={"operation_id": "op-2"}),),
        authorship=authorship,
        created_at=NOW,
    )
    substituted = induction.build_version(
        package_id="software-change",
        version=version.version,
        observations=(observation,),
        change_set=different_change,
        provenance=provenance("proposal-source"),
    )
    repo._save(
        "ontology_package_version",
        f"{substituted.package_id}@{substituted.version}",
        substituted,
        expected_revision=1,
        at=NOW,
    )
    denied = service(repo).list_ontology_packages(
        ListOntologyPackagesRequest(scope=scope())
    )
    assert not denied.packages


def test_serialized_byte_and_wall_clock_budgets_are_truthful():
    unbounded = service().get_entity(
        GetEntityRequest(
            scope=scope(),
            entity_id="krail+semantic://github.example.test/issue/42",
        )
    )
    assert len(unbounded.model_dump_json().encode()) > 1024
    byte_limited = service().get_entity(
        GetEntityRequest(
            scope=scope(),
            entity_id="krail+semantic://github.example.test/issue/42",
            budget=OperationBudget(max_bytes=1024, max_items=100),
        )
    )
    assert len(byte_limited.model_dump_json().encode()) <= 1024
    assert byte_limited.truncated
    assert any(gap.code == "byte-budget" for gap in byte_limited.gaps)

    compared = service().compare_observations(
        CompareObservationsRequest(
            scope=scope(),
            fact_ids=("ci-pass", "ci-fail"),
            budget=OperationBudget(max_bytes=1024),
        )
    )
    assert len(compared.model_dump_json().encode()) <= 1024
    assert compared.truncated
    assert any(gap.code == "byte-budget" for gap in compared.gaps)

    ticks = iter((0.0, 0.066))
    time_limited = service(clock=lambda: next(ticks)).resolve_entity(
        ResolveEntityRequest(
            scope=scope(), query="42", budget=OperationBudget(max_time_ms=1)
        )
    )
    assert time_limited.truncated and not time_limited.candidates
    assert any(gap.code == "time-budget" for gap in time_limited.gaps)

    delayed_repo = seeded_repository()
    original_list = delayed_repo.store.list

    def delayed_list(*args, **kwargs):
        time.sleep(0.01)
        return original_list(*args, **kwargs)

    delayed_repo.store.list = delayed_list
    delayed = service(delayed_repo).resolve_entity(
        ResolveEntityRequest(
            scope=scope(), query="42", budget=OperationBudget(max_time_ms=1)
        )
    )
    assert delayed.truncated
    assert any(gap.code == "time-budget" for gap in delayed.gaps)


def test_time_budget_covers_snapshot_iteration_and_final_transport_shaping():
    repo = seeded_repository()
    original_list = repo.store.list
    visited = 0

    def oversized_list(*args, **kwargs):
        rows = original_list(*args, **kwargs) * 100

        def iterate():
            nonlocal visited
            for row in rows:
                visited += 1
                yield row

        return iterate()

    repo.store.list = oversized_list
    clock_values = iter((0.0, 0.002, 0.002))
    result = service(repo, clock=lambda: next(clock_values)).resolve_entity(
        ResolveEntityRequest(
            scope=scope(), query="42", budget=OperationBudget(max_time_ms=1)
        )
    )
    assert visited == 64
    assert result.truncated and not result.candidates
    assert any(gap.code == "time-budget" for gap in result.gaps)

    final_ticks = iter((0.0, 0.0005, 0.002))
    final = service(clock=lambda: next(final_ticks)).resolve_entity(
        ResolveEntityRequest(
            scope=scope(), query="42", budget=OperationBudget(max_time_ms=1)
        )
    )
    assert final.truncated and not final.candidates
    assert any(gap.code == "time-budget" for gap in final.gaps)


def test_oversized_first_item_advances_without_cursor_dead_loop():
    repo = seeded_repository()
    repo.put_alias(
        Alias(
            tenant_id="tenant-a",
            project_id="project-a",
            alias_id="repo-42",
            entity_id="krail+semantic://github.example.test/repository/acme",
            value="42",
            normalized_value="42",
            provenance=provenance("alias/repo-42"),
            revision=1,
        ),
        expected_revision=0,
        at=NOW,
    )
    operations = service(repo)
    first = operations.resolve_entity(
        ResolveEntityRequest(
            scope=scope(), query="42", budget=OperationBudget(max_bytes=1024)
        )
    )
    assert not first.candidates and first.next_cursor
    assert len(first.model_dump_json().encode()) <= 1024
    assert any(gap.code == "byte-budget" for gap in first.gaps)
    second = operations.resolve_entity(
        ResolveEntityRequest(
            scope=scope(),
            query="42",
            cursor=first.next_cursor,
            budget=OperationBudget(max_bytes=1024),
        )
    )
    assert second.next_cursor != first.next_cursor
    assert len(second.model_dump_json().encode()) <= 1024


def test_capability_is_read_only_and_publishes_only_fixed_operations():
    descriptor = semantic_operations_descriptor()
    assert descriptor.capability_id == "krail.semantic-operations"
    assert descriptor.effects.model_dump() == {
        "classification": "read-only",
        "external_effects": False,
    }
    assert {item.operation_id for item in descriptor.operations} == {
        "resolve_entity",
        "get_entity",
        "traverse_relationships",
        "compare_observations",
        "explain_conflict",
        "assemble_cross_source_evidence",
        "list_ontology_packages",
        "list_ontology_proposal_history",
    }
    limits = {item.name: (item.value, item.unit) for item in descriptor.limits}
    assert limits["max_bytes"] == (262_144, "utf8-bytes")
    assert limits["max_time_ms"] == (10_000, "milliseconds")


def test_packaged_software_change_fixture_matches_strict_models():
    fixture_path = (
        Path(__file__).parents[1]
        / "krail/resources/contracts/krail.semantic-operations.v1/fixtures/software-change.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture_scope = SemanticReadScope.model_validate(fixture["scope"])
    models = {
        "resolve_entity": ResolveEntityRequest,
        "traverse_relationships": TraverseRelationshipsRequest,
        "assemble_cross_source_evidence": AssembleCrossSourceEvidenceRequest,
    }
    for operation, model in models.items():
        model.model_validate({**fixture["requests"][operation], "scope": fixture_scope})
    assert len(fixture["vertical"]) == 5


def test_python_and_cli_semantic_operation_are_equivalent(capsys):
    request = ResolveEntityRequest(
        scope=scope(), query="42", budget=OperationBudget(max_bytes=1024)
    )
    operations = service()
    expected = operations.resolve_entity(request).model_dump(mode="json")
    assert expected["truncated"] and expected["gaps"][0]["code"] == "byte-budget"

    class _Provider:
        def semantic_operation(self, operation, actual):
            assert operation == "resolve_entity" and actual == request
            return operations.resolve_entity(actual)

    class _Project:
        provider = _Provider()

    rail_cli.cmd_provider(
        _Project(),
        argparse.Namespace(
            provider_command="semantic",
            operation="resolve_entity",
            request=request.model_dump_json(),
        ),
    )
    emitted = capsys.readouterr().out
    assert json.loads(emitted) == expected
    assert len(emitted.encode()) <= request.budget.max_bytes
    assert semantic_transport_size(operations.resolve_entity(request)) <= 1024


def test_local_provider_binds_live_actor_policy_and_exact_source_bytes(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("KRAIL_ACTOR", "attacker-controlled")
    root = tmp_path / "local-project"
    root.mkdir()
    source_path = root / "issue.md"
    source_path.write_text("exact source\n", encoding="utf-8")
    runtime = KnowledgeRuntime(root)
    provider = runtime.provider
    exact = provider._ref("issue.md")
    actor = local_semantic_actor()
    policy_digest = local_semantic_policy_digest(
        root.resolve(), actor
    )

    def local_scope(
        *, subject=actor.id, policy=policy_digest, ref=exact,
        source_id=None, classification="public",
    ):
        body = {
            "tenant_id": "local", "project_id": root.name,
            "subject_id": subject, "allowed_authorities": [ref.authority],
            "allowed_resource_types": [ref.resource_type],
            "allowed_classifications": ["public"],
            "allowed_sources": [{
                "contract": "krail.semantic-operations.v1",
                "source": ref.model_dump(mode="json"),
                "source_id": source_id or ref.resource_id,
                "classification": classification,
            }],
            "policy_digest": policy,
        }
        return SemanticReadScope(
            **body,
            scope_digest="sha256:" + hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )

    request = ResolveEntityRequest(scope=local_scope(), query="missing")
    assert provider.semantic_operation("resolve_entity", request).gaps[0].code == "not-found"
    with pytest.raises(PermissionError, match="unavailable"):
        runtime.application.semantic_operation(
            "resolve_entity",
            ResolveEntityRequest(scope=local_scope(subject="attacker"), query="missing"),
        )
    with pytest.raises(PermissionError, match="unavailable"):
        provider.semantic_operation(
            "resolve_entity",
            ResolveEntityRequest(scope=local_scope(policy=DIGEST_B), query="missing"),
        )
    with pytest.raises(PermissionError, match="unavailable"):
        rail_cli.cmd_provider(
            SimpleNamespace(provider=provider),
            argparse.Namespace(
                provider_command="semantic", operation="resolve_entity",
                request=ResolveEntityRequest(
                    scope=local_scope(subject="attacker"), query="missing"
                ).model_dump_json(),
            ),
        )
    assert capsys.readouterr().out == ""
    source_path.write_text("changed source\n", encoding="utf-8")
    stale = provider.semantic_operation("resolve_entity", request)
    assert not stale.candidates
    assert not runtime.application.semantic_operations._scope(
        request.scope
    ).evidence_keys

    restricted_path = root / "restricted.md"
    restricted_path.write_text(
        "---\nvisibility: restricted\n---\nprivate\n", encoding="utf-8"
    )
    restricted_ref = provider._ref("restricted.md")
    restricted_scope = local_scope(ref=restricted_ref)
    assert not runtime.application.semantic_operations._scope(
        restricted_scope
    ).evidence_keys
    spoofed_source = local_scope(ref=restricted_ref, source_id="other-source")
    assert not runtime.application.semantic_operations._scope(
        spoofed_source
    ).evidence_keys


class _Cursor:
    rowcount = 1

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.connection.statements.append((sql, params))

    def fetchall(self):
        return []

    def fetchmany(self, size):
        self.connection.fetchmany_sizes.append(size)
        return []


class _Connection:
    def __init__(self):
        self.statements = []
        self.fetchmany_sizes = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_postgres_graph_snapshot_is_one_scoped_nonlocking_read_transaction():
    connection = _Connection()
    store = PostgresSemanticStore(
        "postgresql://unused", connect=lambda _dsn: connection
    )
    repo = SemanticRepository(store, tenant_id="tenant-a", project_id="project-a")
    result = service(repo).resolve_entity(
        ResolveEntityRequest(scope=scope(), query="missing")
    )
    assert result.gaps[0].code == "not-found"
    assert len(connection.statements) == 1
    sql, params = connection.statements[0]
    assert "tenant_id=%s AND project_id=%s" in sql and "FOR UPDATE" not in sql
    assert params == ("tenant-a", "project-a")
    assert connection.fetchmany_sizes == [64]
    assert connection.committed and not connection.rolled_back
