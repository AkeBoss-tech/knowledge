from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from krail.provider.v1 import ResourceRef
from rail.authorized_context import (
    HostedAccessContextAuthorizer,
    HostedProcedureInvalidationAuthorizer,
    HostedProcedureReviewAuthorizer,
    PROCEDURE_INVALIDATION_CAPABILITY_ID,
    PROCEDURE_INVALIDATION_CAPABILITY_VERSION,
    PROCEDURE_REVIEW_CAPABILITY_ID,
    PROCEDURE_REVIEW_CAPABILITY_VERSION,
)
from rail.core_provenance import (
    CoreProvenanceRepository,
    CoreProvenanceService,
    ProcedureExplanationRequest,
    ProcedureExplanationService,
    ProcedureReviewService,
    create_core_provenance_receipt,
    procedure_record_ref,
)
from rail.hosted.access import AccessClaims, AccessContextAuthority
from rail.procedural_memory import (
    authorize_procedure,
    create_procedure,
    invalidate_for_dependency,
    procedure_temporal_record,
    procedure_temporal_history,
    replay_procedure_history,
    supersede,
    verify_procedure_integrity,
)
from rail.procedure_projection import TemporalProjectionService, create_projection_tombstone
from rail.temporal_records import create_temporal_record, query_temporal_records


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _ref(resource_id: str, version: str = "git:abc") -> ResourceRef:
    content = f"{resource_id}:{version}".encode()
    return ResourceRef(
        authority="git+https://example.test/project",
        resource_type="artifact",
        resource_id=resource_id,
        version=version,
        digest="sha256:" + sha256(content).hexdigest(),
    )


def _procedure(**changes):
    values = {
        "procedure_id": "environment/researcher",
        "procedure_version": "1.0.0",
        "lifecycle": "reviewed",
        "authority": "krail://project/example",
        "writer_family": "human-reviewed",
        "valid_from": NOW,
        "recorded_at": NOW,
        "package_refs": (_ref("packages/researcher"),),
        "command_refs": (_ref("commands/review"),),
        "environment_refs": (_ref("environments/mac"),),
        "test_evidence_refs": (_ref("tests/researcher"),),
        "dependency_refs": (_ref("sources/method"),),
        "rationale": "Keep cited research review reproducible.",
    }
    values.update(changes)
    if values["lifecycle"] == "reviewed":
        values.setdefault("review_ref", _ref("reviews/bootstrap"))
    return create_procedure(**values)


def test_procedure_binds_exact_revisions_and_digest() -> None:
    record = _procedure()
    assert record.record_digest.startswith("sha256:")
    assert record.lifecycle == "reviewed"
    assert record.package_refs[0].version == "git:abc"
    temporal = procedure_temporal_record(record)
    assert temporal.payload_schema == "krail.procedure-memory.v1"
    assert temporal.kind == "approved_state"
    assert {item.exact_key for item in temporal.source_refs} == {
        item.exact_key
        for item in record.package_refs + record.command_refs + record.environment_refs + record.test_evidence_refs + record.dependency_refs
    }


def test_desired_and_observed_activation_are_distinct() -> None:
    desired = _procedure(lifecycle="desired", test_evidence_refs=())
    assert desired.activation_ref is None
    with pytest.raises(ValueError, match="observed activations"):
        _procedure(lifecycle="observed_activation", test_evidence_refs=())


def test_dependency_invalidation_is_selective_and_stable() -> None:
    record = _procedure()
    unrelated = invalidate_for_dependency(record, _ref("sources/other"))
    assert unrelated == record
    stale = invalidate_for_dependency(record, record.dependency_refs[0], reason="method revision changed")
    assert stale.freshness == "stale"
    assert stale.stale_reasons == ("method revision changed",)
    assert invalidate_for_dependency(stale, record.dependency_refs[0]) == stale


