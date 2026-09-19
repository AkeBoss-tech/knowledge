from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from krail.provider.v1 import ResourceRef
from rail.extension_registry import (
    DomainExtensionRegistry,
    HANDLER_LINEAGE_REFS,
    describe_extension,
    describe_operator,
    verify_invocation_integrity,
)
from rail.authorized_context import HostedAccessContextAuthorizer
from rail.company_incident_response import (
    COMPANY_INCIDENT_OPERATOR_ID,
    COMPANY_INCIDENT_OPERATOR_VERSION,
    company_incident_response_extension,
    register_company_incident_response_extension,
)
from rail.core_provenance import (
    CoreProvenanceRepository,
    CoreProvenanceService,
    ProcedureActionableGuidanceResult,
    ProcedureExplanationRequest,
    ProcedureExplanationService,
    ProcedureReviewService,
    create_invalidation_event,
    create_core_provenance_receipt,
    procedure_record_ref,
)
from rail.hosted.access import (
    AccessClaims,
    AccessContextAuthority,
    MemoryRevocationRegistry,
)
from rail.procedure_projection import TemporalProjectionService
from rail.procedural_memory import procedure_temporal_history
from rail.temporal_records import create_temporal_record


def _ref(resource_id: str) -> ResourceRef:
    return ResourceRef(
        authority="https://knowledge.example.test",
        resource_type="evidence",
        resource_id=resource_id,
        version="v1",
        digest="sha256:" + sha256(resource_id.encode()).hexdigest(),
    )


class Allow:
    def authorize(self, ref: ResourceRef, *, at=None) -> None:
        return None


class Deny:
    def authorize(self, ref: ResourceRef) -> None:
        raise PermissionError("hidden source")


class RevokeAfterFirstCheck:
    def __init__(self):
        self.calls = 0

    def authorize(self, ref: ResourceRef) -> None:
        self.calls += 1
        if self.calls > 1:
            raise PermissionError("revoked")


class DenyResource:
    def __init__(self, resource_id: str) -> None:
        self.resource_id = resource_id

    def authorize(self, ref: ResourceRef) -> None:
        if ref.resource_id == self.resource_id:
            raise PermissionError("hidden source")


class ReviewAllow:
    def authorize_review(
        self, candidate_digest, reviewer_ref, evidence_refs, *, lineage_refs=(), at
    ) -> None:
        return None


class TrustAllow:
    def verify(self, receipt) -> None:
        return None


class ProjectionWriteAllow:
    def authorize(self, record, *, at) -> None:
        return None


class ProjectionInvalidateAllow:
    def authorize_invalidation(
        self, event_id, changed_ref, event_digest, *, at
    ) -> None:
        return None


def _extension(extension_id: str, schema: str, operator_id: str):
    operator = describe_operator(
        operator_id=operator_id,
        version="1.0.0",
        input_schema=schema,
        output_schema=schema,
        deterministic=True,
    )
    return describe_extension(
        extension_id=extension_id,
        version="1.0.0",
        payload_schemas=(schema,),
        operators=(operator,),
    ), operator


def test_robotics_and_company_extensions_share_discovery_and_dispatch() -> None:
    registry = DomainExtensionRegistry()
    robotics, robot_operator = _extension(
        "robotics", "robotics.object_state_estimate.v1", "robotics.validate-pose"
    )
    company, company_operator = _extension(
        "company", "company.service_ownership.v1", "company.validate-owner"
    )
    registry.register(robotics, {robot_operator.operator_id: lambda inputs, config: {"valid": "pose" in inputs[0]}})
    registry.register(company, {company_operator.operator_id: lambda inputs, config: {"valid": "owner" in inputs[0]}})

    robot = registry.dispatch(robot_operator.operator_id, robot_operator.version, ((_ref("robot"), {"pose": [1, 2]}),), config={"frame": "map"}, authorizer=Allow())
    owner = registry.dispatch(company_operator.operator_id, company_operator.version, ((_ref("service"), {"owner": "team-a"}),), config={}, authorizer=Allow())
    assert robot.output == {"valid": True}
    assert owner.output == {"valid": True}
    assert robot.lineage_digest != owner.lineage_digest


