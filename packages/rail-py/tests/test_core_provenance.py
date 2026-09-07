from datetime import UTC, datetime
from hashlib import sha256
from threading import Event, Thread
import multiprocessing
import os

import pytest

from krail.provider.v1 import ResourceRef
from rail.core_provenance import (
    CoreProvenanceReceipt,
    CoreProvenanceRepository,
    CoreProvenanceService,
    create_invalidation_event,
    ProcedureExplanationRequest,
    ProcedureExplanationService,
    ProcedureReviewService,
    create_core_provenance_receipt,
)
from rail.semantic.repository import JsonSemanticStore, SemanticRow
from rail.procedural_memory import create_procedure, procedure_temporal_history
from rail.procedure_projection import TemporalProjectionService
from rail.temporal_records import query_temporal_records
from rail.authorized_context import (
    HostedAccessContextAuthorizer,
    HostedProcedureInvalidationAuthorizer,
    HostedAccessContextAuthorizer,
    HostedProcedureReviewAuthorizer,
    PROCEDURE_REVIEW_CAPABILITY_ID,
    PROCEDURE_REVIEW_CAPABILITY_VERSION,
    PROCEDURE_INVALIDATION_CAPABILITY_ID,
    PROCEDURE_INVALIDATION_CAPABILITY_VERSION,
)
from rail.hosted.access import AccessClaims, AccessContextAuthority, MemoryRevocationRegistry


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _ref(resource_type: str, resource_id: str, version: str, payload: str) -> ResourceRef:
    return ResourceRef(
        authority="opensaddle://core",
        resource_type=resource_type,
        resource_id=resource_id,
        version=version,
        digest="sha256:" + sha256(payload.encode()).hexdigest(),
    )


def _receipt(**changes) -> CoreProvenanceReceipt:
    values = {
        "receipt_id": "receipt:run-1",
        "command_ref": _ref("command", "opensaddle/command/dev.review", "1", "descriptor"),
        "environment_ref": _ref("environment", "opensaddle/environment-revision/project-a", "7", "definition"),
        "observed_at": NOW,
    }
    values.update(changes)
    return create_core_provenance_receipt(**values)


class Allow:
    def __init__(self):
        self.calls = []

    def authorize(self, ref, *, at=None):
            self.calls.append(ref.exact_key)


class AllowReview:
    def authorize_review(self, candidate_digest, reviewer_ref, evidence_refs, *, lineage_refs=(), at):
        return None


class AllowInvalidation:
    def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
        return None


class AllowProjectionWriter:
    def authorize(self, record, *, at):
        return None


class AllowTrust:
    def verify(self, receipt):
        return None


def _process_ingest(path, receipt_id, barrier, output):
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    barrier.wait()
    try:
        CoreProvenanceService(repository=repository).ingest(
            _receipt(receipt_id=receipt_id), authorizer=Allow(), trust=AllowTrust()
        )
        output.put(None)
    except Exception as exc:  # pragma: no cover - parent reports concrete error
        output.put(repr(exc))


def _process_crash_holding_lock(lock_path, ready):
    import fcntl
    from pathlib import Path

    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        os._exit(0)


class DenyAfterFirst:
    def __init__(self):
        self.calls = 0

    def authorize(self, ref, *, at=None):
        self.calls += 1
        if self.calls > 2:
            raise PermissionError("revoked")


def test_core_receipt_ingests_exact_command_and_environment_refs_as_observed_procedure() -> None:
    service = CoreProvenanceService()
    authorizer = Allow()
    receipt = _receipt()

    ingested = service.ingest(receipt, authorizer=authorizer, trust=AllowTrust())

    assert ingested.record.lifecycle == "desired"
    assert ingested.record.command_refs == (receipt.command_ref,)
    assert ingested.record.environment_refs == (receipt.environment_ref,)
    assert ingested.record.activation_ref is None
    assert len(authorizer.calls) == 4  # precheck and recheck for both exact refs


def test_core_receipt_is_idempotent_and_conflicting_replay_is_rejected() -> None:
    service = CoreProvenanceService()
    receipt = _receipt()
    first = service.ingest(receipt, authorizer=Allow(), trust=AllowTrust())
    assert service.ingest(receipt, authorizer=Allow(), trust=AllowTrust()) == first

    conflicting = _receipt(
        environment_ref=_ref(
            "environment", "opensaddle/environment-revision/project-a", "8", "new-definition"
        )
    )
    with pytest.raises(ValueError, match="conflicting receipt"):
        service.ingest(conflicting, authorizer=Allow(), trust=AllowTrust())


def test_core_receipt_rechecks_authorization_before_exposure() -> None:
    with pytest.raises(PermissionError, match="provenance access denied"):
        CoreProvenanceService().ingest(_receipt(), authorizer=DenyAfterFirst(), trust=AllowTrust())