def test_company_incident_reviewed_procedure_abstains_after_new_evidence(tmp_path) -> None:
    """Only authorized, currently supported reviewed guidance is actionable."""
    path = tmp_path / ".krail" / "semantic.json"
    repository = CoreProvenanceRepository(path, tenant_id="company", project_id="payments")
    projection = TemporalProjectionService(
        str(path), tenant_id="company", project_id="payments", clock=lambda: NOW
    )

    class ProjectionWriter:
        def authorize(self, record, *, at):
            return None

    class Allow:
        def authorize(self, ref, *, at=None):
            return None

    class Trust:
        def verify(self, receipt):
            return None

    def core_ref(resource_type: str, resource_id: str, version: str) -> ResourceRef:
        return ResourceRef(
            authority="opensaddle://core",
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            digest="sha256:" + sha256(f"{resource_id}:{version}".encode()).hexdigest(),
        )

    def incident_record(source: ResourceRef, revision: str, at: datetime):
        return create_temporal_record(
            record_id=f"company/incident/payment-lag:{revision}",
            entity_id="company/incident/payment-lag",
            entity_authority="krail://company/payments",
            payload_schema="company.incident",
            payload_schema_version="1.0.0",
            kind="observation",
            authority="krail://company/payments",
            writer_family="incident-review",
            valid_from=at,
            recorded_at=at,
            source_refs=(source,),
            revision=revision,
            payload={"summary": "Payment settlement lag exceeded the reviewed threshold."},
        )

    incident_v1_source = _ref("incidents/payment-lag", "git:incident-v1")
    incident_v1 = incident_record(incident_v1_source, "1", NOW)
    projection.ingest(incident_v1, at=NOW, writer=ProjectionWriter())
    projection.register_alias(
        incident_v1_source, projection.record_ref(incident_v1), at=NOW
    )

    receipt_v1 = create_core_provenance_receipt(
        receipt_id="receipt:payment-lag:v1",
        command_ref=core_ref("command", "opensaddle/command/payment-lag-response", "1"),
        environment_ref=core_ref(
            "environment", "opensaddle/environment-revision/payments", "1"
        ),
        observed_at=NOW,
    )
    candidate_v1 = CoreProvenanceService(
        repository=repository,
        clock=lambda: NOW,
        projection=projection,
        projection_writer=ProjectionWriter(),
        projection_id="authorized-guidance",
    ).ingest(receipt_v1, authorizer=Allow(), trust=Trust())
    reviewer = ResourceRef(
        authority="https://control.example.test",
        resource_type="reviewer",
        resource_id="agent/incident-reviewer",
        version="1",
        digest="sha256:" + sha256(b"incident-reviewer").hexdigest(),
    )
    review_evidence_v1 = (incident_v1_source, _ref("tests/payment-lag", "run:v1"))
    review_capability_digest = "sha256:" + "d" * 64
    review_authority = AccessContextAuthority(
        {"key": b"company-review-key"},
        issuer=reviewer.authority,
        required_capability_id=PROCEDURE_REVIEW_CAPABILITY_ID,
    )

    def review_action(candidate, evidence, nonce, *, allowed=True):
        refs = (
            *candidate.command_refs,
            *candidate.environment_refs,
            *evidence,
            reviewer,
        )
        claims = AccessClaims(
            issuer=reviewer.authority,
            tenant_id="company",
            project_id="payments",
            subject=reviewer.resource_id,
            delegator="user/operations-lead",
            delegation_id=f"delegation/{nonce}",
            capability_id=PROCEDURE_REVIEW_CAPABILITY_ID,
            capability_version=PROCEDURE_REVIEW_CAPABILITY_VERSION,
            capability_digest=review_capability_digest,
            actions=(("procedure.review",) if allowed else ("context.read",)),
            source_ids=tuple(ref.resource_id for ref in refs),
            classifications=("internal",),
            policy_digest="sha256:" + "e" * 64,
            issued_at=NOW,
            not_before=NOW,
            expires_at=NOW + timedelta(hours=1),
            nonce=nonce,
        )
        return HostedProcedureReviewAuthorizer(
            review_authority,
            review_authority.issue(claims, key_id="key"),
            tenant_id="company",
            project_id="payments",
            exact_refs=refs,
            allowed_candidate_digests=(candidate.record_digest,),
            capability_digest=review_capability_digest,
            clock=lambda: NOW,
        )

    review_service = ProcedureReviewService(
        repository=repository,
        clock=lambda: NOW,
        projection=projection,
        projection_writer=ProjectionWriter(),
        projection_id="authorized-guidance",
    )
    with pytest.raises(PermissionError, match="procedure review action denied"):
        review_service.review(
            candidate_v1.record.record_digest,
            decision_id="review:payment-lag:v1",
            reviewer_ref=reviewer,
            evidence_refs=review_evidence_v1,
            accepted=True,
            authorizer=Allow(),
            review_authorizer=review_action(
                candidate_v1.record, review_evidence_v1, "review-v1-denied", allowed=False
            ),
        )
    assert repository.load_review("review:payment-lag:v1") is None
    reviewed_v1 = review_service.review(
        candidate_v1.record.record_digest,
        decision_id="review:payment-lag:v1",
        reviewer_ref=reviewer,
        evidence_refs=review_evidence_v1,
        accepted=True,
        authorizer=Allow(),
        review_authorizer=review_action(candidate_v1.record, review_evidence_v1, "review-v1"),
    )
    assert reviewed_v1.promoted_record is not None

    def read_authorizer(record, decision, evidence, nonce, *, restricted=False):
        candidate = (
            candidate_v1.record
            if decision.candidate_digest == candidate_v1.record.record_digest
            else candidate_v2.record
        )
        decision_ref = ResourceRef(
            authority="krail://procedural-memory",
            resource_type="procedure-review",
            resource_id=decision.decision_id,
            version=decision.schema_version,
            digest=decision.decision_digest,
        )
        refs = tuple(dict.fromkeys((
            procedure_record_ref(candidate),
            procedure_record_ref(record),
            *candidate.command_refs,
            *candidate.environment_refs,
            *record.command_refs,
            *record.environment_refs,
            *evidence,
            reviewer,
            record.review_ref,
            decision_ref,
        )))
        refs = tuple(ref for ref in refs if ref is not None)
        granted = (evidence[0],) if restricted else refs
        read_authority = AccessContextAuthority(
            {"key": b"company-read-key"}, issuer="https://read.example.test"
        )
        claims = AccessClaims(
            issuer="https://read.example.test",
            tenant_id="company",
            project_id="payments",
            subject="reader/operations",
            delegator="user/operations-lead",
            delegation_id=f"delegation/{nonce}",
            capability_id="krail.context-brief",
            capability_version="1.0.0",
            capability_digest="sha256:" + "f" * 64,
            actions=("context.read",),
            source_ids=tuple(dict.fromkeys(ref.resource_id for ref in granted)),
            classifications=("internal",),
            policy_digest="sha256:" + "1" * 64,
            issued_at=NOW,
            not_before=NOW,
            expires_at=NOW + timedelta(hours=1),
            nonce=nonce,
        )
        return HostedAccessContextAuthorizer(
            read_authority,
            read_authority.issue(claims, key_id="key"),
            exact_refs=granted,
            clock=lambda: NOW,
        )

    explanation_v1 = ProcedureExplanationService(
        repository=repository,
        clock=lambda: NOW,
        projection=projection,
        projection_id="authorized-guidance",
    )
    request_v1 = ProcedureExplanationRequest(candidate_digest=candidate_v1.record.record_digest)
    reader_v1 = read_authorizer(
        reviewed_v1.promoted_record,
        reviewed_v1.decision,
        review_evidence_v1,
        "read-v1",
    )
    guidance_v1 = explanation_v1.actionable_guidance(request_v1, authorizer=reader_v1)
    assert guidance_v1 is not None
    assert guidance_v1.guidance == reviewed_v1.promoted_record.rationale
    assert incident_v1_source in guidance_v1.evidence_refs

    invalidated_at = NOW + timedelta(minutes=1)
    tombstone = create_projection_tombstone(
        event_id="incident:payment-lag:v2",
        target_ref=projection.record_ref(incident_v1),
        reason="incident evidence revised",
        effective_at=invalidated_at,
        recorded_at=invalidated_at,
    )
    capability_digest = "sha256:" + "a" * 64
    authority = AccessContextAuthority(
        {"key": b"company-procedure-key"},
        issuer="https://control.example.test",
        required_capability_id=PROCEDURE_INVALIDATION_CAPABILITY_ID,
    )
    invalidation_claims = AccessClaims(
        issuer="https://control.example.test",
        tenant_id="company",
        project_id="payments",
        subject="agent/incident-reviewer",
        delegator="user/operations-lead",
        delegation_id="delegation/payment-lag-invalidation",
        capability_id=PROCEDURE_INVALIDATION_CAPABILITY_ID,
        capability_version=PROCEDURE_INVALIDATION_CAPABILITY_VERSION,
        capability_digest=capability_digest,
        actions=("procedure.invalidate",),
        source_ids=(incident_v1.record_id,),
        classifications=("internal",),
        policy_digest="sha256:" + "b" * 64,
        issued_at=NOW,
        not_before=NOW,
        expires_at=NOW + timedelta(hours=1),
        nonce="payment-lag-invalidation",
    )
    invalidator = HostedProcedureInvalidationAuthorizer(
        authority,
        authority.issue(invalidation_claims, key_id="key"),
        tenant_id="company",
        project_id="payments",
        exact_refs=(projection.record_ref(incident_v1),),
        allowed_event_digests=(tombstone.tombstone_digest,),
        capability_digest=capability_digest,
        clock=lambda: invalidated_at,
    )
    projection.clock = lambda: invalidated_at
    projection.tombstone(
        projection.record_ref(incident_v1),
        event_id=tombstone.event_id,
        reason=tombstone.reason,
        effective_at=tombstone.effective_at,
        recorded_at=tombstone.recorded_at,
        authorizer=invalidator,
    )
    assert explanation_v1.actionable_guidance(request_v1, authorizer=reader_v1) is None
    before_recompute_projection = TemporalProjectionService(
        str(path), tenant_id="company", project_id="payments", clock=lambda: invalidated_at
    )
    before_recompute_restart = ProcedureExplanationService(
        repository=CoreProvenanceRepository(
            path, tenant_id="company", project_id="payments"
        ),
        clock=lambda: invalidated_at,
        projection=before_recompute_projection,
        projection_id="authorized-guidance",
    )
    assert before_recompute_restart.actionable_guidance(
        request_v1, authorizer=reader_v1
    ) is None
    projection.recompute(
        projection_id="authorized-guidance",
        valid_at=invalidated_at,
        known_at=invalidated_at,
        at=invalidated_at,
    )
    assert explanation_v1.actionable_guidance(request_v1, authorizer=reader_v1) is None

    rereviewed_at = NOW + timedelta(minutes=2)
    incident_v2_source = _ref("incidents/payment-lag", "git:incident-v2")
    incident_v2 = incident_record(incident_v2_source, "2", rereviewed_at)
    projection.ingest(incident_v2, at=rereviewed_at, writer=ProjectionWriter())
    projection.register_alias(
        incident_v2_source, projection.record_ref(incident_v2), at=rereviewed_at
    )
    receipt_v2 = create_core_provenance_receipt(
        receipt_id="receipt:payment-lag:v2",
        command_ref=core_ref("command", "opensaddle/command/payment-lag-response", "2"),
        environment_ref=core_ref(
            "environment", "opensaddle/environment-revision/payments", "2"
        ),
        observed_at=rereviewed_at,
    )
    projection.clock = lambda: rereviewed_at
    candidate_v2 = CoreProvenanceService(
        repository=repository,
        clock=lambda: rereviewed_at,
        projection=projection,
        projection_writer=ProjectionWriter(),
        projection_id="authorized-guidance",
    ).ingest(receipt_v2, authorizer=Allow(), trust=Trust())
    review_evidence_v2 = (incident_v2_source, _ref("tests/payment-lag", "run:v2"))
    reviewed_v2 = ProcedureReviewService(
        repository=repository,
        clock=lambda: rereviewed_at,
        projection=projection,
        projection_writer=ProjectionWriter(),
        projection_id="authorized-guidance",
    ).review(
        candidate_v2.record.record_digest,
        decision_id="review:payment-lag:v2",
        reviewer_ref=reviewer,
        evidence_refs=review_evidence_v2,
        accepted=True,
        authorizer=Allow(),
        review_authorizer=review_action(candidate_v2.record, review_evidence_v2, "review-v2"),
    )
    assert reviewed_v2.promoted_record is not None
    request_v2 = ProcedureExplanationRequest(candidate_digest=candidate_v2.record.record_digest)
    reader_v2 = read_authorizer(
        reviewed_v2.promoted_record,
        reviewed_v2.decision,
        review_evidence_v2,
        "read-v2",
    )
    explanation_v2 = ProcedureExplanationService(
        repository=repository,
        clock=lambda: rereviewed_at,
        projection=projection,
        projection_id="authorized-guidance",
    )
    guidance_v2 = explanation_v2.actionable_guidance(request_v2, authorizer=reader_v2)
    assert guidance_v2 is not None, [
        (
            state.entity_id,
            state.record_digests,
            tuple((item.input_ref.resource_id, item.status) for item in state.dependency_states),
        )
        for state in projection.current_state("authorized-guidance")
    ]
    assert guidance_v2.evidence_refs == review_evidence_v2
    assert incident_v1_source not in guidance_v2.evidence_refs
    with pytest.raises(PermissionError, match="procedure guidance access denied"):
        explanation_v2.actionable_guidance(
            request_v2,
            authorizer=read_authorizer(
                reviewed_v2.promoted_record,
                reviewed_v2.decision,
                review_evidence_v2,
                "restricted-read",
                restricted=True,
            ),
        )

    restarted_projection = TemporalProjectionService(
        str(path), tenant_id="company", project_id="payments", clock=lambda: rereviewed_at
    )
    restarted = ProcedureExplanationService(
        repository=CoreProvenanceRepository(
            path, tenant_id="company", project_id="payments"
        ),
        clock=lambda: rereviewed_at,
        projection=restarted_projection,
        projection_id="authorized-guidance",
    )
    assert restarted.actionable_guidance(request_v2, authorizer=reader_v2) == guidance_v2
    persisted_records = {
        row.record_id
        for row in restarted_projection.store.list("company", "payments", kind="temporal_record")
    }
    assert {incident_v1.record_digest, incident_v2.record_digest} <= persisted_records


