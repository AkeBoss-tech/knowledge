from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
from datetime import UTC, datetime

import pytest
from krail.provider.semantic import (
    AssembleCrossSourceEvidenceRequest,
    CompareObservationsRequest,
    ExplainConflictRequest,
    GetEntityRequest,
    ListOntologyProposalHistoryRequest,
    OperationBudget,
    ResolveEntityRequest,
    SemanticReadScope,
    TraverseRelationshipsRequest,
)
from krail.provider.v1 import ResourceRef
from rail.capability_publication import semantic_operations_descriptor
from rail import cli as rail_cli
from rail.semantic import (
    Alias,
    Conflict,
    Entity,
    Fact,
    MemorySemanticStore,
    PostgresSemanticStore,
    SemanticOperationsService,
    SemanticRepository,
    SemanticType,
)
from rail.semantic.models import DraftAuthorship, EvidenceProvenance, FactObject, OntologyChangeOperation, OntologyChangeSet, canonical_digest
from rail.semantic.repository import SemanticRow


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
AUTHORITY = "https://github.example.test"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def provenance(resource_id: str, *, authority: str = AUTHORITY) -> EvidenceProvenance:
    return EvidenceProvenance(
        evidence=(ResourceRef(authority=authority, resource_type="issue", resource_id=resource_id, version="etag:v1", digest=DIGEST_A),),
        processing_version="semantic-kernel/1.0.0",
        processing_digest=DIGEST_B,
        observed_at=NOW,
    )


def scope(*, authorities=(AUTHORITY,), policy_digest=DIGEST_A) -> SemanticReadScope:
    body = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "subject_id": "user/alice",
        "allowed_authorities": list(authorities),
        "allowed_resource_types": [],
        "policy_digest": policy_digest,
    }
    digest = "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return SemanticReadScope(**body, scope_digest=digest)


def seeded_repository() -> SemanticRepository:
    repo = SemanticRepository(MemorySemanticStore(), tenant_id="tenant-a", project_id="project-a")
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
                tenant_id="tenant-a", project_id="project-a", type_id=type_id,
                type_kind=kind, label=type_id, description=type_id,
                pack_id="software-change", pack_version="1.0.0",
                provenance=provenance("type/" + type_id), revision=1,
            ),
            expected_revision=0, at=NOW,
        )
    ids = {
        "repo": "krail+semantic://github.example.test/repository/acme",
        "issue": "krail+semantic://github.example.test/issue/42",
        "change": "krail+semantic://github.example.test/change/abc123",
        "pr": "krail+semantic://github.example.test/pull-request/9",
        "ci": "krail+semantic://github.example.test/ci/check-7",
    }
    types = {
        "repo": "software.repository", "issue": "software.issue", "change": "software.change",
        "pr": "software.pull-request", "ci": "software.ci-check",
    }
    for name, entity_id in ids.items():
        repo.put_entity(
            Entity(
                tenant_id="tenant-a", project_id="project-a", entity_id=entity_id,
                type_id=types[name], canonical_name=name.upper(), provenance=provenance(name), revision=1,
            ),
            expected_revision=0, at=NOW,
        )
    repo.put_alias(
        Alias(
            tenant_id="tenant-a", project_id="project-a", alias_id="issue-42",
            entity_id=ids["issue"], value="#42", normalized_value="42",
            provenance=provenance("alias/42"), revision=1,
        ),
        expected_revision=0, at=NOW,
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
                tenant_id="tenant-a", project_id="project-a", fact_id=fact_id,
                subject_entity_id=subject, relationship_type_id=relationship,
                object=FactObject(entity_id=target), assertion_kind="observation",
                confidence=1, provenance=provenance("fact/" + fact_id), revision=1,
            ),
            expected_revision=0, at=NOW,
        )
    for fact_id, value in (("ci-pass", "passed"), ("ci-fail", "failed")):
        repo.put_fact(
            Fact(
                tenant_id="tenant-a", project_id="project-a", fact_id=fact_id,
                subject_entity_id=ids["ci"], relationship_type_id="software.status",
                object=FactObject(literal=value), assertion_kind="observation", confidence=1,
                provenance=provenance("fact/" + fact_id), revision=1,
            ),
            expected_revision=0, at=NOW,
        )
    repo.put_conflict(
        Conflict(
            tenant_id="tenant-a", project_id="project-a", conflict_id="ci-status",
            fact_ids=("ci-pass", "ci-fail"), reason="contradictory-value",
            provenance=provenance("conflict/ci-status"), revision=1,
        ),
        expected_revision=0, at=NOW,
    )
    return repo


