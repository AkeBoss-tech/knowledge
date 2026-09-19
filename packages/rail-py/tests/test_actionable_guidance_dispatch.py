from datetime import UTC, datetime, timedelta
from hashlib import sha256
import pytest

from krail.provider.v1 import ResourceRef
from rail.capability_publication import (
    PROCEDURE_EXPLANATION_CAPABILITY_ID,
    PROCEDURE_EXPLANATION_CAPABILITY_VERSION,
    procedure_explanation_descriptor,
)
from rail.core_provenance import (
    CoreProvenanceRepository,
    CoreProvenanceService,
    ProcedureActionableGuidanceRequest,
    ProcedureExplanationRequest,
    ProcedureExplanationService,
    ProcedureReviewService,
    create_core_provenance_receipt,
    procedure_record_ref,
)
from rail.hosted.access import (
    AccessClaims,
    AccessContextAuthority,
    MemoryRevocationRegistry,
)
from rail.knowledge import KnowledgeRuntime
from rail.semantic.operations import SemanticOperationsService
from rail.semantic.repository import JsonSemanticStore, SemanticRepository


NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def ref(resource_type: str, resource_id: str, version: str = "1") -> ResourceRef:
    return ResourceRef(
        authority="opensaddle://core",
        resource_type=resource_type,
        resource_id=resource_id,
        version=version,
        digest="sha256:" + sha256(f"{resource_id}:{version}".encode()).hexdigest(),
    )


class Allow:
    def authorize(self, resource, *, at=None):
        return None


class Trust:
    def verify(self, receipt):
        return None


class Review:
    def authorize_review(
        self, candidate_digest, reviewer_ref, evidence_refs, *, lineage_refs=(), at
    ):
        return None


class Invalidate:
    def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
        return None


def configured_dispatch(tmp_path):
    path = tmp_path / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant", project_id="project")
    receipt = create_core_provenance_receipt(
        receipt_id="receipt:guidance",
        command_ref=ref("command", "opensaddle/command/payment-response"),
        environment_ref=ref(
            "environment", "opensaddle/environment-revision/payments"
        ),
        observed_at=NOW,
    )
    candidate = CoreProvenanceService(
        repository=repository, clock=lambda: NOW
    ).ingest(receipt, authorizer=Allow(), trust=Trust())
    reviewer = ResourceRef(
        authority="https://control.example.test",
        resource_type="reviewer",
        resource_id="reviewer/operations",
        version="1",
        digest="sha256:" + sha256(b"reviewer").hexdigest(),
    )
    evidence = ref("evidence", "incident/payment-lag")
    reviewed = ProcedureReviewService(
        repository=repository, clock=lambda: NOW
    ).review(
        candidate.record.record_digest,
        decision_id="review:guidance",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=Review(),
    )
    assert reviewed.promoted_record is not None
    promoted = reviewed.promoted_record
    decision_ref = ResourceRef(
        authority="krail://procedural-memory",
        resource_type="procedure-review",
        resource_id=reviewed.decision.decision_id,
        version=reviewed.decision.schema_version,
        digest=reviewed.decision.decision_digest,
    )
    exact_refs = tuple(dict.fromkeys((
        procedure_record_ref(candidate.record),
        procedure_record_ref(promoted),
        *candidate.record.command_refs,
        *candidate.record.environment_refs,
        *promoted.command_refs,
        *promoted.environment_refs,
        *promoted.test_evidence_refs,
        reviewer,
        promoted.review_ref,
        decision_ref,
    )))
    exact_refs = tuple(item for item in exact_refs if item is not None)
    descriptor = procedure_explanation_descriptor()
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"key": b"consumer-read-key"},
        issuer="https://control.example.test",
        revocations=revocations,
        required_capability_id=PROCEDURE_EXPLANATION_CAPABILITY_ID,
        required_capability_digest=descriptor.descriptor_digest,
    )

    def request(
        refs=exact_refs,
        source_refs=None,
        candidate_digest=candidate.record.record_digest,
        nonce="guidance",
        capability_version=PROCEDURE_EXPLANATION_CAPABILITY_VERSION,
        tenant_id="tenant",
        project_id="project",
        issued_at=NOW,
        not_before=NOW,
        expires_at=NOW + timedelta(hours=1),
    ):
        authorized_source_refs = refs if source_refs is None else source_refs
        claims = AccessClaims(
            issuer="https://control.example.test",
            tenant_id=tenant_id,
            project_id=project_id,
            subject="consumer/core",
            delegator="user/operations",
            delegation_id=f"delegation/{nonce}",
            capability_id=PROCEDURE_EXPLANATION_CAPABILITY_ID,
            capability_version=capability_version,
            capability_digest=descriptor.descriptor_digest,
            actions=("context.read",),
            source_ids=tuple(
                dict.fromkeys(item.resource_id for item in authorized_source_refs)
            ),
            classifications=("internal",),
            policy_digest="sha256:" + "a" * 64,
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
            nonce=nonce,
        )
        return ProcedureActionableGuidanceRequest(
            procedure=ProcedureExplanationRequest(
                candidate_digest=candidate_digest
            ),
            access_context=authority.issue(claims, key_id="key"),
            exact_refs=refs,
        )

    semantic_repository = SemanticRepository(
        JsonSemanticStore(path), tenant_id="tenant", project_id="project"
    )
    operations = SemanticOperationsService(
        semantic_repository,
        cursor_key=b"procedure-guidance-cursor-key-32b",
        authorize_scope=lambda scope: None,
        procedure_guidance=ProcedureExplanationService(
            repository=repository, clock=lambda: NOW
        ),
        procedure_access_authority=authority,
        procedure_capability_digest=descriptor.descriptor_digest,
        procedure_clock=lambda: NOW,
    )
    runtime = KnowledgeRuntime(tmp_path)
    runtime.application.semantic_operations = operations
    provider = runtime.provider
    return (
        provider,
        repository,
        candidate,
        reviewed,
        exact_refs,
        request,
        authority,
        revocations,
    )