def test_core_receipt_requires_authenticated_trusted_origin() -> None:
    class Untrusted:
        def verify(self, receipt):
            raise PermissionError("untrusted payload")

    with pytest.raises(PermissionError, match="origin is not authenticated"):
        CoreProvenanceService().ingest(_receipt(), authorizer=Allow(), trust=Untrusted())


def test_procedure_review_rechecks_current_authorization_before_exposure(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    receipt = _receipt()
    ingested = CoreProvenanceService(repository=repository).ingest(
        receipt, authorizer=Allow(), trust=AllowTrust()
    )
    reviewer = _ref("reviewer", "reviewer/1", "1", "reviewer")
    evidence = _ref("evidence", "test/review", "1", "evidence")

    class RevokeAfterPrecheck:
        def __init__(self):
            self.calls = 0

        def authorize(self, ref, *, at=None):
            self.calls += 1
            if self.calls > 4:
                raise PermissionError("revoked")

    with pytest.raises(PermissionError, match="procedure review access denied"):
        ProcedureReviewService(repository=repository).review(
            ingested.record.record_digest,
            decision_id="review:run-1",
            reviewer_ref=reviewer,
            evidence_refs=(evidence,),
            accepted=True,
            authorizer=RevokeAfterPrecheck(),
            review_authorizer=AllowReview(),
        )


def test_procedure_review_promotes_or_rejects_durably_and_is_idempotent(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    reviewer = _ref("reviewer", "reviewer/1", "1", "reviewer")
    evidence = _ref("evidence", "test/review", "1", "evidence")
    service = ProcedureReviewService(repository=repository, clock=lambda: NOW)
    accepted = service.review(
        ingested.record.record_digest,
        decision_id="review:accepted",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=AllowReview(),
    )
    assert accepted.decision.outcome == "accepted"
    assert accepted.decision.candidate_digest == ingested.record.record_digest
    assert accepted.promoted_record is not None
    assert accepted.promoted_record.lifecycle == "reviewed"
    assert accepted.promoted_record.test_evidence_refs == (evidence,)
    assert accepted.promoted_record.activation_ref is None
    temporal = procedure_temporal_history([ingested.record, accepted.promoted_record])
    assert [item.revision for item in query_temporal_records(
        temporal, valid_at=NOW, known_at=NOW.replace(hour=23)
    )] == [accepted.promoted_record.procedure_version]
    assert service.review(
        ingested.record.record_digest,
        decision_id="review:accepted",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=AllowReview(),
    ) == accepted
    with pytest.raises(ValueError, match="conflicting review identifier"):
        service.review(
            ingested.record.record_digest,
            decision_id="review:accepted",
            reviewer_ref=reviewer,
            evidence_refs=(evidence,),
            accepted=False,
            authorizer=Allow(),
            review_authorizer=AllowReview(),
        )
    rejected = service.review(
        ingested.record.record_digest,
        decision_id="review:rejected",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=False,
        authorizer=Allow(),
        review_authorizer=AllowReview(),
    )
    assert rejected.promoted_record is None
    restarted = ProcedureReviewService(
        repository=CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a"),
        clock=lambda: NOW,
    )
    assert restarted.review(
        ingested.record.record_digest,
        decision_id="review:accepted",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=AllowReview(),
    ) == accepted
    with pytest.raises(ValueError, match="conflicting review identifier"):
        restarted.review("sha256:" + "f" * 64, decision_id="review:accepted", reviewer_ref=reviewer, evidence_refs=(evidence,), accepted=True, authorizer=Allow(), review_authorizer=AllowReview())
    with pytest.raises(ValueError, match="stale or unavailable"):
        restarted.review("sha256:" + "e" * 64, decision_id="review:stale", reviewer_ref=reviewer, evidence_refs=(evidence,), accepted=True, authorizer=Allow(), review_authorizer=AllowReview())


def test_core_review_and_explanation_share_persisted_temporal_projection(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    projection = TemporalProjectionService(str(path), tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW)
    writer = AllowProjectionWriter()
    ingested = CoreProvenanceService(
        repository=repository, clock=lambda: NOW, projection=projection, projection_writer=writer,
    ).ingest(_receipt(), authorizer=Allow(), trust=AllowTrust())
    reviewed = ProcedureReviewService(
        repository=repository, clock=lambda: NOW, projection=projection, projection_writer=writer,
    ).review(
        ingested.record.record_digest, decision_id="review:projection", reviewer_ref=_ref("reviewer", "reviewer/projection", "1", "reviewer"),
        evidence_refs=(_ref("evidence", "test/projection", "1", "evidence"),), accepted=True,
        authorizer=Allow(), review_authorizer=AllowReview(),
    )
    assert reviewed.promoted_record is not None
    base_temporal, reviewed_temporal = procedure_temporal_history([ingested.record, reviewed.promoted_record])
    assert tuple(item.digest for item in projection.affected_region(projection.record_ref(base_temporal))) == (reviewed_temporal.record_digest,)
    before = ProcedureExplanationService(repository=repository, clock=lambda: NOW, projection=projection).explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest), authorizer=Allow()
    )
    assert (before.review_status, before.support_status) == ("accepted", "current")
    repository.record_invalidation(
        event_id="invalidation:temporal-parent", changed_ref=projection.record_ref(base_temporal),
        reason="parent temporal revision invalidated", at=NOW, authorizer=AllowInvalidation(),
    )
    # Simulate interruption after canonical event publication; idempotent replay
    # must repair the disposable projection rather than return silently.
    repository.record_invalidation(
        event_id="invalidation:temporal-parent", changed_ref=projection.record_ref(base_temporal),
        reason="parent temporal revision invalidated", at=NOW, authorizer=AllowInvalidation(),
        projection=projection,
    )
    projected = {state.entity_id: state for state in projection.current_state("core-provenance")}
    assert any(item.status == "stale" for item in projected[ingested.record.procedure_id].dependency_states)
    explained = ProcedureExplanationService(
        repository=repository, clock=lambda: NOW, projection=projection,
    ).explain(ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest), authorizer=Allow())
    assert explained.support_status == "stale"
    assert "temporal dependency projection" in explained.gaps[-1]
    reopened = TemporalProjectionService(str(path), tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW)
    replayed = ProcedureExplanationService(repository=CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a"), clock=lambda: NOW, projection=reopened).explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest), authorizer=Allow()
    )
    assert replayed.support_status == "stale"


