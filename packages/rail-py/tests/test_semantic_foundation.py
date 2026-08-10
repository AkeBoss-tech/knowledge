from __future__ import annotations

from datetime import UTC, datetime

import pytest

from krail.provider.v1 import ResourceRef
from rail.hosted import ConcurrencyConflict
from rail.semantic import (
    Alias,
    CandidateConcept,
    Conflict,
    Entity,
    Fact,
    JsonSemanticStore,
    MemorySemanticStore,
    ObservedStructure,
    OntologyChangeOperation,
    OntologyInductionService,
    PackMapping,
    PackSignature,
    PostgresSemanticStore,
    ReviewerQuestion,
    SemanticPack,
    SemanticPackService,
    SemanticRepository,
    SemanticType,
    ValidationFinding,
)
from rail.semantic.models import (
    DraftAuthorship,
    EvidenceProvenance,
    FactObject,
    canonical_digest,
)
from rail.semantic.packs import PackEvaluation, QualityMetric

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def evidence(resource_id: str = "issue-42") -> EvidenceProvenance:
    return EvidenceProvenance(
        evidence=(
            ResourceRef(
                authority="https://github.example.test",
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


def entity(entity_id: str, *, revision: int = 1) -> Entity:
    return Entity(
        tenant_id="tenant-a",
        project_id="project-a",
        entity_id=entity_id,
        type_id="software.issue",
        canonical_name=entity_id,
        provenance=evidence(entity_id),
        revision=revision,
    )


def repository(store=None) -> SemanticRepository:
    return SemanticRepository(
        store or MemorySemanticStore(), tenant_id="tenant-a", project_id="project-a"
    )


def authorship(*, agent: bool = False) -> DraftAuthorship:
    return DraftAuthorship(
        actor_id="agent/ontology" if agent else "user/alice",
        actor_kind="agent" if agent else "human",
        delegation_id="delegation/7" if agent else None,
        capability_digest=DIGEST_A if agent else None,
        policy_digest=DIGEST_B,
    )


def change_operation(operation_id: str = "op-1") -> OntologyChangeOperation:
    return OntologyChangeOperation(
        operation_id=operation_id,
        operation="add",
        target_kind="concept",
        target_id="software.issue",
        after={"label": "Issue"},
    )


def semantic_pack(*, version: str = "1.0.0", revision: int = 1) -> SemanticPack:
    values = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "pack_id": "software-change",
        "version": version,
        "type_ids": ("software.issue", "software.pull-request"),
        "mappings": (
            PackMapping(
                mapping_id="github.issue",
                source_kind="issue",
                source_path="$.issue",
                target_type_id="software.issue",
                target_field="canonical_name",
                transform="normalize-text",
            ),
        ),
        "provenance": evidence("pack-source"),
        "revision": revision,
    }
    digest = canonical_digest(
        SemanticPack.model_construct(
            **values,
            content_digest=DIGEST_A,
            signature=PackSignature(
                issuer="https://trust.example.test",
                key_id="key-1",
                algorithm="external-attestation",
                signed_digest=DIGEST_A,
                signature="fixture",
                signed_at=NOW,
            ),
        ).model_dump(mode="json", exclude={"content_digest", "signature"})
    )
    return SemanticPack(
        **values,
        content_digest=digest,
        signature=PackSignature(
            issuer="https://trust.example.test",
            key_id="key-1",
            algorithm="external-attestation",
            signed_digest=digest,
            signature="fixture-signature",
            signed_at=NOW,
        ),
    )


def test_semantic_kernel_requires_evidence_and_limited_valid_time():
    repo = repository()
    semantic_type = SemanticType(
        tenant_id="tenant-a",
        project_id="project-a",
        type_id="software.issue",
        type_kind="entity",
        label="Issue",
        description="A tracked software issue",
        pack_id="software-change",
        pack_version="1.0.0",
        provenance=evidence("type-source"),
        revision=1,
    )
    repo.put_type(semantic_type, expected_revision=0, at=NOW)
    issue = repo.put_entity(entity("issue-42"), expected_revision=0, at=NOW)
    pull_request = repo.put_entity(entity("pr-9"), expected_revision=0, at=NOW)
    fact = Fact(
        tenant_id="tenant-a",
        project_id="project-a",
        fact_id="fact-1",
        subject_entity_id=issue.entity_id,
        relationship_type_id="software.resolved-by",
        object=FactObject(entity_id=pull_request.entity_id),
        assertion_kind="observation",
        confidence=1,
        valid_from=NOW,
        provenance=evidence("relationship-source"),
        revision=1,
    )
    repo.put_fact(fact, expected_revision=0, at=NOW)
    alias = Alias(
        tenant_id="tenant-a",
        project_id="project-a",
        alias_id="alias-1",
        entity_id=issue.entity_id,
        value="#42",
        normalized_value="42",
        provenance=evidence("alias-source"),
        revision=1,
    )
    repo.put_alias(alias, expected_revision=0, at=NOW)
    assert repo.get("fact", fact.fact_id) == fact
    with pytest.raises(ValueError, match="valid_until"):
        Fact(**{**fact.model_dump(), "valid_until": NOW})


def test_conflicts_are_explicit_and_merge_split_is_reversible():
    repo = repository()
    first = repo.put_entity(entity("issue-42"), expected_revision=0, at=NOW)
    second = repo.put_entity(entity("issue-alias"), expected_revision=0, at=NOW)
    for fact_id, literal in (("open", "open"), ("closed", "closed")):
        repo.put_fact(
            Fact(
                tenant_id="tenant-a",
                project_id="project-a",
                fact_id=fact_id,
                subject_entity_id=first.entity_id,
                relationship_type_id="software.status",
                object=FactObject(literal=literal),
                assertion_kind="claim",
                confidence=0.8,
                provenance=evidence(fact_id),
                revision=1,
            ),
            expected_revision=0,
            at=NOW,
        )
    conflict = Conflict(
        tenant_id="tenant-a",
        project_id="project-a",
        conflict_id="status-conflict",
        fact_ids=("open", "closed"),
        reason="contradictory-value",
        provenance=evidence("conflict-source"),
        revision=1,
    )
    repo.put_conflict(conflict, expected_revision=0, at=NOW)
    merge = repo.merge_entities(
        merge_id="merge-1",
        source_entity_id=second.entity_id,
        target_entity_id=first.entity_id,
        provenance=evidence("merge-source"),
        applied_at=NOW,
    )
    assert repo.get("entity", second.entity_id).merged_into == first.entity_id
    reversed_merge = repo.split_merge(merge.merge_id, reversed_at=NOW)
    assert reversed_merge.state == "reversed"
    assert repo.get("entity", second.entity_id).state == "active"


@pytest.mark.parametrize("adapter", ["memory", "json"])
def test_local_semantic_store_parity(adapter, tmp_path):
    store = (
        MemorySemanticStore()
        if adapter == "memory"
        else JsonSemanticStore(tmp_path / "semantic.json")
    )
    repo = repository(store)
    stored = repo.put_entity(entity("issue-42"), expected_revision=0, at=NOW)
    with pytest.raises(ConcurrencyConflict):
        repo.put_entity(
            stored.model_copy(update={"canonical_name": "stale", "revision": 2}),
            expected_revision=0,
            at=NOW,
        )
    if adapter == "json":
        reopened = repository(JsonSemanticStore(tmp_path / "semantic.json"))
        assert reopened.get("entity", stored.entity_id) == stored


def test_pack_signature_quality_drift_and_immutable_version():
    repo = repository()
    service = SemanticPackService(repo)
    pack = semantic_pack()
    service.publish(pack, expected_revision=0, published_at=NOW)
    with pytest.raises(ConcurrencyConflict):
        service.publish(pack, expected_revision=0, published_at=NOW)
    baseline = service.build_evaluation(
        evaluation_id="eval-1",
        pack=pack,
        metrics=(
            QualityMetric(metric="mapping-coverage", value=0.95, sample_size=100),
            QualityMetric(metric="conflict-rate", value=0.02, sample_size=100),
        ),
        evaluated_at=NOW,
        processing_version="pack-eval/1.0.0",
        processing_digest=DIGEST_A,
    )
    current = service.build_evaluation(
        evaluation_id="eval-2",
        pack=pack,
        metrics=(
            QualityMetric(metric="mapping-coverage", value=0.7, sample_size=100),
            QualityMetric(metric="conflict-rate", value=0.2, sample_size=100),
        ),
        evaluated_at=NOW,
        processing_version="pack-eval/1.0.0",
        processing_digest=DIGEST_A,
        baseline=baseline,
    )
    assert current.drift_codes == ("coverage-regression", "conflict-regression")


def test_agent_draft_rebase_compare_review_and_explicit_publish():
    repo = repository()
    service = OntologyInductionService(repo)
    with pytest.raises(ValueError, match="delegated capability"):
        DraftAuthorship(
            actor_id="agent/untrusted",
            actor_kind="agent",
            policy_digest=DIGEST_B,
        )
    draft = service.build_change_set(
        change_set_id="change-1",
        package_id="software-change",
        operations=(change_operation(),),
        authorship=authorship(agent=True),
        created_at=NOW,
    )
    rebased = service.rebase_draft(
        draft,
        new_change_set_id="change-2",
        new_base_version="1.0.0",
        new_base_digest=DIGEST_A,
        at=NOW,
    )
    comparison = service.compare(draft, rebased)
    assert comparison.added_operation_ids == comparison.removed_operation_ids == ()
    rollback = service.build_rollback_draft(
        change_set_id="rollback-1",
        package_id="software-change",
        published_version="1.0.0",
        published_digest=DIGEST_A,
        inverse_operations=(
            OntologyChangeOperation(
                operation_id="restore-1",
                operation="restore",
                target_kind="concept",
                target_id="software.issue",
                before_digest=DIGEST_B,
                after={"label": "Original Issue"},
            ),
        ),
        authorship=authorship(),
        created_at=NOW,
    )
    assert rollback.rollback_of_version == "1.0.0"
    assert rollback.state == "draft"
    observation = ObservedStructure(
        tenant_id="tenant-a",
        project_id="project-a",
        observation_id="observation-1",
        source=evidence().evidence[0],
        structure_kind="record",
        path="$.issue",
        sample_digest=DIGEST_A,
        occurrence_count=10,
        provenance=evidence("observed-source"),
    )
    concept = CandidateConcept(
        candidate_id="concept-1",
        type_id="software.issue",
        label="Issue",
        description="Tracked issue",
        observation_ids=(observation.observation_id,),
        confidence=0.9,
    )
    version = service.build_version(
        package_id="software-change",
        version="1.1.0",
        observations=(observation,),
        concepts=(concept,),
        change_set=rebased,
        provenance=evidence("version-source"),
    )
    package = service.propose(version, rebased, proposed_at=NOW)
    with pytest.raises(ConcurrencyConflict, match="only reviewed"):
        service.publish(
            package.package_id,
            reviewed_content_digest=version.content_digest,
            at=NOW,
        )
    service.begin_review(package.package_id, at=NOW)
    reviewed = service.review(
        package.package_id,
        reviewer="user/reviewer",
        review_digest=DIGEST_B,
        accepted=True,
        at=NOW,
    )
    assert reviewed.state == "reviewed"
    with pytest.raises(ConcurrencyConflict, match="stale"):
        service.publish(
            package.package_id, reviewed_content_digest=DIGEST_A, at=NOW
        )
    published = service.publish(
        package.package_id,
        reviewed_content_digest=version.content_digest,
        at=NOW,
    )
    assert published.state == "published"
    assert published.published_version == version.version


def test_blocking_findings_and_open_questions_prevent_review_acceptance():
    repo = repository()
    service = OntologyInductionService(repo)
    draft = service.build_change_set(
        change_set_id="blocked-change",
        package_id="software-change",
        operations=(change_operation(),),
        authorship=authorship(),
        created_at=NOW,
    )
    observation = ObservedStructure(
        tenant_id="tenant-a",
        project_id="project-a",
        observation_id="observation-1",
        source=evidence().evidence[0],
        structure_kind="field",
        path="$.status",
        sample_digest=DIGEST_A,
        occurrence_count=1,
        provenance=evidence(),
    )
    version = service.build_version(
        package_id="software-change",
        version="2.0.0",
        observations=(observation,),
        findings=(
            ValidationFinding(
                finding_id="finding-1",
                severity="blocking",
                code="ambiguous-status",
                message="Status mapping is ambiguous",
                evidence=evidence().evidence,
            ),
        ),
        reviewer_questions=(
            ReviewerQuestion(question_id="question-1", prompt="Which status wins?"),
        ),
        change_set=draft,
        provenance=evidence(),
    )
    package = service.propose(version, draft, proposed_at=NOW)
    service.begin_review(package.package_id, at=NOW)
    with pytest.raises(ValueError, match="blocking"):
        service.review(
            package.package_id,
            reviewer="user/reviewer",
            review_digest=DIGEST_B,
            accepted=True,
            at=NOW,
        )


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.connection.statements.append((sql, params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_postgres_semantic_store_uses_scoped_cas_table():
    connection = FakeConnection()
    store = PostgresSemanticStore(
        "postgresql://unused", connect=lambda dsn: connection
    )
    repo = repository(store)
    repo.put_entity(entity("issue-42"), expected_revision=0, at=NOW)
    sql = "\n".join(statement for statement, _ in connection.statements)
    assert "krail_semantic_record" in sql
    assert "tenant_id,project_id,record_kind,record_id" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert connection.committed and not connection.rolled_back