@pytest.mark.parametrize(
    "changes",
    (
        {"valid_to": NOW + timedelta(seconds=1)},
        {"valid_from": NOW + timedelta(seconds=3), "recorded_at": NOW + timedelta(seconds=3)},
    ),
)
def test_actionable_guidance_without_projection_rejects_invalid_time_ranges(
    tmp_path, monkeypatch, changes
) -> None:
    reviewed = _procedure(**changes)
    service = ProcedureExplanationService(
        repository=CoreProvenanceRepository(
            tmp_path / "semantic.json", tenant_id="t", project_id="p"
        ),
        clock=lambda: NOW + timedelta(seconds=2),
    )
    explanation = SimpleNamespace(
        review_status="accepted",
        support_status="current",
        candidate=_procedure(lifecycle="desired", test_evidence_refs=()),
        reviewed=reviewed,
        decisions=(),
        invalidation_refs=(),
    )
    authorized = []

    def explain(request, *, authorizer):
        authorizer.authorize(reviewed.command_refs[0], at=NOW)
        authorized.append(True)
        return explanation

    monkeypatch.setattr(service, "explain", explain)
    monkeypatch.setattr(
        service,
        "_authorize_actionable_identities",
        lambda request, *, authorizer, at: None,
    )
    assert service.actionable_guidance(
        ProcedureExplanationRequest(candidate_digest=explanation.candidate.record_digest),
        authorizer=_Authorizer(),
    ) is None
    assert authorized == [True]