def test_published_actionable_guidance_dispatches_signed_read_and_abstention(tmp_path):
    descriptor = procedure_explanation_descriptor()
    operations = {item.operation_id: item for item in descriptor.operations}
    assert descriptor.semantic_version == PROCEDURE_EXPLANATION_CAPABILITY_VERSION
    assert "actionable_guidance" in operations
    assert operations["actionable_guidance"].input_schema["additionalProperties"] is False
    assert operations["actionable_guidance"].output_schema["additionalProperties"] is False

    (
        provider,
        repository,
        candidate,
        reviewed,
        exact_refs,
        request,
        authority,
        revocations,
    ) = configured_dispatch(tmp_path)
    result = provider.semantic_operation("actionable_guidance", request())
    assert result.status == "guidance"
    assert result.abstention_reason is None
    assert result.guidance is not None
    assert result.guidance.candidate_ref == procedure_record_ref(candidate.record)
    assert result.guidance.reviewed_ref == procedure_record_ref(reviewed.promoted_record)
    assert result.guidance.evidence_refs == reviewed.promoted_record.test_evidence_refs

    candidate_ref = procedure_record_ref(candidate.record)
    reviewed_ref = procedure_record_ref(reviewed.promoted_record)
    for missing, nonce in (
        (candidate_ref, "candidate-ref-current"),
        (reviewed_ref, "review-ref-current"),
    ):
        restricted_refs = tuple(item for item in exact_refs if item != missing)
        with pytest.raises(PermissionError, match="actionable guidance access denied"):
            provider.semantic_operation(
                "actionable_guidance",
                request(restricted_refs, source_refs=exact_refs, nonce=nonce),
            )

    invalidation = repository.record_invalidation(
        event_id="invalidate:guidance",
        changed_ref=reviewed.promoted_record.test_evidence_refs[0],
        reason="incident evidence replaced",
        at=NOW,
        authorizer=Invalidate(),
    )
    invalidation_ref = ResourceRef(
        authority="krail://procedural-memory",
        resource_type="procedure-invalidation",
        resource_id=invalidation.event_id,
        version=invalidation.schema_version,
        digest=invalidation.event_digest,
    )
    abstained = provider.semantic_operation(
        "actionable_guidance",
        request((*exact_refs, invalidation_ref), nonce="stale"),
    )
    assert abstained.status == "abstained"
    assert abstained.guidance is None
    assert abstained.abstention_reason == "not-current"

    stale_refs = (*exact_refs, invalidation_ref)
    for missing, nonce in (
        (candidate_ref, "candidate-ref-stale"),
        (reviewed_ref, "review-ref-stale"),
    ):
        restricted_refs = tuple(item for item in stale_refs if item != missing)
        with pytest.raises(PermissionError, match="actionable guidance access denied"):
            provider.semantic_operation(
                "actionable_guidance",
                request(restricted_refs, source_refs=stale_refs, nonce=nonce),
            )

    restricted = (exact_refs[0],)
    with pytest.raises(PermissionError, match="actionable guidance access denied"):
        provider.semantic_operation(
            "actionable_guidance", request(restricted, nonce="denied")
        )
    with pytest.raises(PermissionError, match="actionable guidance access denied"):
        provider.semantic_operation(
            "actionable_guidance",
            request(nonce="wrong-version", capability_version="1.0.0"),
        )

    for scope in (
        {"tenant_id": "other"},
        {"project_id": "other"},
        {
            "issued_at": NOW - timedelta(hours=2),
            "not_before": NOW - timedelta(hours=2),
            "expires_at": NOW - timedelta(seconds=1),
        },
    ):
        with pytest.raises(PermissionError, match="actionable guidance access denied"):
            provider.semantic_operation(
                "actionable_guidance", request(nonce=f"denied-{len(scope)}", **scope)
            )

    signed = request(nonce="tampered")
    tampered = signed.model_copy(
        update={
            "access_context": signed.access_context.model_copy(
                update={"signature": "sha256:" + "0" * 64}
            )
        }
    )
    with pytest.raises(PermissionError, match="actionable guidance access denied"):
        provider.semantic_operation("actionable_guidance", tampered)

    revoked = request(nonce="revoked")
    revocations.revoke_context(revoked.access_context.context_digest, revoked_at=NOW)
    with pytest.raises(PermissionError, match="actionable guidance access denied"):
        provider.semantic_operation("actionable_guidance", revoked)

    unreviewed = CoreProvenanceService(
        repository=repository, clock=lambda: NOW
    ).ingest(
        create_core_provenance_receipt(
            receipt_id="receipt:unreviewed",
            command_ref=ref("command", "opensaddle/command/unreviewed"),
            environment_ref=ref(
                "environment", "opensaddle/environment-revision/unreviewed"
            ),
            observed_at=NOW,
        ),
        authorizer=Allow(),
        trust=Trust(),
    ).record
    unreviewed_refs = (
        procedure_record_ref(unreviewed),
        *unreviewed.command_refs,
        *unreviewed.environment_refs,
    )
    with pytest.raises(PermissionError, match="actionable guidance access denied"):
        provider.semantic_operation(
            "actionable_guidance",
            request(
                unreviewed_refs[1:],
                source_refs=unreviewed_refs,
                candidate_digest=unreviewed.record_digest,
                nonce="unreviewed-candidate-ref",
            ),
        )