def test_registry_rejects_duplicate_versions_and_schema_collisions() -> None:
    registry = DomainExtensionRegistry()
    first, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    registry.register(first, {operator.operator_id: lambda inputs, config: {}})
    with pytest.raises(ValueError, match="already registered"):
        registry.register(first, {operator.operator_id: lambda inputs, config: {}})
    collision, collision_operator = _extension("company", "robotics.pose.v1", "company.validate")
    with pytest.raises(ValueError, match="owned by another"):
        registry.register(collision, {collision_operator.operator_id: lambda inputs, config: {}})


def test_dispatch_requires_exact_operator_and_authorization_before_handler() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    calls = []
    registry.register(descriptor, {operator.operator_id: lambda inputs, config: calls.append(1) or {}})
    with pytest.raises(PermissionError, match="extension access denied"):
        registry.dispatch(operator.operator_id, operator.version, ((_ref("secret"), {}),), config={}, authorizer=Deny())
    assert calls == []
    with pytest.raises(LookupError, match="unknown operator"):
        registry.dispatch(operator.operator_id, "2.0.0", ((_ref("source"), {}),), config={}, authorizer=Allow())
    with pytest.raises(ValueError, match="at least one exact input ref"):
        registry.dispatch(
            operator.operator_id,
            operator.version,
            (),
            config={},
            authorizer=Allow(),
        )


def test_registry_does_not_accept_untrusted_install_or_handler_shape() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    untrusted = descriptor.model_copy(update={"trust": "third_party"})
    with pytest.raises(ValueError, match="trusted local"):
        registry.register(untrusted, {operator.operator_id: lambda inputs, config: {}})
    with pytest.raises(TypeError, match="callables"):
        registry.register(descriptor, {operator.operator_id: "eval('bad')"})


def test_dispatch_rechecks_authorization_after_handler_before_exposure() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    registry.register(descriptor, {operator.operator_id: lambda inputs, config: {"valid": True}})

    with pytest.raises(PermissionError, match="extension access denied"):
        registry.dispatch(
            operator.operator_id,
            operator.version,
            ((_ref("revoked-during-call"), {}),),
            config={},
            authorizer=RevokeAfterFirstCheck(),
        )


def test_invocation_lineage_exposes_output_digest_for_integrity_checks() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    registry.register(descriptor, {operator.operator_id: lambda inputs, config: {"valid": True}})
    result = registry.dispatch(
        operator.operator_id,
        operator.version,
        ((_ref("lineage"), {}),),
        config={},
        authorizer=Allow(),
    )
    assert result.output_digest.startswith("sha256:")
    verify_invocation_integrity(result, operator)
    result.output["valid"] = False
    with pytest.raises(ValueError, match="output was mutated"):
        verify_invocation_integrity(result, operator)


def test_dispatch_binds_handler_consumed_exact_refs_into_lineage() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    canonical_ref = _ref("canonical-snapshot")
    registry.register(
        descriptor,
        {operator.operator_id: lambda inputs, config: {"valid": True, HANDLER_LINEAGE_REFS: (canonical_ref,)}},
    )
    caller_ref = _ref("caller")
    result = registry.dispatch(
        operator.operator_id,
        operator.version,
        ((caller_ref, {}),),
        config={},
        authorizer=Allow(),
    )
    assert result.output == {"valid": True}
    assert {ref.exact_key for ref in result.input_refs} == {caller_ref.exact_key, canonical_ref.exact_key}
    verify_invocation_integrity(result, operator)


def test_dispatch_rejects_denied_or_malformed_handler_lineage_refs() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    canonical_ref = _ref("canonical-secret")
    calls = []
    registry.register(
        descriptor,
        {operator.operator_id: lambda inputs, config: calls.append(True) or {HANDLER_LINEAGE_REFS: (canonical_ref,)}},
    )
    with pytest.raises(PermissionError, match="extension access denied"):
        registry.dispatch(
            operator.operator_id,
            operator.version,
            ((_ref("caller"), {}),),
            config={},
            authorizer=DenyResource("canonical-secret"),
        )
    assert calls == [True]

    malformed = DomainExtensionRegistry()
    malformed.register(
        descriptor,
        {operator.operator_id: lambda inputs, config: {HANDLER_LINEAGE_REFS: ("not-a-ref",)}},
    )
    with pytest.raises(TypeError, match="lineage refs"):
        malformed.dispatch(operator.operator_id, operator.version, ((_ref("caller"), {}),), config={}, authorizer=Allow())