def test_supersession_requires_exact_prior_digest_and_identity() -> None:
    previous = _procedure()
    replacement = _procedure(procedure_version="1.1.0", supersedes_digest=previous.record_digest)
    assert supersede(previous, replacement) == replacement
    with pytest.raises(ValueError, match="exact prior"):
        supersede(previous, _procedure(procedure_version="1.2.0"))
    with pytest.raises(ValueError, match="same procedure identity"):
        supersede(previous, _procedure(procedure_id="environment/other", supersedes_digest=previous.record_digest))
    with pytest.raises(ValueError, match="authority or writer"):
        supersede(
            previous,
            _procedure(
                procedure_version="1.1.0",
                authority="krail://other",
                supersedes_digest=previous.record_digest,
            ),
        )


def test_invalid_time_and_duplicate_lineage_are_rejected() -> None:
    with pytest.raises(ValueError, match="valid_to"):
        _procedure(valid_to=NOW.replace(year=2025))
    with pytest.raises(ValueError, match="unique exact"):
        _procedure(dependency_refs=(_ref("sources/method"), _ref("sources/method")))


def test_out_of_order_history_replay_is_deterministic_and_checks_supersession() -> None:
    previous = _procedure(valid_from=NOW, recorded_at=NOW)
    replacement = _procedure(
        procedure_version="1.1.0",
        valid_from=NOW.replace(hour=13),
        recorded_at=NOW.replace(hour=14),
        supersedes_digest=previous.record_digest,
    )
    assert replay_procedure_history([replacement, previous]) == (previous, replacement)
    with pytest.raises(ValueError, match="supplied exact revision"):
        replay_procedure_history([_procedure(procedure_version="1.2.0", supersedes_digest="sha256:" + "f" * 64)])