def test_core_receipt_replay_repairs_interrupted_projection_publish(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    receipt = _receipt(receipt_id="receipt:projection-replay")
    first = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
        receipt, authorizer=Allow(), trust=AllowTrust()
    )
    projection = TemporalProjectionService(str(path), tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW)
    replayed = CoreProvenanceService(
        repository=repository, clock=lambda: NOW, projection=projection, projection_writer=AllowProjectionWriter(),
    ).ingest(receipt, authorizer=Allow(), trust=AllowTrust())
    assert replayed == first
    assert projection.current_state("core-provenance")[0].record_digests == (first.temporal_record.record_digest,)

def test_procedure_review_requires_authenticated_review_action(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    reviewer = _ref("reviewer", "reviewer/1", "1", "reviewer")
    evidence = _ref("evidence", "test/review", "1", "evidence")

    class ReadableOnly:
        def authorize(self, ref, *, at=None):
            return None

        def authorize_review(self, candidate_digest, reviewer_ref, evidence_refs, *, lineage_refs=(), at):
            raise PermissionError("review role missing")

    with pytest.raises(PermissionError, match="procedure review action denied"):
        ProcedureReviewService(repository=repository).review(
            ingested.record.record_digest,
            decision_id="review:unauthorized",
            reviewer_ref=reviewer,
            evidence_refs=(evidence,),
            accepted=True,
            authorizer=ReadableOnly(),
            review_authorizer=ReadableOnly(),
        )


def test_procedure_review_replay_rechecks_full_promoted_lineage(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    reviewer = _ref("reviewer", "reviewer/1", "1", "reviewer")
    evidence = _ref("evidence", "test/review", "1", "evidence")
    service = ProcedureReviewService(repository=repository)
    service.review(
        ingested.record.record_digest,
        decision_id="review:replay",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=AllowReview(),
    )

    class CommandRevoked:
        def authorize(self, ref, *, at=None):
            if ref.resource_type == "command":
                raise PermissionError("command revoked")

    with pytest.raises(PermissionError, match="procedure review access denied"):
        ProcedureReviewService(repository=repository).review(
            ingested.record.record_digest,
            decision_id="review:replay",
            reviewer_ref=reviewer,
            evidence_refs=(evidence,),
            accepted=True,
            authorizer=CommandRevoked(),
            review_authorizer=AllowReview(),
        )


def test_procedure_review_rejects_independently_valid_substituted_promotion(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    first = CoreProvenanceService(repository=repository).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    second = CoreProvenanceService(repository=repository).ingest(
        _receipt(
            receipt_id="receipt:run-2",
            environment_ref=_ref("environment", "opensaddle/environment-revision/project-a", "8", "definition-8"),
        ),
        authorizer=Allow(),
        trust=AllowTrust(),
    )
    reviewer = _ref("reviewer", "reviewer/1", "1", "reviewer")
    evidence = _ref("evidence", "test/review", "1", "evidence")
    service = ProcedureReviewService(repository=repository)
    service.review(
        second.record.record_digest,
        decision_id="review:other",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=AllowReview(),
    )
    first_result = service.review(
        first.record.record_digest,
        decision_id="review:first",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=AllowReview(),
    )
    row = repository.store.get("tenant-a", "project-a", "procedure_review", "review:first")
    other = repository.store.get("tenant-a", "project-a", "procedure_review", "review:other")
    with repository.store.transaction():
        repository.store.put(
            row.model_copy(update={"payload": {**row.payload, "promoted_record": other.payload["promoted_record"]}, "revision": row.revision + 1}),
            expected_revision=row.revision,
        )
    with pytest.raises(ValueError, match="not derived from its candidate"):
        repository.load_review("review:first")


def test_signed_review_adapter_requires_versioned_action_and_exact_scope(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    command, environment = ingested.record.command_refs[0], ingested.record.environment_refs[0]
    evidence = _ref("evidence", "test/review", "1", "evidence")
    reviewer = ResourceRef(
        authority="https://control.example.test",
        resource_type="reviewer",
        resource_id="agent/worker-7",
        version="1",
        digest="sha256:" + sha256(b"reviewer").hexdigest(),
    )
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"key": b"review-key"},
        issuer="https://control.example.test",
        revocations=revocations,
        required_capability_id=PROCEDURE_REVIEW_CAPABILITY_ID,
    )
    claims = AccessClaims(
        issuer="https://control.example.test",
        tenant_id="tenant-a",
        project_id="project-a",
        subject="agent/worker-7",
        delegator="user/alice",
        delegation_id="delegation/review",
        capability_id=PROCEDURE_REVIEW_CAPABILITY_ID,
        capability_version=PROCEDURE_REVIEW_CAPABILITY_VERSION,
        capability_digest="sha256:" + "a" * 64,
        actions=("procedure.review",),
        source_ids=(command.resource_id, environment.resource_id, evidence.resource_id, reviewer.resource_id),
        classifications=("internal",),
        policy_digest="sha256:" + "b" * 64,
        issued_at=NOW,
        not_before=NOW,
        expires_at=NOW.replace(hour=23),
        nonce="review-nonce",
    )
    context = authority.issue(claims, key_id="key")
    action = HostedProcedureReviewAuthorizer(
        authority,
        context,
        tenant_id="tenant-a",
        project_id="project-a",
        exact_refs=(command, environment, evidence, reviewer),
        allowed_candidate_digests=(ingested.record.record_digest,),
        capability_digest="sha256:" + "a" * 64,
        clock=lambda: NOW,
    )
    result = ProcedureReviewService(repository=repository, clock=lambda: NOW).review(
        ingested.record.record_digest,
        decision_id="review:signed",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=action,
    )
    assert result.promoted_record is not None

    read_only_claims = claims.model_copy(
        update={"actions": ("context.read",), "nonce": "read-only"}
    )
    read_only = HostedProcedureReviewAuthorizer(
        authority,
        authority.issue(read_only_claims, key_id="key"),
        tenant_id="tenant-a",
        project_id="project-a",
        exact_refs=(command, environment, evidence, reviewer),
        allowed_candidate_digests=(ingested.record.record_digest,),
        capability_digest="sha256:" + "a" * 64,
        clock=lambda: NOW,
    )
    with pytest.raises(PermissionError, match="procedure review action denied"):
        read_only.authorize_review(
            ingested.record.record_digest,
            reviewer,
            (evidence,),
            lineage_refs=(command, environment),
            at=NOW,
        )
    changed_values = ingested.record.model_dump(mode="python")
    changed_values.pop("record_digest")
    changed_values["rationale"] = "Changed candidate rationale"
    changed_candidate = create_procedure(**changed_values)
    assert changed_candidate.record_digest != ingested.record.record_digest
    with pytest.raises(PermissionError, match="procedure review action denied"):
        action.authorize_review(
            changed_candidate.record_digest,
            reviewer,
            (evidence,),
            lineage_refs=(command, environment),
            at=NOW,
        )

    changed_capability = HostedProcedureReviewAuthorizer(
        authority,
        authority.issue(
            claims.model_copy(
                update={
                    "capability_digest": "sha256:" + "d" * 64,
                    "nonce": "changed-capability",
                }
            ),
            key_id="key",
        ),
        tenant_id="tenant-a",
        project_id="project-a",
        exact_refs=(command, environment, evidence, reviewer),
        allowed_candidate_digests=(ingested.record.record_digest,),
        capability_digest="sha256:" + "a" * 64,
        clock=lambda: NOW,
    )
    with pytest.raises(PermissionError, match="procedure review action denied"):
        changed_capability.authorize_review(
            ingested.record.record_digest,
            reviewer,
            (evidence,),
            lineage_refs=(command, environment),
            at=NOW,
        )
    revocations.revoke_delegation("delegation/review", revoked_at=NOW)
    with pytest.raises(PermissionError, match="procedure review action denied"):
        action.authorize_review(
            ingested.record.record_digest,
            reviewer,
            (evidence,),
            lineage_refs=(command, environment),
            at=NOW,
        )


def test_signed_read_explains_persisted_review_history_without_review_permission(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    reviewer = _ref("reviewer", "reviewer/1", "1", "reviewer")
    evidence = _ref("evidence", "test/review", "1", "evidence")
    accepted = ProcedureReviewService(repository=repository, clock=lambda: NOW).review(
        ingested.record.record_digest,
        decision_id="review:explain",
        reviewer_ref=reviewer,
        evidence_refs=(evidence,),
        accepted=True,
        authorizer=Allow(),
        review_authorizer=AllowReview(),
    )
    assert accepted.promoted_record is not None
    promoted = accepted.promoted_record
    decision_ref = ResourceRef(
        authority="krail://procedural-memory",
        resource_type="procedure-review",
        resource_id=accepted.decision.decision_id,
        version=accepted.decision.schema_version,
        digest=accepted.decision.decision_digest,
    )
    refs = (*ingested.record.command_refs, *ingested.record.environment_refs, reviewer, evidence,
            *promoted.command_refs, *promoted.environment_refs, promoted.review_ref, decision_ref)
    refs = tuple(ref for ref in refs if ref is not None)
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority({"key": b"read-key"}, issuer="https://read.example.test", revocations=revocations)
    claims = AccessClaims(
        issuer="https://read.example.test", tenant_id="tenant-a", project_id="project-a",
        subject="reader/1", delegator="user/alice", delegation_id="delegation/read",
        capability_id="krail.context-brief", capability_version="1.0.0",
        capability_digest="sha256:" + "c" * 64, actions=("context.read",),
        source_ids=tuple(dict.fromkeys(ref.resource_id for ref in refs)), classifications=("internal",),
        policy_digest="sha256:" + "d" * 64, issued_at=NOW, not_before=NOW,
        expires_at=NOW.replace(hour=23), nonce="read-nonce",
    )
    authorizer = HostedAccessContextAuthorizer(
        authority, authority.issue(claims, key_id="key"), exact_refs=refs, clock=lambda: NOW
    )
    service = ProcedureExplanationService(repository=repository, clock=lambda: NOW)
    explained = service.explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest),
        authorizer=authorizer,
    )
    assert explained.review_status == "accepted"
    assert explained.lifecycle == "reviewed"
    assert explained.activation_status == "not_observed"
    assert explained.reviewed == promoted
    assert explained.decision_refs == (decision_ref,)
    assert explained.candidate.rationale == ingested.record.rationale
    denied_reviewer = HostedAccessContextAuthorizer(
        authority,
        authorizer.context,
        exact_refs=tuple(ref for ref in refs if ref != reviewer),
        clock=lambda: NOW,
    )
    with pytest.raises(PermissionError, match="procedure explanation access denied"):
        service.explain(
            ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest),
            authorizer=denied_reviewer,
        )
    rows_before = tuple(repository.store.list("tenant-a", "project-a"))
    restarted = ProcedureExplanationService(
        repository=CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a"),
        clock=lambda: NOW,
    )
    assert restarted.explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest),
        authorizer=authorizer,
    ) == explained
    with pytest.raises(PermissionError, match="procedure explanation access denied"):
        restarted.explain(
            ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest),
            authorizer=denied_reviewer,
        )
    assert tuple(repository.store.list("tenant-a", "project-a")) == rows_before
    revocations.revoke_delegation("delegation/read", revoked_at=NOW)
    with pytest.raises(PermissionError, match="procedure explanation access denied"):
        service.explain(
            ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest),
            authorizer=authorizer,
        )