def service(repo=None) -> SemanticOperationsService:
    return SemanticOperationsService(
        repo or seeded_repository(),
        cursor_key=b"semantic-test-cursor-key-32-bytes!!",
        authorize_scope=lambda value: value,
    )


def test_repository_issue_change_pr_ci_vertical_is_bounded_and_grounded():
    operations = service()
    resolved = operations.resolve_entity(ResolveEntityRequest(scope=scope(), query="42"))
    assert resolved.candidates[0].match == "alias"
    issue_id = resolved.candidates[0].entity.entity_id
    fetched = operations.get_entity(GetEntityRequest(scope=scope(), entity_id=issue_id))
    assert fetched.entity and fetched.aliases[0].value == "#42"
    traversal = operations.traverse_relationships(
        TraverseRelationshipsRequest(
            scope=scope(), root_entity_id="krail+semantic://github.example.test/repository/acme",
            direction="outgoing", budget=OperationBudget(max_depth=8, max_nodes=20, max_edges=20, max_items=20),
        )
    )
    assert [hop.relationship.fact_id for hop in traversal.hops] == ["repo-issue", "issue-change", "change-pr", "pr-ci"]
    evidence = operations.assemble_cross_source_evidence(
        AssembleCrossSourceEvidenceRequest(scope=scope(), entity_ids=(issue_id,), decision="Assess issue delivery")
    )
    assert evidence.statements and all(statement.citations for statement in evidence.statements)
    assert all(ref.authority == AUTHORITY for ref in evidence.citations)


def test_policy_shaping_omits_whole_records_without_hidden_counts_or_ids():
    repo = seeded_repository()
    hidden = Entity(
        tenant_id="tenant-a", project_id="project-a",
        entity_id="krail+semantic://private.example.test/issue/secret", type_id="software.issue",
        canonical_name="SECRET", provenance=provenance("secret", authority="https://private.example.test"), revision=1,
    )
    repo.put_entity(hidden, expected_revision=0, at=NOW)
    repo.put_fact(
        Fact(
            tenant_id="tenant-a", project_id="project-a", fact_id="public-to-hidden",
            subject_entity_id="krail+semantic://github.example.test/issue/42",
            relationship_type_id="software.produces",
            object=FactObject(entity_id=hidden.entity_id), assertion_kind="observation",
            confidence=1, provenance=provenance("fact/public-to-hidden"), revision=1,
        ), expected_revision=0, at=NOW,
    )
    result = service(repo).resolve_entity(ResolveEntityRequest(scope=scope(), query="SECRET"))
    assert not result.candidates and result.omissions.present
    projection = result.model_dump_json()
    assert "private.example" not in projection and "secret" not in projection
    fetched = service(repo).get_entity(
        GetEntityRequest(scope=scope(), entity_id="krail+semantic://github.example.test/issue/42")
    )
    assert fetched.omissions.present and "private.example" not in fetched.model_dump_json()
    with pytest.raises(ValueError, match="scope digest"):
        SemanticReadScope(**{**scope().model_dump(mode="python"), "subject_id": "attacker"})


def test_live_scope_authorizer_is_rechecked_and_revocation_fails_closed():
    active = True

    def authorize(value):
        if not active:
            raise PermissionError("semantic scope is unavailable")
        return value

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


def test_cursor_binds_operation_scope_and_snapshot_and_budget_gaps_are_typed():
    repo = seeded_repository()
    operations = service(repo)
    first = operations.traverse_relationships(
        TraverseRelationshipsRequest(
            scope=scope(), root_entity_id="krail+semantic://github.example.test/repository/acme",
            direction="outgoing", budget=OperationBudget(max_depth=8, max_nodes=20, max_edges=20, max_items=1),
        )
    )
    assert first.truncated and first.next_cursor and first.gaps[0].code == "item-budget"
    with pytest.raises(ValueError, match="cursor"):
        operations.resolve_entity(ResolveEntityRequest(scope=scope(), query="42", cursor=first.next_cursor))
    repo.put_entity(
        Entity(
            tenant_id="tenant-a", project_id="project-a",
            entity_id="krail+semantic://github.example.test/issue/43", type_id="software.issue",
            canonical_name="ISSUE-43", provenance=provenance("issue/43"), revision=1,
        ), expected_revision=0, at=NOW,
    )
    stale = operations.traverse_relationships(
        TraverseRelationshipsRequest(
            scope=scope(), root_entity_id="krail+semantic://github.example.test/repository/acme",
            direction="outgoing", cursor=first.next_cursor,
            budget=OperationBudget(max_depth=8, max_nodes=20, max_edges=20, max_items=1),
        )
    )
    assert stale.truncated and stale.gaps[0].code == "cursor-stale" and not stale.hops