def test_procedure_history_composes_into_generic_temporal_query() -> None:
    previous = _procedure(valid_from=NOW.replace(hour=10), recorded_at=NOW.replace(hour=11))
    replacement = _procedure(
        procedure_version="1.1.0",
        valid_from=NOW.replace(hour=10),
        recorded_at=NOW.replace(hour=13),
        supersedes_digest=previous.record_digest,
    )
    with pytest.raises(ValueError, match="procedure_temporal_history"):
        procedure_temporal_record(replacement)
    temporal = procedure_temporal_history([replacement, previous])
    assert [item.revision for item in temporal] == ["1.0.0", "1.1.0"]
    assert {item.payload_schema_version for item in temporal} == {"1.0.0"}
    assert temporal[1].supersedes_digest == temporal[0].record_digest
    before = query_temporal_records(temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=12))
    after = query_temporal_records(temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=14))
    assert [item.revision for item in before] == ["1.0.0"]
    assert [item.revision for item in after] == ["1.1.0"]


def test_procedure_history_resolves_three_revision_correction_chain() -> None:
    first = _procedure(valid_from=NOW.replace(hour=10), recorded_at=NOW.replace(hour=11))
    second = _procedure(
        procedure_version="1.1.0",
        valid_from=NOW.replace(hour=9),
        recorded_at=NOW.replace(hour=13),
        supersedes_digest=first.record_digest,
    )
    third = _procedure(
        procedure_version="1.2.0",
        valid_from=NOW.replace(hour=8),
        recorded_at=NOW.replace(hour=15),
        supersedes_digest=second.record_digest,
    )
    temporal = procedure_temporal_history([third, first, second])
    assert [item.revision for item in query_temporal_records(
        temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=12)
    )] == ["1.0.0"]
    assert [item.revision for item in query_temporal_records(
        temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=14)
    )] == ["1.1.0"]
    assert [item.revision for item in query_temporal_records(
        temporal, valid_at=NOW.replace(hour=10), known_at=NOW.replace(hour=16)
    )] == ["1.2.0"]