def test_procedure_explanation_reports_missing_and_conflicting_review_support(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    service = ProcedureExplanationService(repository=repository, clock=lambda: NOW)
    missing = service.explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest),
        authorizer=Allow(),
    )
    assert missing.review_status == "missing"
    assert missing.support_status == "missing"
    assert missing.reviewed is None
    reviewer = _ref("reviewer", "reviewer/1", "1", "reviewer")
    evidence = _ref("evidence", "test/review", "1", "evidence")
    reviews = ProcedureReviewService(repository=repository, clock=lambda: NOW)
    reviews.review(
        ingested.record.record_digest, decision_id="review:yes", reviewer_ref=reviewer,
        evidence_refs=(evidence,), accepted=True, authorizer=Allow(), review_authorizer=AllowReview()
    )
    reviews.review(
        ingested.record.record_digest, decision_id="review:no", reviewer_ref=reviewer,
        evidence_refs=(evidence,), accepted=False, authorizer=Allow(), review_authorizer=AllowReview()
    )
    conflicting = service.explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest),
        authorizer=Allow(),
    )
    assert conflicting.review_status == "conflicting"
    assert conflicting.support_status == "conflicting"
    assert conflicting.reviewed is None
    assert "conflict" in conflicting.gaps[0]


def test_dependency_revision_rebuild_marks_reviewed_guidance_stale_without_rewriting_history(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    reviewer = _ref("reviewer", "reviewer/1", "1", "reviewer")
    evidence = _ref("evidence", "test/review", "1", "evidence")
    accepted = ProcedureReviewService(repository=repository, clock=lambda: NOW).review(
        ingested.record.record_digest, decision_id="review:freshness", reviewer_ref=reviewer,
        evidence_refs=(evidence,), accepted=True, authorizer=Allow(), review_authorizer=AllowReview()
    )
    reader = Allow()
    explanation = ProcedureExplanationService(repository=repository, clock=lambda: NOW)
    before = explanation.explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest), authorizer=reader
    )
    assert before.support_status == "current"
    source_digest = ingested.record.command_refs[0].digest
    event = repository.record_invalidation(
        event_id="invalidation:command-1", changed_ref=ingested.record.command_refs[0],
        reason="command descriptor revision changed", at=NOW.replace(hour=13), authorizer=AllowInvalidation()
    )
    assert repository.record_invalidation(
        event_id="invalidation:command-1", changed_ref=ingested.record.command_refs[0],
        reason="command descriptor revision changed", at=NOW.replace(hour=13), authorizer=AllowInvalidation()
    ) == event
    with pytest.raises(ValueError, match="conflicting invalidation"):
        repository.record_invalidation(
            event_id="invalidation:command-1", changed_ref=ingested.record.command_refs[0],
            reason="different cause", at=NOW.replace(hour=13), authorizer=AllowInvalidation()
        )
    repository.record_invalidation(
        event_id="invalidation:command-2", changed_ref=ingested.record.command_refs[0],
        reason="later command descriptor change", at=NOW.replace(hour=12), authorizer=AllowInvalidation()
    )
    repository.rebuild_freshness_projection(at=NOW.replace(hour=13))
    after = explanation.explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest), authorizer=reader
    )
    assert after.support_status == "stale"
    assert event.event_digest in {ref.digest for ref in after.invalidation_refs}
    assert after.candidate.record_digest == ingested.record.record_digest
    assert accepted.promoted_record is not None
    assert accepted.promoted_record.record_digest == repository.load_review("review:freshness").promoted_record.record_digest
    assert ingested.record.command_refs[0].digest == source_digest
    restarted = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    restarted.rebuild_freshness_projection(at=NOW.replace(hour=14))
    replayed = ProcedureExplanationService(repository=restarted, clock=lambda: NOW).explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest), authorizer=reader
    )
    assert replayed.support_status == "stale"
    assert replayed.explanation_digest == after.explanation_digest