def test_default_runtime_dispatch_reports_unavailable_without_private_reads(
    tmp_path, monkeypatch
):
    runtime = KnowledgeRuntime(tmp_path)
    operations = runtime.application.semantic_operations

    def private_read(*args, **kwargs):
        raise AssertionError("unavailable dispatch accessed private records")

    monkeypatch.setattr(operations.repository.store, "get", private_read)
    monkeypatch.setattr(operations.repository.store, "list", private_read)
    authority = AccessContextAuthority(
        {"key": b"unused-read-key"}, issuer="https://control.example.test"
    )
    claims = AccessClaims(
        issuer="https://control.example.test",
        tenant_id="local",
        project_id=tmp_path.name,
        subject="consumer/core",
        delegator="user/operations",
        delegation_id="delegation/unconfigured",
        capability_id=PROCEDURE_EXPLANATION_CAPABILITY_ID,
        capability_version=PROCEDURE_EXPLANATION_CAPABILITY_VERSION,
        capability_digest=procedure_explanation_descriptor().descriptor_digest,
        actions=("context.read",),
        source_ids=("private/candidate",),
        classifications=("internal",),
        policy_digest="sha256:" + "a" * 64,
        issued_at=NOW,
        not_before=NOW,
        expires_at=NOW + timedelta(hours=1),
        nonce="unconfigured",
    )
    request = ProcedureActionableGuidanceRequest(
        procedure=ProcedureExplanationRequest(candidate_digest="sha256:" + "b" * 64),
        access_context=authority.issue(claims, key_id="key"),
        exact_refs=(ref("procedure-record", "private/candidate"),),
    )
    result = runtime.provider.semantic_operation("actionable_guidance", request)
    assert result.status == "unavailable"
    assert result.guidance is None
    assert result.abstention_reason is None
    assert result.unavailable_reason == "not-configured"