def test_compare_and_conflict_explanation_never_hide_partial_truth():
    operations = service()
    compared = operations.compare_observations(
        CompareObservationsRequest(scope=scope(), fact_ids=("ci-pass", "ci-fail"))
    )
    assert compared.differences[0].code == "conflict"
    explained = operations.explain_conflict(ExplainConflictRequest(scope=scope(), conflict_id="ci-status"))
    assert explained.state == "open" and {item.fact_id for item in explained.facts} == {"ci-pass", "ci-fail"}


def test_proposal_history_is_policy_bound_and_never_auto_publishes():
    repo = seeded_repository()
    operation = OntologyChangeOperation(operation_id="op-1", operation="add", target_kind="concept", target_id="software.release", after={"label": "Release"})
    values = {
        "tenant_id": "tenant-a", "project_id": "project-a", "change_set_id": "change-1",
        "package_id": "software-change", "operations": (operation,),
        "authorship": DraftAuthorship(actor_id="user/alice", actor_kind="human", policy_digest=DIGEST_A),
        "created_at": NOW, "updated_at": NOW, "revision": 1,
    }
    digest = canonical_digest(OntologyChangeSet.model_construct(**values, change_digest=DIGEST_B).model_dump(mode="json", exclude={"change_digest", "state", "updated_at", "revision"}))
    change = OntologyChangeSet(**values, change_digest=digest)
    repo._save("ontology_change_set", change.change_set_id, change, expected_revision=0, at=NOW)
    visible = service(repo).list_ontology_proposal_history(
        ListOntologyProposalHistoryRequest(scope=scope(), package_id="software-change")
    )
    assert visible.proposals[0].state == "draft"
    denied = service(repo).list_ontology_proposal_history(
        ListOntologyProposalHistoryRequest(scope=scope(policy_digest=DIGEST_B), package_id="software-change")
    )
    assert not denied.proposals and denied.omissions.reasons == ("policy",)


def test_capability_is_read_only_and_publishes_only_fixed_operations():
    descriptor = semantic_operations_descriptor()
    assert descriptor.capability_id == "krail.semantic-operations"
    assert descriptor.effects.model_dump() == {"classification": "read-only", "external_effects": False}
    assert {item.operation_id for item in descriptor.operations} == {
        "resolve_entity", "get_entity", "traverse_relationships", "compare_observations",
        "explain_conflict", "assemble_cross_source_evidence", "list_ontology_packages",
        "list_ontology_proposal_history",
    }


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
    request = ResolveEntityRequest(scope=scope(), query="42")
    operations = service()
    expected = operations.resolve_entity(request).model_dump(mode="json")

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
    assert json.loads(capsys.readouterr().out) == expected


class _Cursor:
    rowcount = 1
    def __init__(self, connection): self.connection = connection
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params): self.connection.statements.append((sql, params))
    def fetchall(self): return []


class _Connection:
    def __init__(self): self.statements = []; self.committed = False; self.rolled_back = False
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return _Cursor(self)
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True


def test_postgres_graph_snapshot_is_one_scoped_nonlocking_read_transaction():
    connection = _Connection()
    store = PostgresSemanticStore("postgresql://unused", connect=lambda _dsn: connection)
    repo = SemanticRepository(store, tenant_id="tenant-a", project_id="project-a")
    result = service(repo).resolve_entity(ResolveEntityRequest(scope=scope(), query="missing"))
    assert result.gaps[0].code == "not-found"
    assert len(connection.statements) == 1
    sql, params = connection.statements[0]
    assert "tenant_id=%s AND project_id=%s" in sql and "FOR UPDATE" not in sql
    assert params == ("tenant-a", "project-a")
    assert connection.committed and not connection.rolled_back