def test_explanation_cannot_return_current_after_new_invalidation_event_without_rebuild(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    ProcedureReviewService(repository=repository, clock=lambda: NOW).review(
        ingested.record.record_digest, decision_id="review:watermark", reviewer_ref=_ref("reviewer", "reviewer/watermark", "1", "reviewer"),
        evidence_refs=(_ref("evidence", "test/watermark", "1", "evidence"),), accepted=True,
        authorizer=Allow(), review_authorizer=AllowReview()
    )
    explanation = ProcedureExplanationService(repository=repository, clock=lambda: NOW)
    repository.rebuild_freshness_projection(at=NOW)
    repository.record_invalidation(
        event_id="invalidation:unrebuilt", changed_ref=ingested.record.command_refs[0],
        reason="new command revision", at=NOW.replace(hour=13), authorizer=AllowInvalidation()
    )
    result = explanation.explain(
        ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest), authorizer=Allow()
    )
    assert result.support_status == "stale"


def test_freshness_projection_preserves_overbound_causes_and_explanation_fails_closed(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    for index in range(17):
        repository.record_invalidation(
            event_id=f"invalidation:bound-{index}", changed_ref=ingested.record.command_refs[0],
            reason=f"revision cause {index}", at=NOW.replace(hour=13), authorizer=AllowInvalidation(),
        )
    projections = repository.rebuild_freshness_projection(at=NOW)
    projection = next(item for item in projections if item.procedure_digest == ingested.record.record_digest)
    assert len(projection.invalidation_refs) == 17
    with pytest.raises(ValueError, match="explanation bound"):
        ProcedureExplanationService(repository=repository, clock=lambda: NOW).explain(
            ProcedureExplanationRequest(candidate_digest=ingested.record.record_digest), authorizer=Allow()
        )


def test_reader_only_authority_cannot_write_invalidation_event(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    with pytest.raises(PermissionError, match="invalidation action denied"):
        repository.record_invalidation(
            event_id="invalidation:reader", changed_ref=ingested.record.command_refs[0],
            reason="reader must not mark stale", at=NOW, authorizer=Allow(),
        )


def test_signed_invalidation_action_requires_write_capability(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    ingested = CoreProvenanceService(repository=repository, clock=lambda: NOW).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    changed_ref = ingested.record.command_refs[0]
    event = create_invalidation_event(
        event_id="invalidation:signed", changed_ref=changed_ref,
        reason="signed command revision", recorded_at=NOW,
    )
    authority = AccessContextAuthority(
        {"key": b"invalidate-key"}, issuer="https://control.example.test",
        required_capability_id=PROCEDURE_INVALIDATION_CAPABILITY_ID,
    )
    claims = AccessClaims(
        issuer="https://control.example.test", tenant_id="tenant-a", project_id="project-a",
        subject="agent/maintainer", delegator="user/alice", delegation_id="delegation/invalidate",
        capability_id=PROCEDURE_INVALIDATION_CAPABILITY_ID,
        capability_version=PROCEDURE_INVALIDATION_CAPABILITY_VERSION,
        capability_digest="sha256:" + "e" * 64, actions=("procedure.invalidate",),
        source_ids=(changed_ref.resource_id,), classifications=("internal",),
        policy_digest="sha256:" + "f" * 64, issued_at=NOW, not_before=NOW,
        expires_at=NOW.replace(hour=23), nonce="invalidate-nonce",
    )
    action = HostedProcedureInvalidationAuthorizer(
        authority, authority.issue(claims, key_id="key"), tenant_id="tenant-a", project_id="project-a",
        exact_refs=(changed_ref,), allowed_event_digests=(event.event_digest,),
        capability_digest=claims.capability_digest, clock=lambda: NOW,
    )
    stored = repository.record_invalidation(
        event_id=event.event_id, changed_ref=changed_ref, reason=event.reason,
        at=NOW, authorizer=action,
    )
    assert stored == event
    reader_claims = claims.model_copy(update={"actions": ("context.read",), "nonce": "reader"})
    reader = HostedAccessContextAuthorizer(
        authority, authority.issue(reader_claims, key_id="key"), exact_refs=(changed_ref,), clock=lambda: NOW
    )
    with pytest.raises(PermissionError, match="invalidation action denied"):
        repository.record_invalidation(
            event_id="invalidation:reader-signed", changed_ref=changed_ref,
            reason="reader cannot write", at=NOW, authorizer=reader,
        )
def test_core_receipt_uses_live_authorization_time_not_observed_time() -> None:
    observed = NOW.replace(year=2020)

    class CurrentOnly:
        def authorize(self, ref, *, at=None):
            if at != observed:
                raise PermissionError("revoked now")

    receipt = _receipt(observed_at=observed)
    with pytest.raises(PermissionError, match="provenance access denied"):
        CoreProvenanceService(clock=lambda: NOW).ingest(
            receipt, authorizer=CurrentOnly(), trust=AllowTrust()
        )


def test_core_receipt_rejects_missing_or_non_core_exact_refs() -> None:
    with pytest.raises(ValueError, match="command and environment"):
        create_core_provenance_receipt(
            receipt_id="receipt:missing",
            command_ref=None,
            environment_ref=None,
            observed_at=NOW,
        )
    with pytest.raises(ValueError, match="Core issuer"):
        _receipt(command_ref=_ref("command", "other/command", "1", "descriptor"))


def test_core_receipt_survives_repository_restart_and_rechecks_current_auth(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    first_repo = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    receipt = _receipt()
    first = CoreProvenanceService(repository=first_repo).ingest(
        receipt, authorizer=Allow(), trust=AllowTrust()
    )

    class Denied:
        def authorize(self, ref, *, at=None):
            raise PermissionError("revoked now")

    restarted = CoreProvenanceService(
        repository=CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    )
    with pytest.raises(PermissionError, match="provenance access denied"):
        restarted.ingest(receipt, authorizer=Denied(), trust=AllowTrust())
    assert first.record.record_digest == first_repo.load(receipt.receipt_id).record.record_digest


def test_core_receipt_interrupted_atomic_write_preserves_prior_record(tmp_path, monkeypatch) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    first = _receipt()
    CoreProvenanceService(repository=repository).ingest(first, authorizer=Allow(), trust=AllowTrust())
    second = _receipt(receipt_id="receipt:run-2")

    import rail.semantic.repository as semantic_repository

    def fail_replace(*args, **kwargs):
        raise OSError("simulated interrupted publication")

    monkeypatch.setattr(semantic_repository.os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted"):
        CoreProvenanceService(repository=repository).ingest(
            second, authorizer=Allow(), trust=AllowTrust()
        )
    restored = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a").load(first.receipt_id)
    assert restored is not None
    assert restored.receipt.receipt_digest == first.receipt_digest
    assert CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a").load(second.receipt_id) is None


def test_core_receipt_records_observation_and_ingest_time_separately() -> None:
    receipt = _receipt(observed_at=NOW.replace(hour=9))
    result = CoreProvenanceService(clock=lambda: NOW).ingest(
        receipt, authorizer=Allow(), trust=AllowTrust()
    )
    assert result.record.valid_from == receipt.observed_at
    assert result.record.recorded_at == NOW


def test_core_receipt_rejects_independently_valid_tampered_stored_record(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    first = _receipt()
    second = _receipt(receipt_id="receipt:run-2")
    service = CoreProvenanceService(repository=repository)
    service.ingest(first, authorizer=Allow(), trust=AllowTrust())
    service.ingest(second, authorizer=Allow(), trust=AllowTrust())
    row = repository.store.get("tenant-a", "project-a", "core_provenance", first.receipt_id)
    other = repository.store.get("tenant-a", "project-a", "core_provenance", second.receipt_id)
    with repository.store.transaction():
        repository.store.put(
            row.model_copy(update={"payload": other.payload, "revision": row.revision + 1}),
            expected_revision=row.revision,
        )
    with pytest.raises(ValueError, match="not bound|inconsistent"):
        repository.load(first.receipt_id)


def test_core_receipt_two_repository_instances_do_not_lose_updates(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    first_repo = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    second_repo = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    errors = []

    def run(repo, receipt_id):
        try:
            CoreProvenanceService(repository=repo).ingest(
                _receipt(receipt_id=receipt_id), authorizer=Allow(), trust=AllowTrust()
            )
        except Exception as exc:  # pragma: no cover - assertion reports the concrete error
            errors.append(exc)

    threads = [
        Thread(target=run, args=(first_repo, "receipt:concurrent-1")),
        Thread(target=run, args=(second_repo, "receipt:concurrent-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    restored = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    assert restored.load("receipt:concurrent-1") is not None
    assert restored.load("receipt:concurrent-2") is not None


def test_core_receipt_two_processes_do_not_lose_updates(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    output = context.Queue()
    processes = [
        context.Process(target=_process_ingest, args=(str(path), "receipt:process-1", barrier, output)),
        context.Process(target=_process_ingest, args=(str(path), "receipt:process-2", barrier, output)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert [output.get(timeout=2), output.get(timeout=2)] == [None, None]
    restored = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    assert restored.load("receipt:process-1") is not None
    assert restored.load("receipt:process-2") is not None


def test_core_receipt_lock_releases_after_process_crash(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    lock_path = path.with_name(path.name + ".lock")
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    process = context.Process(target=_process_crash_holding_lock, args=(str(lock_path), ready))
    process.start()
    assert ready.wait(5)
    process.join(5)
    assert process.exitcode == 0
    repository = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    CoreProvenanceService(repository=repository).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )


def test_core_repository_reads_refresh_stale_store_instances(tmp_path) -> None:
    path = tmp_path / ".krail" / "semantic.json"
    first = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    second = CoreProvenanceRepository(path, tenant_id="tenant-a", project_id="project-a")
    CoreProvenanceService(repository=first).ingest(
        _receipt(), authorizer=Allow(), trust=AllowTrust()
    )
    assert second.load("receipt:run-1") is not None


def test_json_store_reader_cannot_observe_uncommitted_commit_or_list(tmp_path) -> None:
    store = JsonSemanticStore(tmp_path / "semantic.json")
    entered = Event()
    release = Event()
    observed = []
    row = SemanticRow(
        tenant_id="tenant-a",
        project_id="project-a",
        record_kind="core_provenance",
        record_id="receipt:pending",
        revision=1,
        payload={"value": "uncommitted"},
        created_at=NOW,
        updated_at=NOW,
    )

    def writer():
        with store.transaction():
            store.put(row, expected_revision=0)
            entered.set()
            release.wait(5)

    def reader():
        entered.wait(5)
        observed.append((store.get("tenant-a", "project-a", "core_provenance", row.record_id), store.list("tenant-a", "project-a")))

    writer_thread = Thread(target=writer)
    reader_thread = Thread(target=reader)
    writer_thread.start()
    reader_thread.start()
    entered.wait(5)
    assert not observed
    release.set()
    writer_thread.join(5)
    reader_thread.join(5)
    assert observed[0][0] == row
    assert observed[0][1] == [row]


def test_json_store_reader_sees_rollback_and_nested_transaction_fails(tmp_path) -> None:
    store = JsonSemanticStore(tmp_path / "semantic.json")
    entered = Event()
    observed = []
    row = SemanticRow(
        tenant_id="tenant-a",
        project_id="project-a",
        record_kind="core_provenance",
        record_id="receipt:rollback",
        revision=1,
        payload={"value": "rollback"},
        created_at=NOW,
        updated_at=NOW,
    )

    def writer():
        with pytest.raises(RuntimeError, match="rollback"):
            with store.transaction():
                store.put(row, expected_revision=0)
                entered.set()
                raise RuntimeError("rollback")

    def reader():
        entered.wait(5)
        observed.append((store.get("tenant-a", "project-a", "core_provenance", row.record_id), store.list("tenant-a", "project-a")))

    writer_thread = Thread(target=writer)
    reader_thread = Thread(target=reader)
    writer_thread.start()
    reader_thread.start()
    writer_thread.join(5)
    reader_thread.join(5)
    assert observed == [(None, [])]
    with pytest.raises(RuntimeError, match="nested"):
        with store.transaction():
            with store.transaction():
                pass