def test_replay_rejects_conflicting_authority_or_same_version() -> None:
    first = _procedure()
    with pytest.raises(ValueError, match="mix identities"):
        replay_procedure_history([first, _procedure(authority="krail://other")])
    with pytest.raises(ValueError, match="conflicting revisions"):
        replay_procedure_history([first, _procedure(procedure_version=first.procedure_version, rationale="different")])


def test_procedure_integrity_boundary_detects_mutation() -> None:
    record = _procedure()
    with pytest.raises(ValidationError, match="frozen"):
        record.stale_reasons += ("tampered",)
    assert verify_procedure_integrity(record) is record


class _Authorizer:
    def __init__(self, denied: ResourceRef | None = None):
        self.denied = denied
        self.seen: list[ResourceRef] = []

    def authorize(self, ref: ResourceRef, *, at=None) -> None:
        self.seen.append(ref)
        if self.denied is not None and ref.exact_key == self.denied.exact_key:
            raise PermissionError("hidden reason")


def test_all_procedure_refs_are_authorized_before_metadata_exposure() -> None:
    record = _procedure()
    authorizer = _Authorizer()
    assert authorize_procedure(record, authorizer) is record
    assert {ref.exact_key for ref in authorizer.seen} == {
        ref.exact_key
        for ref in record.package_refs + record.command_refs + record.environment_refs + record.test_evidence_refs + record.dependency_refs
    } | {record.review_ref.exact_key}
    denied = _Authorizer(record.environment_refs[0])
    with pytest.raises(PermissionError, match="procedure access denied") as error:
        authorize_procedure(record, denied)
    assert "environments/mac" not in str(error.value)
    assert "Keep cited" not in str(error.value)