def test_invocation_integrity_rejects_output_schema_tampering() -> None:
    registry = DomainExtensionRegistry()
    descriptor, operator = _extension("robotics", "robotics.pose.v1", "robotics.validate")
    registry.register(descriptor, {operator.operator_id: lambda inputs, config: {"valid": True}})
    result = registry.dispatch(
        operator.operator_id,
        operator.version,
        ((_ref("schema"), {}),),
        config={},
        authorizer=Allow(),
    )
    tampered = result.model_copy(update={"output_schema": "robotics.unauthorized.v1"})
    with pytest.raises(ValueError, match="output schema"):
        verify_invocation_integrity(tampered, operator)


def test_company_incident_guidance_uses_generic_registry_and_temporal_replay(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 7, 12, tzinfo=UTC)
    path = tmp_path / "company.json"
    repository = CoreProvenanceRepository(
        path, tenant_id="company", project_id="payments"
    )
    projection = TemporalProjectionService(
        str(path), tenant_id="company", project_id="payments", clock=lambda: now
    )

    def exact(resource_type: str, resource_id: str, version: str) -> ResourceRef:
        return ResourceRef(
            authority="opensaddle://core",
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            digest="sha256:"
            + sha256(f"{resource_id}:{version}".encode()).hexdigest(),
        )

    incident_v1_source = ResourceRef(
        authority="git://company/incidents",
        resource_type="evidence",
        resource_id="incidents/payment-lag",
        version="v1",
        digest="sha256:" + sha256(b"incident-v1").hexdigest(),
    )
    incident_v1 = create_temporal_record(
        record_id="company/incident/payment-lag:1",
        entity_id="company/incident/payment-lag",
        entity_authority="krail://company/payments",
        payload_schema="company.incident",
        payload_schema_version="1.0.0",
        kind="observation",
        authority="krail://company/payments",
        writer_family="incident-review",
        valid_from=now,
        recorded_at=now,
        ingested_at=now,
        source_refs=(incident_v1_source,),
        revision="1",
        payload={"summary": "Settlement lag exceeded the reviewed threshold."},
    )
    projection.ingest(incident_v1, at=now, writer=ProjectionWriteAllow())
    projection.register_alias(
        incident_v1_source, projection.record_ref(incident_v1), at=now
    )
    incident_v1_evidence = projection.record_ref(incident_v1)

    def candidate(version: str, at: datetime):
        receipt = create_core_provenance_receipt(
            receipt_id=f"receipt:payment-lag:{version}",
            command_ref=exact(
                "command", "opensaddle/command/payment-lag-response", version
            ),
            environment_ref=exact(
                "environment",
                f"opensaddle/environment-revision/payments-{version}",
                version,
            ),
            observed_at=at,
        )
        return CoreProvenanceService(
            repository=repository,
            clock=lambda: at,
            projection=projection,
            projection_writer=ProjectionWriteAllow(),
            projection_id="company-incident-response",
        ).ingest(receipt, authorizer=Allow(), trust=TrustAllow())

    reviewer = ResourceRef(
        authority="https://control.example.test",
        resource_type="reviewer",
        resource_id="reviewer/incident-operations",
        version="1",
        digest="sha256:" + sha256(b"incident-reviewer").hexdigest(),
    )

    def review(candidate_value, evidence, decision_id: str, at: datetime):
        return ProcedureReviewService(
            repository=repository,
            clock=lambda: at,
            projection=projection,
            projection_writer=ProjectionWriteAllow(),
            projection_id="company-incident-response",
        ).review(
            candidate_value.record.record_digest,
            decision_id=decision_id,
            reviewer_ref=reviewer,
            evidence_refs=evidence,
            accepted=True,
            authorizer=Allow(),
            review_authorizer=ReviewAllow(),
        )

    candidate_v1 = candidate("1", now)
    test_v1 = _ref("tests/payment-lag-v1")
    reviewed_v1 = review(
        candidate_v1,
        (incident_v1_evidence, test_v1),
        "review:payment-lag:v1",
        now,
    )
    assert reviewed_v1.promoted_record is not None

    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"reader": b"company-incident-reader"},
        issuer="https://control.example.test",
        revocations=revocations,
    )
    company_operator = company_incident_response_extension().operators[0]

    def dispatch_for(
        candidate_value,
        reviewed_value,
        incident_source,
        test_ref,
        at,
        *,
        omit_resource_id=None,
        tenant_id="company",
        project_id="payments",
        capability_id=COMPANY_INCIDENT_OPERATOR_ID,
        capability_version=COMPANY_INCIDENT_OPERATOR_VERSION,
        capability_digest=company_operator.descriptor_digest,
        issued_at=now,
        not_before=now,
        expires_at=now + timedelta(hours=1),
        revoke=False,
        extra_refs=(),
        prepare_service=None,
        nonce,
    ):
        promoted = reviewed_value.promoted_record
        assert promoted is not None
        promoted_temporal = procedure_temporal_history(
            (candidate_value.record, promoted)
        )[-1]
        decision_ref = ResourceRef(
            authority="krail://procedural-memory",
            resource_type="procedure-review",
            resource_id=reviewed_value.decision.decision_id,
            version=reviewed_value.decision.schema_version,
            digest=reviewed_value.decision.decision_digest,
        )
        state = next(
            (
                item
                for item in projection.current_state("company-incident-response")
                if promoted_temporal.record_digest in item.record_digests
            ),
            None,
        )
        projection_refs = (
            (projection.current_state_ref(state),)
            if state is not None
            else ()
        )
        refs = tuple(
            dict.fromkeys(
                (
                    incident_source,
                    procedure_record_ref(candidate_value.record),
                    procedure_record_ref(promoted),
                    *candidate_value.record.command_refs,
                    *candidate_value.record.environment_refs,
                    *promoted.command_refs,
                    *promoted.environment_refs,
                    incident_source,
                    test_ref,
                    reviewer,
                    promoted.review_ref,
                    decision_ref,
                    *projection_refs,
                    *extra_refs,
                )
            )
        )
        refs = tuple(ref for ref in refs if ref is not None)
        granted = tuple(
            ref for ref in refs if ref.resource_id != omit_resource_id
        )
        claims = AccessClaims(
            issuer="https://control.example.test",
            tenant_id=tenant_id,
            project_id=project_id,
            subject="reader/incident-operator",
            delegator="user/operations-lead",
            delegation_id=f"delegation/{nonce}",
            capability_id=capability_id,
            capability_version=capability_version,
            capability_digest=capability_digest,
            actions=("context.read",),
            source_ids=tuple(dict.fromkeys(ref.resource_id for ref in granted)),
            classifications=("internal",),
            policy_digest="sha256:" + "d" * 64,
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
            nonce=nonce,
        )
        context = authority.issue(claims, key_id="reader")
        if revoke:
            revocations.revoke_context(context.context_digest, revoked_at=at)
        reader = HostedAccessContextAuthorizer(
            authority,
            context,
            exact_refs=granted,
            clock=lambda: at,
        )
        service = ProcedureExplanationService(
            repository=repository,
            clock=lambda: at,
            projection=projection,
            projection_id="company-incident-response",
        )
        if prepare_service is not None:
            prepare_service(service)
        registry = DomainExtensionRegistry()
        register_company_incident_response_extension(registry, service, reader)
        # The robotics extension and company operator coexist in the same
        # generic registry; dispatch contains no domain branch.
        robotics, robotics_operator = _extension(
            "robotics",
            "robotics.object-state.v1",
            "robotics.validate-object-state",
        )
        registry.register(
            robotics,
            {robotics_operator.operator_id: lambda inputs, config: {"valid": True}},
        )
        assert {item.extension_id for item in registry.discover()} == {
            "company.incident-response",
            "robotics",
        }
        result = registry.dispatch(
            COMPANY_INCIDENT_OPERATOR_ID,
            COMPANY_INCIDENT_OPERATOR_VERSION,
            (
                (
                    incident_source,
                    ProcedureExplanationRequest(
                        candidate_digest=candidate_value.record.record_digest
                    ).model_dump(mode="json"),
                ),
            ),
            config={},
            authorizer=reader,
        )
        verify_invocation_integrity(
            result,
            next(
                item
                for extension in registry.discover()
                if extension.extension_id == "company.incident-response"
                for item in extension.operators
            ),
        )
        return result

    current_v1 = dispatch_for(
        candidate_v1,
        reviewed_v1,
        incident_v1_evidence,
        test_v1,
        now,
        nonce="current-v1",
    )
    current_payload = ProcedureActionableGuidanceResult.model_validate(
        current_v1.output
    )
    assert current_payload.status == "guidance"
    assert current_payload.guidance is not None
    assert current_payload.guidance.guidance == reviewed_v1.promoted_record.rationale
    lineage_ids = {ref.resource_id for ref in current_v1.input_refs}
    assert {
        candidate_v1.record.procedure_id,
        reviewed_v1.decision.decision_id,
        incident_v1_evidence.resource_id,
    } <= lineage_ids
    projection_lineage_ref = next(
        ref
        for ref in current_v1.input_refs
        if ref.resource_type == "projection-current-state"
    )
    persisted_projection = projection.store.get(
        "company",
        "payments",
        "procedure_projection_current",
        projection_lineage_ref.resource_id,
    )
    assert persisted_projection is not None
    assert str(persisted_projection.revision) == projection_lineage_ref.version
    assert (
        persisted_projection.payload["state"]["state_digest"]
        == projection_lineage_ref.digest
    )
    lineage_keys = {ref.exact_key for ref in current_v1.input_refs}
    assert {
        procedure_record_ref(candidate_v1.record).exact_key,
        procedure_record_ref(reviewed_v1.promoted_record).exact_key,
        incident_v1_evidence.exact_key,
        test_v1.exact_key,
        reviewer.exact_key,
    } <= lineage_keys

    authority_failures = (
        ("wrong-tenant", {"tenant_id": "other-company"}),
        ("wrong-project", {"project_id": "other-project"}),
        ("wrong-capability", {"capability_id": "company.other-operation"}),
        ("wrong-version", {"capability_version": "2.0.0"}),
        ("wrong-digest", {"capability_digest": "sha256:" + "0" * 64}),
        (
            "expired",
            {
                "issued_at": now - timedelta(hours=2),
                "not_before": now - timedelta(hours=2),
                "expires_at": now - timedelta(hours=1),
            },
        ),
        ("revoked", {"revoke": True}),
    )
    for nonce, overrides in authority_failures:
        with pytest.raises(
            PermissionError,
            match="company incident response access denied|extension access denied",
        ):
            dispatch_for(
                candidate_v1,
                reviewed_v1,
                incident_v1_evidence,
                test_v1,
                now,
                nonce=nonce,
                **overrides,
            )

    for denied_id, nonce in (
        (incident_v1_evidence.resource_id, "deny-evidence"),
        (reviewed_v1.decision.decision_id, "deny-review"),
    ):
        with pytest.raises(
            PermissionError,
            match="extension access denied|procedure explanation access denied",
        ):
            dispatch_for(
                candidate_v1,
                reviewed_v1,
                incident_v1_evidence,
                test_v1,
                now,
                omit_resource_id=denied_id,
                nonce=nonce,
            )

    race_at = now + timedelta(seconds=30)
    race_event = create_invalidation_event(
        event_id="invalidate:payment-lag:v1-during-read",
        changed_ref=test_v1,
        reason="review evidence revoked during guidance read",
        recorded_at=race_at,
    )
    race_event_ref = ResourceRef(
        authority="krail://procedural-memory",
        resource_type="procedure-invalidation",
        resource_id=race_event.event_id,
        version=race_event.schema_version,
        digest=race_event.event_digest,
    )

    def invalidate_after_initial_explain(service):
        initial_explain = service.explain
        calls = 0

        def interleaved(request, *, authorizer):
            nonlocal calls
            explanation = initial_explain(request, authorizer=authorizer)
            calls += 1
            if calls == 1:
                repository.record_invalidation(
                    event_id=race_event.event_id,
                    changed_ref=test_v1,
                    reason=race_event.reason,
                    at=race_at,
                    authorizer=ProjectionInvalidateAllow(),
                )
            return explanation

        service.explain = interleaved

    raced = dispatch_for(
        candidate_v1,
        reviewed_v1,
        incident_v1_evidence,
        test_v1,
        race_at,
        extra_refs=(race_event_ref,),
        prepare_service=invalidate_after_initial_explain,
        nonce="invalidation-during-guidance",
    )
    raced_payload = ProcedureActionableGuidanceResult.model_validate(raced.output)
    assert raced_payload.status == "abstained"
    assert raced_payload.guidance is None
    assert reviewed_v1.promoted_record.rationale not in str(raced.output)
    assert race_event_ref in raced.input_refs

    corrected_at = now + timedelta(minutes=2)
    incident_v2_source = incident_v1_source.model_copy(
        update={
            "resource_id": "incidents/payment-lag/v2",
            "version": "v2",
            "digest": "sha256:" + sha256(b"incident-v2").hexdigest(),
        }
    )
    incident_v2 = create_temporal_record(
        record_id="company/incident/payment-lag:2",
        entity_id=incident_v1.entity_id,
        entity_authority=incident_v1.entity_authority,
        payload_schema=incident_v1.payload_schema,
        payload_schema_version=incident_v1.payload_schema_version,
        kind="observation",
        authority=incident_v1.authority,
        writer_family=incident_v1.writer_family,
        valid_from=now + timedelta(minutes=1),
        recorded_at=corrected_at,
        ingested_at=corrected_at + timedelta(seconds=30),
        source_refs=(incident_v2_source,),
        revision="2",
        payload={"summary": "Revised settlement-lag incident evidence."},
    )
    assert incident_v2.ingested_at > incident_v2.recorded_at
    projection.clock = lambda: corrected_at + timedelta(seconds=30)
    projection.ingest(
        incident_v2,
        at=corrected_at + timedelta(seconds=30),
        writer=ProjectionWriteAllow(),
    )
    projection.register_alias(
        incident_v2_source,
        projection.record_ref(incident_v2),
        at=corrected_at + timedelta(seconds=30),
    )
    incident_v2_evidence = projection.record_ref(incident_v2)
    projection.tombstone(
        projection.record_ref(incident_v1),
        event_id="incident:payment-lag:v2-replaces-v1",
        reason="late-arriving v2 evidence invalidates v1",
        effective_at=now + timedelta(minutes=1),
        recorded_at=corrected_at + timedelta(seconds=30),
        authorizer=ProjectionInvalidateAllow(),
    )
    restarted_projection = TemporalProjectionService(
        str(path),
        tenant_id="company",
        project_id="payments",
        clock=lambda: corrected_at + timedelta(seconds=30),
    )
    rebuilt = restarted_projection.rebuild(
        projection_id="company-incident-response",
        valid_at=corrected_at + timedelta(seconds=30),
        known_at=corrected_at + timedelta(seconds=30),
        at=corrected_at + timedelta(seconds=30),
    )
    replayed = restarted_projection.rebuild(
        projection_id="company-incident-response",
        valid_at=corrected_at + timedelta(seconds=30),
        known_at=corrected_at + timedelta(seconds=30),
        at=corrected_at + timedelta(seconds=30),
    )
    assert replayed.checkpoint_digest == rebuilt.checkpoint_digest
    projection = restarted_projection
    stale_v1 = dispatch_for(
        candidate_v1,
        reviewed_v1,
        incident_v1_evidence,
        test_v1,
        corrected_at + timedelta(seconds=30),
        extra_refs=(race_event_ref,),
        nonce="stale-v1",
    )
    stale_payload = ProcedureActionableGuidanceResult.model_validate(stale_v1.output)
    assert stale_payload.status == "abstained"
    assert stale_payload.guidance is None
    assert reviewed_v1.promoted_record.rationale not in str(stale_v1.output)

    candidate_v2 = candidate("2", corrected_at + timedelta(minutes=1))
    test_v2 = _ref("tests/payment-lag-v2")
    reviewed_v2 = review(
        candidate_v2,
        (incident_v2_evidence, test_v2),
        "review:payment-lag:v2",
        corrected_at + timedelta(minutes=1),
    )
    projection.rebuild(
        projection_id="company-incident-response",
        valid_at=corrected_at + timedelta(minutes=1),
        known_at=corrected_at + timedelta(minutes=1),
        at=corrected_at + timedelta(minutes=1),
    )
    projection.clock = lambda: corrected_at + timedelta(minutes=1)
    current_v2 = dispatch_for(
        candidate_v2,
        reviewed_v2,
        incident_v2_evidence,
        test_v2,
        corrected_at + timedelta(minutes=1),
        nonce="current-v2",
    )
    v2_payload = ProcedureActionableGuidanceResult.model_validate(current_v2.output)
    assert v2_payload.status == "guidance"
    assert v2_payload.guidance is not None
    assert v2_payload.guidance.reviewed_ref == procedure_record_ref(
        reviewed_v2.promoted_record
    )
    assert v2_payload.guidance.evidence_refs == (
        incident_v2_evidence,
        test_v2,
    )
    stale_again = dispatch_for(
        candidate_v1,
        reviewed_v1,
        incident_v1_evidence,
        test_v1,
        corrected_at + timedelta(minutes=1),
        extra_refs=(race_event_ref,),
        nonce="stale-v1-after-rereview",
    )
    assert ProcedureActionableGuidanceResult.model_validate(
        stale_again.output
    ).status == "abstained"
