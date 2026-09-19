from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from krail.provider.v1 import ResourceRef
from rail.authorized_context import HostedAccessContextAuthorizer
from rail.company_knowledge import (
    COMPANY_EFFECTIVE_TIME_OPERATOR_ID,
    COMPANY_OPERATOR_VERSION,
    COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
    COMPANY_POLICY_QUERY_OPERATOR_ID,
    OWNERSHIP_SCHEMA,
    POLICY_SCHEMA,
    SERVICE_SCHEMA,
    TEAM_SCHEMA,
    CompanyKnowledgeService,
    EffectivePolicyQuery,
    EffectiveTimeValidationRequest,
    ServiceOwnershipAnswer,
    ServiceOwnershipQuery,
    company_knowledge_extension,
    CompanyOperationalGuidanceRequest,
    CompanyOperationalGuidanceService,
    register_company_knowledge_extension,
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
from rail.extension_registry import DomainExtensionRegistry, verify_invocation_integrity
from rail.hosted.access import AccessClaims, AccessContextAuthority, MemoryRevocationRegistry, PacketRequestBinding
from rail.company_guidance_packet import (
    CompanyGuidancePacketCreateRequest,
    CompanyGuidancePacketReadRequest,
    CompanyGuidancePacketService,
)
from rail.capability_publication import company_guidance_packet_descriptor
from rail.procedure_projection import TemporalProjectionService
from rail.robotics_world_memory import (
    ActionFreshnessQuery,
    ObjectStateQuery,
    Pose,
    TabletopWorldMemory,
    WorldObject,
    register_world_memory_extension,
)
from rail.temporal_records import TemporalRecord, create_temporal_record


MONDAY = datetime(2026, 9, 7, 9, tzinfo=UTC)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)
THURSDAY = MONDAY + timedelta(days=3)
FRIDAY = MONDAY + timedelta(days=4)


def _ref(name: str, *, authority: str = "fixture://company", version: str = "1") -> ResourceRef:
    return ResourceRef(
        authority=authority,
        resource_type="evidence",
        resource_id=name,
        version=version,
        digest="sha256:" + sha256(f"{authority}:{name}:{version}".encode()).hexdigest(),
    )


class ExactProjectionWriter:
    def __init__(self, record: TemporalRecord) -> None:
        self.record_digest = record.record_digest

    def authorize(self, record: TemporalRecord, *, at: datetime) -> None:
        if record.record_digest != self.record_digest or at.tzinfo is None:
            raise PermissionError("fixture projection write denied")


class ExactInvalidationAuthorizer:
    def __init__(self, target: ResourceRef) -> None:
        self.target = target.exact_key

    def authorize_invalidation(
        self,
        event_id: str,
        changed_ref: ResourceRef,
        event_digest: str,
        *,
        at: datetime,
    ) -> None:
        if (
            not event_id
            or changed_ref.exact_key != self.target
            or not event_digest.startswith("sha256:")
            or at.tzinfo is None
        ):
            raise PermissionError("fixture invalidation denied")


class LocalReader:
    def authorize(self, ref: ResourceRef) -> None:
        if not ref.digest.startswith("sha256:"):
            raise PermissionError("invalid exact ref")


def _record(
    *,
    schema: str,
    entity_id: str,
    revision: str,
    authority: str,
    kind: str,
    payload: dict[str, object],
    valid_from: datetime,
    recorded_at: datetime,
    source: ResourceRef,
    ingested_at: datetime | None = None,
    supersedes_digest: str | None = None,
    freshness: str = "current",
    visibility: str = "internal",
    writer_family: str = "company-import",
) -> TemporalRecord:
    return create_temporal_record(
        record_id=f"{schema}:{entity_id}:{authority}:{revision}",
        entity_id=entity_id,
        entity_authority="company://fixture/acme",
        payload_schema=schema,
        payload_schema_version="1.0.0",
        kind=kind,
        authority=authority,
        writer_family=writer_family,
        valid_from=valid_from,
        recorded_at=recorded_at,
        ingested_at=ingested_at,
        source_refs=(source,),
        revision=revision,
        freshness=freshness,
        visibility=visibility,
        payload=payload,
        supersedes_digest=supersedes_digest,
    )


def _company_fixture(path: Path):
    projection = TemporalProjectionService(
        str(path), tenant_id="acme", project_id="platform", clock=lambda: THURSDAY
    )
    service = CompanyKnowledgeService(projection)
    catalog = "catalog://acme"
    policy_authority = "policy://acme/board"
    chat = "chat://acme"

    records: dict[str, TemporalRecord] = {}
    for team_id, display_name in (("team-red", "Red Team"), ("team-blue", "Blue Team")):
        source = _ref(f"teams/{team_id}", authority=catalog)
        records[team_id] = _record(
            schema=TEAM_SCHEMA,
            entity_id=team_id,
            revision="1",
            authority=catalog,
            kind="approved_state",
            payload={
                "team_id": team_id,
                "display_name": display_name,
                "source_authority": catalog,
            },
            valid_from=MONDAY,
            recorded_at=MONDAY,
            source=source,
        )
    for service_id, display_name in (
        ("service-alpha", "Alpha API"),
        ("service-beta", "Beta Worker"),
    ):
        source = _ref(f"services/{service_id}", authority=catalog)
        records[service_id] = _record(
            schema=SERVICE_SCHEMA,
            entity_id=service_id,
            revision="1",
            authority=catalog,
            kind="approved_state",
            payload={
                "service_id": service_id,
                "display_name": display_name,
                "source_authority": catalog,
            },
            valid_from=MONDAY,
            recorded_at=MONDAY,
            source=source,
        )

    alpha_old_source = _ref("ownership/service-alpha/old", authority=catalog)
    records["alpha-old"] = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="service-alpha",
        revision="1",
        authority=catalog,
        kind="approved_state",
        payload={
            "service_id": "service-alpha",
            "owner_team_id": "team-red",
            "assertion_status": "approved",
            "source_authority": catalog,
        },
        valid_from=MONDAY,
        recorded_at=MONDAY,
        source=alpha_old_source,
    )
    alpha_transfer_source = _ref(
        "ownership/service-alpha/transfer", authority=catalog, version="2"
    )
    records["alpha-transfer"] = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="service-alpha",
        revision="2",
        authority=catalog,
        kind="approved_state",
        payload={
            "service_id": "service-alpha",
            "owner_team_id": "team-blue",
            "assertion_status": "approved",
            "source_authority": catalog,
        },
        valid_from=TUESDAY,
        recorded_at=THURSDAY,
        ingested_at=THURSDAY,
        source=alpha_transfer_source,
        supersedes_digest=records["alpha-old"].record_digest,
    )
    records["alpha-report"] = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="service-alpha",
        revision="chat-1",
        authority=chat,
        kind="reported_claim",
        payload={
            "service_id": "service-alpha",
            "owner_team_id": "team-red",
            "assertion_status": "reported",
            "source_authority": chat,
        },
        valid_from=WEDNESDAY,
        recorded_at=WEDNESDAY,
        source=_ref("chat/ownership-alpha", authority=chat),
        writer_family="chat-import",
    )
    records["beta"] = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="service-beta",
        revision="1",
        authority=catalog,
        kind="approved_state",
        payload={
            "service_id": "service-beta",
            "owner_team_id": "team-blue",
            "assertion_status": "approved",
            "source_authority": catalog,
        },
        valid_from=MONDAY,
        recorded_at=MONDAY,
        source=_ref("ownership/service-beta", authority=catalog),
    )

    records["policy-draft"] = _record(
        schema=POLICY_SCHEMA,
        entity_id="deploy-policy",
        revision="draft-1",
        authority=policy_authority,
        kind="proposed_change",
        payload={
            "policy_id": "deploy-policy",
            "scope": "production",
            "rule": "Draft: one reviewer.",
            "assertion_status": "draft",
            "source_authority": policy_authority,
        },
        valid_from=MONDAY,
        recorded_at=MONDAY,
        source=_ref("policy/deploy/draft", authority=policy_authority),
        writer_family="policy-import",
    )
    records["policy-approved"] = _record(
        schema=POLICY_SCHEMA,
        entity_id="deploy-policy",
        revision="approved-1",
        authority=policy_authority,
        kind="approved_state",
        payload={
            "policy_id": "deploy-policy",
            "scope": "production",
            "rule": "Two reviewers are required.",
            "assertion_status": "approved",
            "source_authority": policy_authority,
        },
        valid_from=TUESDAY,
        recorded_at=THURSDAY,
        source=_ref("policy/deploy/approved", authority=policy_authority),
        supersedes_digest=records["policy-draft"].record_digest,
        writer_family="policy-import",
    )
    records["policy-chat"] = _record(
        schema=POLICY_SCHEMA,
        entity_id="deploy-policy",
        revision="chat-1",
        authority=chat,
        kind="reported_claim",
        payload={
            "policy_id": "deploy-policy",
            "scope": "production",
            "rule": "Chat says review is optional.",
            "assertion_status": "reported",
            "source_authority": chat,
        },
        valid_from=THURSDAY,
        recorded_at=THURSDAY,
        source=_ref("chat/deploy-policy", authority=chat),
        writer_family="chat-import",
    )
    for record in records.values():
        service.ingest(
            record,
            at=record.ingested_at or record.recorded_at,
            writer=ExactProjectionWriter(record),
        )
    return service, records


def _all_record_refs(service: CompanyKnowledgeService, records) -> tuple[ResourceRef, ...]:
    refs = []
    for record in records:
        refs.extend(
            (
                service.projection.record_ref(record),
                *record.source_refs,
                *record.provenance_refs,
            )
        )
    return tuple(dict.fromkeys(refs))


def _query_ref(name: str) -> ResourceRef:
    return _ref(name, authority="client://acme")


def _registered_company(
    service: CompanyKnowledgeService,
    refs: tuple[ResourceRef, ...],
    *,
    classifications: tuple[str, ...] = ("internal",),
):
    descriptor = company_knowledge_extension()
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"reader": b"company-initial-proof"},
        issuer="https://control.example.test",
        revocations=revocations,
    )
    readers = {}
    for operator in descriptor.operators:
        claims = AccessClaims(
            issuer="https://control.example.test",
            tenant_id=service.tenant_id,
            project_id=service.project_id,
            subject="reader/company-knowledge",
            delegator="user/company-admin",
            delegation_id=f"delegation/{operator.operator_id}",
            capability_id=operator.operator_id,
            capability_version=operator.version,
            capability_digest=operator.descriptor_digest,
            actions=("context.read",),
            source_ids=tuple(dict.fromkeys(ref.resource_id for ref in refs)),
            classifications=classifications,
            policy_digest="sha256:" + "d" * 64,
            issued_at=MONDAY,
            not_before=MONDAY,
            expires_at=FRIDAY + timedelta(days=1),
            nonce=f"nonce/{operator.operator_id}",
        )
        context = authority.issue(claims, key_id="reader")
        readers[operator.operator_id] = HostedAccessContextAuthorizer(
            authority, context, exact_refs=refs, clock=lambda: FRIDAY
        )
    registry = DomainExtensionRegistry()
    register_company_knowledge_extension(registry, service, readers)
    return registry, descriptor, readers, revocations


def _dispatch(registry, readers, operator_id, ref, payload):
    return registry.dispatch(
        operator_id,
        COMPANY_OPERATOR_VERSION,
        ((ref, payload),),
        config={},
        authorizer=readers[operator_id],
    )


def test_registered_company_journey_separates_valid_known_authority_and_policy(tmp_path):
    service, records = _company_fixture(tmp_path / "company.json")
    query_refs = tuple(
        _query_ref(name)
        for name in (
            "queries/alpha-known-tuesday",
            "queries/alpha-known-thursday",
            "queries/alpha-current-thursday",
            "queries/beta",
            "queries/policy-draft",
            "queries/policy-approved",
            "queries/validate-known",
            "queries/validate-unknown",
        )
    )
    refs = (*_all_record_refs(service, records.values()), *query_refs)
    registry, descriptor, readers, _revocations = _registered_company(service, refs)
    robot_memory = TabletopWorldMemory(clock=lambda: THURSDAY)
    register_world_memory_extension(registry, robot_memory, LocalReader())
    assert {item.extension_id for item in registry.discover()} == {
        "company.knowledge",
        "robotics.world-memory",
    }
    assert {item.operator_id for item in descriptor.operators} == {
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        COMPANY_EFFECTIVE_TIME_OPERATOR_ID,
        COMPANY_POLICY_QUERY_OPERATOR_ID,
    }

    before = _dispatch(
        registry,
        readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        query_refs[0],
        ServiceOwnershipQuery(
            service_id="service-alpha", valid_at=TUESDAY, known_at=TUESDAY
        ).model_dump(mode="json"),
    )
    before_answer = ServiceOwnershipAnswer.model_validate(before.output)
    assert (before_answer.status, before_answer.owner_team_id) == ("owned", "team-red")

    corrected = _dispatch(
        registry,
        readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        query_refs[1],
        ServiceOwnershipQuery(
            service_id="service-alpha", valid_at=TUESDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    corrected_answer = ServiceOwnershipAnswer.model_validate(corrected.output)
    assert (corrected_answer.status, corrected_answer.owner_team_id) == (
        "owned",
        "team-blue",
    )
    assert records["alpha-old"].record_digest != records["alpha-transfer"].record_digest
    assert service.projection.store.get(
        "acme", "platform", "temporal_record", records["alpha-old"].record_digest
    ) is not None
    corrected_keys = {ref.exact_key for ref in corrected.input_refs}
    assert {
        query_refs[1].exact_key,
        service.projection.record_ref(records["alpha-transfer"]).exact_key,
        records["alpha-transfer"].source_refs[0].exact_key,
    } <= corrected_keys
    ownership_operator = next(
        item
        for item in descriptor.operators
        if item.operator_id == COMPANY_OWNERSHIP_QUERY_OPERATOR_ID
    )
    verify_invocation_integrity(corrected, ownership_operator)
    assert corrected.operator_version == COMPANY_OPERATOR_VERSION
    direct_corrected, direct_corrected_refs = service.owner(
        ServiceOwnershipQuery(
            service_id="service-alpha", valid_at=TUESDAY, known_at=THURSDAY
        ),
        reader=readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID],
    )
    assert direct_corrected == corrected_answer
    assert direct_corrected_refs

    replayed_service = CompanyKnowledgeService(
        TemporalProjectionService(
            str(service.projection.store.path),
            tenant_id="acme",
            project_id="platform",
            clock=lambda: THURSDAY,
        )
    )
    replayed_registry, _replayed_descriptor, replayed_readers, _ = (
        _registered_company(replayed_service, refs)
    )
    replayed = _dispatch(
        replayed_registry,
        replayed_readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        query_refs[1],
        ServiceOwnershipQuery(
            service_id="service-alpha", valid_at=TUESDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    assert replayed.output == corrected.output
    assert replayed.lineage_digest == corrected.lineage_digest

    current_with_report = _dispatch(
        registry,
        readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        query_refs[2],
        ServiceOwnershipQuery(
            service_id="service-alpha", valid_at=THURSDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    assert current_with_report.output["owner_team_id"] == "team-blue"
    assert service.projection.record_ref(records["alpha-report"]) in current_with_report.input_refs

    beta = _dispatch(
        registry,
        readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        query_refs[3],
        ServiceOwnershipQuery(
            service_id="service-beta", valid_at=THURSDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    assert beta.output["owner_team_id"] == "team-blue"

    draft_only = _dispatch(
        registry,
        readers,
        COMPANY_POLICY_QUERY_OPERATOR_ID,
        query_refs[4],
        EffectivePolicyQuery(
            policy_id="deploy-policy",
            scope="production",
            valid_at=TUESDAY,
            known_at=TUESDAY,
        ).model_dump(mode="json"),
    )
    assert draft_only.output["status"] == "unknown"
    approved = _dispatch(
        registry,
        readers,
        COMPANY_POLICY_QUERY_OPERATOR_ID,
        query_refs[5],
        EffectivePolicyQuery(
            policy_id="deploy-policy",
            scope="production",
            valid_at=THURSDAY,
            known_at=THURSDAY,
        ).model_dump(mode="json"),
    )
    assert approved.output["rule"] == "Two reviewers are required."
    assert "Chat says review is optional" not in str(approved.output)
    assert service.projection.record_ref(records["policy-chat"]) in approved.input_refs
    direct_policy, direct_policy_refs = service.effective_policy(
        EffectivePolicyQuery(
            policy_id="deploy-policy",
            scope="production",
            valid_at=THURSDAY,
            known_at=THURSDAY,
        ),
        reader=readers[COMPANY_POLICY_QUERY_OPERATOR_ID],
    )
    assert direct_policy.rule == "Two reviewers are required."
    assert direct_policy_refs

    known = _dispatch(
        registry,
        readers,
        COMPANY_EFFECTIVE_TIME_OPERATOR_ID,
        query_refs[6],
        EffectiveTimeValidationRequest(
            record=records["alpha-transfer"],
            valid_at=TUESDAY,
            known_at=THURSDAY,
        ).model_dump(mode="json"),
    )
    not_known = _dispatch(
        registry,
        readers,
        COMPANY_EFFECTIVE_TIME_OPERATOR_ID,
        query_refs[7],
        EffectiveTimeValidationRequest(
            record=records["alpha-transfer"],
            valid_at=TUESDAY,
            known_at=TUESDAY,
        ).model_dump(mode="json"),
    )
    assert known.output["status"] == "effective"
    assert not_known.output["status"] == "not-known"
    direct_effective = service.validate_effective_time(
        EffectiveTimeValidationRequest(
            record=records["alpha-transfer"],
            valid_at=TUESDAY,
            known_at=THURSDAY,
        ),
        reader=readers[COMPANY_EFFECTIVE_TIME_OPERATOR_ID],
    )
    assert direct_effective.status == "effective"


def test_company_conflict_restriction_denial_and_descriptor_bound_authority(tmp_path):
    service, records = _company_fixture(tmp_path / "company.json")
    board = "decision://acme/board"
    conflict = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="service-alpha",
        revision="board-1",
        authority=board,
        kind="approved_state",
        payload={
            "service_id": "service-alpha",
            "owner_team_id": "team-red",
            "assertion_status": "approved",
            "source_authority": board,
        },
        valid_from=TUESDAY,
        recorded_at=FRIDAY,
        source=_ref("decision/ownership-alpha", authority=board),
        writer_family="decision-import",
    )
    restricted = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="service-beta",
        revision="restricted-1",
        authority=board,
        kind="approved_state",
        payload={
            "service_id": "service-beta",
            "owner_team_id": "team-red",
            "assertion_status": "approved",
            "source_authority": board,
        },
        valid_from=TUESDAY,
        recorded_at=FRIDAY,
        source=_ref("decision/ownership-beta-restricted", authority=board),
        visibility="restricted",
        writer_family="decision-import",
    )
    for record in (conflict, restricted):
        service.ingest(record, at=FRIDAY, writer=ExactProjectionWriter(record))
        records[record.revision] = record
    conflict_ref = _query_ref("queries/conflict")
    restricted_ref = _query_ref("queries/restricted")
    denied_ref = _query_ref("queries/denied")
    refs = (
        *_all_record_refs(service, records.values()),
        conflict_ref,
        restricted_ref,
    )
    registry, descriptor, readers, _ = _registered_company(service, refs)
    conflict_result = _dispatch(
        registry,
        readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        conflict_ref,
        ServiceOwnershipQuery(
            service_id="service-alpha", valid_at=FRIDAY, known_at=FRIDAY
        ).model_dump(mode="json"),
    )
    assert conflict_result.output["status"] == "conflict"
    assert conflict_result.output["owner_team_id"] is None

    restricted_result = _dispatch(
        registry,
        readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        restricted_ref,
        ServiceOwnershipQuery(
            service_id="service-beta", valid_at=FRIDAY, known_at=FRIDAY
        ).model_dump(mode="json"),
    )
    assert restricted_result.output["status"] == "unknown"
    assert restricted_result.output["reason"] == "insufficient-authorized-evidence"
    assert "team-red" not in str(restricted_result.output)
    assert service.projection.record_ref(restricted) not in restricted_result.input_refs

    with pytest.raises(PermissionError, match="extension access denied"):
        _dispatch(
            registry,
            readers,
            COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
            denied_ref,
            ServiceOwnershipQuery(
                service_id="service-alpha", valid_at=FRIDAY, known_at=FRIDAY
            ).model_dump(mode="json"),
        )

    reader = readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID]
    authority_mismatches = {
        "wrong-tenant": {"tenant_id": "other-company"},
        "wrong-project": {"project_id": "other-project"},
        "wrong-operator": {"capability_id": "company.other.query"},
        "wrong-version": {"capability_version": "2.0.0"},
        "wrong-digest": {"capability_digest": "sha256:" + "0" * 64},
        "authority-expansion": {"source_ids": ("*",)},
    }
    for label, updates in authority_mismatches.items():
        bad_claims = reader.context.claims.model_copy(
            update={**updates, "nonce": label}
        )
        bad_context = reader.authority.issue(bad_claims, key_id="reader")
        bad_reader = HostedAccessContextAuthorizer(
            reader.authority, bad_context, exact_refs=refs, clock=lambda: FRIDAY
        )
        bad_registry = DomainExtensionRegistry()
        bad_readers = dict(readers)
        bad_readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID] = bad_reader
        register_company_knowledge_extension(bad_registry, service, bad_readers)
        with pytest.raises(PermissionError, match="company knowledge access denied"):
            service.owner(
                ServiceOwnershipQuery(
                    service_id="service-alpha", valid_at=FRIDAY, known_at=FRIDAY
                ),
                reader=bad_reader,
            )
        if label == "wrong-tenant":
            with pytest.raises(
                PermissionError, match="company knowledge access denied"
            ):
                service.effective_policy(
                    EffectivePolicyQuery(
                        policy_id="deploy-policy",
                        scope="production",
                        valid_at=FRIDAY,
                        known_at=FRIDAY,
                    ),
                    reader=bad_reader,
                )
            with pytest.raises(
                PermissionError, match="company knowledge access denied"
            ):
                service.validate_effective_time(
                    EffectiveTimeValidationRequest(
                        record=records["alpha-transfer"],
                        valid_at=FRIDAY,
                        known_at=FRIDAY,
                    ),
                    reader=bad_reader,
                )
        with pytest.raises(PermissionError, match="company knowledge access denied"):
            _dispatch(
                bad_registry,
                bad_readers,
                COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
                conflict_ref,
                ServiceOwnershipQuery(
                    service_id="service-alpha", valid_at=FRIDAY, known_at=FRIDAY
                ).model_dump(mode="json"),
            )

    with pytest.raises(PermissionError, match="company knowledge access denied"):
        service.effective_policy(
            EffectivePolicyQuery(
                policy_id="deploy-policy",
                scope="production",
                valid_at=FRIDAY,
                known_at=FRIDAY,
            ),
            reader=readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID],
        )
    with pytest.raises(PermissionError, match="company knowledge access denied"):
        service.validate_effective_time(
            EffectiveTimeValidationRequest(
                record=records["alpha-transfer"],
                valid_at=FRIDAY,
                known_at=FRIDAY,
            ),
            reader=readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID],
        )


def test_company_superseded_source_dependency_is_bitemporally_stale_until_replaced(
    tmp_path,
):
    path = tmp_path / "company-source-correction.json"
    projection = TemporalProjectionService(
        str(path), tenant_id="acme", project_id="platform", clock=lambda: THURSDAY
    )
    service = CompanyKnowledgeService(projection)
    catalog = "catalog://acme"
    policy_authority = "policy://acme/board"
    team = _record(
        schema=TEAM_SCHEMA,
        entity_id="team-a",
        revision="1",
        authority=catalog,
        kind="approved_state",
        payload={
            "team_id": "team-a",
            "display_name": "Team A",
            "source_authority": catalog,
        },
        valid_from=MONDAY,
        recorded_at=MONDAY,
        source=_ref("teams/team-a", authority=catalog),
    )
    service_v1 = _record(
        schema=SERVICE_SCHEMA,
        entity_id="svc-a",
        revision="1",
        authority=catalog,
        kind="approved_state",
        payload={
            "service_id": "svc-a",
            "display_name": "Service A",
            "source_authority": catalog,
        },
        valid_from=MONDAY,
        recorded_at=MONDAY,
        source=_ref("services/svc-a", authority=catalog),
    )
    ownership_v1 = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="svc-a",
        revision="owner-1",
        authority=catalog,
        kind="approved_state",
        payload={
            "service_id": "svc-a",
            "owner_team_id": "team-a",
            "assertion_status": "approved",
            "source_authority": catalog,
        },
        valid_from=MONDAY,
        recorded_at=MONDAY,
        source=projection.record_ref(service_v1),
        writer_family="ownership-review",
    )
    policy_v1 = _record(
        schema=POLICY_SCHEMA,
        entity_id="svc-a",
        revision="policy-1",
        authority=policy_authority,
        kind="approved_state",
        payload={
            "policy_id": "svc-a",
            "scope": "production",
            "rule": "Team A reviews changes.",
            "assertion_status": "approved",
            "source_authority": policy_authority,
        },
        valid_from=MONDAY,
        recorded_at=MONDAY,
        source=projection.record_ref(service_v1),
        writer_family="policy-review",
    )
    for record in (team, service_v1, ownership_v1, policy_v1):
        service.ingest(record, at=record.recorded_at, writer=ExactProjectionWriter(record))
    projection.rebuild(
        projection_id="company-knowledge",
        valid_at=THURSDAY,
        known_at=THURSDAY,
        at=THURSDAY,
    )

    historical_ref = _query_ref("queries/source-correction-historical")
    current_ref = _query_ref("queries/source-correction-current")
    policy_ref = _query_ref("queries/source-correction-policy")
    initial_records = (team, service_v1, ownership_v1, policy_v1)
    initial_refs = (
        *_all_record_refs(service, initial_records),
        historical_ref,
        current_ref,
        policy_ref,
    )
    initial_registry, _, initial_readers, _ = _registered_company(
        service, initial_refs
    )
    initial_owner = _dispatch(
        initial_registry,
        initial_readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        current_ref,
        ServiceOwnershipQuery(
            service_id="svc-a", valid_at=THURSDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    initial_policy = _dispatch(
        initial_registry,
        initial_readers,
        COMPANY_POLICY_QUERY_OPERATOR_ID,
        policy_ref,
        EffectivePolicyQuery(
            policy_id="svc-a",
            scope="production",
            valid_at=THURSDAY,
            known_at=THURSDAY,
        ).model_dump(mode="json"),
    )
    assert initial_owner.output["status"] == "owned"
    assert initial_policy.output["status"] == "effective"

    service_v2 = _record(
        schema=SERVICE_SCHEMA,
        entity_id="svc-a",
        revision="2",
        authority=catalog,
        kind="approved_state",
        payload={
            "service_id": "svc-a",
            "display_name": "Service A corrected",
            "source_authority": catalog,
        },
        valid_from=FRIDAY,
        recorded_at=FRIDAY,
        source=_ref("services/svc-a-correction", authority=catalog, version="2"),
        supersedes_digest=service_v1.record_digest,
    )
    projection.clock = lambda: FRIDAY
    service.ingest(service_v2, at=FRIDAY, writer=ExactProjectionWriter(service_v2))
    dirty = set(projection.dirty_outputs("company-knowledge"))
    assert {
        projection.record_ref(service_v2),
        projection.record_ref(ownership_v1),
        projection.record_ref(policy_v1),
    } <= dirty

    records_after_correction = (*initial_records, service_v2)
    refs_after_correction = (
        *_all_record_refs(service, records_after_correction),
        historical_ref,
        current_ref,
        policy_ref,
    )
    corrected_registry, _, corrected_readers, _ = _registered_company(
        service, refs_after_correction
    )
    historical = _dispatch(
        corrected_registry,
        corrected_readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        historical_ref,
        ServiceOwnershipQuery(
            service_id="svc-a", valid_at=THURSDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    assert historical.output["status"] == "owned"
    assert historical.output["owner_team_id"] == "team-a"
    historical_policy = _dispatch(
        corrected_registry,
        corrected_readers,
        COMPANY_POLICY_QUERY_OPERATOR_ID,
        policy_ref,
        EffectivePolicyQuery(
            policy_id="svc-a",
            scope="production",
            valid_at=THURSDAY,
            known_at=THURSDAY,
        ).model_dump(mode="json"),
    )
    assert historical_policy.output["status"] == "effective"
    assert historical_policy.output["rule"] == "Team A reviews changes."

    def assert_current_is_stale(current_service, current_registry, current_readers):
        owner = _dispatch(
            current_registry,
            current_readers,
            COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
            current_ref,
            ServiceOwnershipQuery(
                service_id="svc-a", valid_at=FRIDAY, known_at=FRIDAY
            ).model_dump(mode="json"),
        )
        policy = _dispatch(
            current_registry,
            current_readers,
            COMPANY_POLICY_QUERY_OPERATOR_ID,
            policy_ref,
            EffectivePolicyQuery(
                policy_id="svc-a",
                scope="production",
                valid_at=FRIDAY,
                known_at=FRIDAY,
            ).model_dump(mode="json"),
        )
        assert owner.output["status"] == "stale"
        assert owner.output["owner_team_id"] is None
        assert policy.output["status"] == "stale"
        assert policy.output["rule"] is None
        direct_owner, _ = current_service.owner(
            ServiceOwnershipQuery(
                service_id="svc-a", valid_at=FRIDAY, known_at=FRIDAY
            ),
            reader=current_readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID],
        )
        assert direct_owner.status == "stale"

    assert_current_is_stale(service, corrected_registry, corrected_readers)
    restarted_projection = TemporalProjectionService(
        str(path), tenant_id="acme", project_id="platform", clock=lambda: FRIDAY
    )
    restarted = CompanyKnowledgeService(restarted_projection)
    restarted_registry, _, restarted_readers, _ = _registered_company(
        restarted, refs_after_correction
    )
    assert_current_is_stale(restarted, restarted_registry, restarted_readers)

    restarted_projection.recompute(
        projection_id="company-knowledge",
        valid_at=FRIDAY,
        known_at=FRIDAY,
        at=FRIDAY,
    )
    after_recompute_registry, _, after_recompute_readers, _ = _registered_company(
        restarted, refs_after_correction
    )
    assert restarted_projection.dirty_outputs("company-knowledge") == ()
    assert_current_is_stale(
        restarted, after_recompute_registry, after_recompute_readers
    )
    replayed_historical = _dispatch(
        after_recompute_registry,
        after_recompute_readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        historical_ref,
        ServiceOwnershipQuery(
            service_id="svc-a", valid_at=THURSDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    replayed_historical_policy = _dispatch(
        after_recompute_registry,
        after_recompute_readers,
        COMPANY_POLICY_QUERY_OPERATOR_ID,
        policy_ref,
        EffectivePolicyQuery(
            policy_id="svc-a",
            scope="production",
            valid_at=THURSDAY,
            known_at=THURSDAY,
        ).model_dump(mode="json"),
    )
    assert replayed_historical.output["status"] == "owned"
    assert replayed_historical.output["owner_team_id"] == "team-a"
    assert replayed_historical_policy.output["status"] == "effective"
    assert replayed_historical_policy.output["rule"] == "Team A reviews changes."

    reviewed_at = FRIDAY + timedelta(minutes=1)
    ownership_v2 = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="svc-a",
        revision="owner-2",
        authority=catalog,
        kind="approved_state",
        payload={
            "service_id": "svc-a",
            "owner_team_id": "team-a",
            "assertion_status": "approved",
            "source_authority": catalog,
        },
        valid_from=FRIDAY,
        recorded_at=reviewed_at,
        source=restarted_projection.record_ref(service_v2),
        supersedes_digest=ownership_v1.record_digest,
        writer_family="ownership-review",
    )
    policy_v2 = _record(
        schema=POLICY_SCHEMA,
        entity_id="svc-a",
        revision="policy-2",
        authority=policy_authority,
        kind="approved_state",
        payload={
            "policy_id": "svc-a",
            "scope": "production",
            "rule": "Team A reviews corrected-service changes.",
            "assertion_status": "approved",
            "source_authority": policy_authority,
        },
        valid_from=FRIDAY,
        recorded_at=reviewed_at,
        source=restarted_projection.record_ref(service_v2),
        supersedes_digest=policy_v1.record_digest,
        writer_family="policy-review",
    )
    restarted_projection.clock = lambda: reviewed_at
    for record in (ownership_v2, policy_v2):
        restarted.ingest(record, at=reviewed_at, writer=ExactProjectionWriter(record))
    restarted_projection.recompute(
        projection_id="company-knowledge",
        valid_at=reviewed_at,
        known_at=reviewed_at,
        at=reviewed_at,
    )
    final_refs = (
        *_all_record_refs(
            restarted,
            (*records_after_correction, ownership_v2, policy_v2),
        ),
        current_ref,
        policy_ref,
    )
    final_registry, _, final_readers, _ = _registered_company(
        restarted, final_refs
    )
    final_owner = _dispatch(
        final_registry,
        final_readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        current_ref,
        ServiceOwnershipQuery(
            service_id="svc-a", valid_at=reviewed_at, known_at=reviewed_at
        ).model_dump(mode="json"),
    )
    final_policy = _dispatch(
        final_registry,
        final_readers,
        COMPANY_POLICY_QUERY_OPERATOR_ID,
        policy_ref,
        EffectivePolicyQuery(
            policy_id="svc-a",
            scope="production",
            valid_at=reviewed_at,
            known_at=reviewed_at,
        ).model_dump(mode="json"),
    )
    assert final_owner.output["status"] == "owned"
    assert final_owner.output["owner_team_id"] == "team-a"
    assert final_policy.output["status"] == "effective"
    assert final_policy.output["rule"] == "Team A reviews corrected-service changes."


def test_company_external_dirty_intervals_remain_historical_after_reenqueue(tmp_path):
    """K21-DIRTY-HISTORY: later queue work cannot rewrite past abstention."""

    service, records = _company_fixture(tmp_path / "company-dirty-history.json")
    projection = service.projection
    first = THURSDAY + timedelta(hours=1)
    historical_known_at = first + timedelta(minutes=30)
    recomputed = first + timedelta(hours=2)
    second = first + timedelta(hours=3)
    owner_source = records["alpha-transfer"].source_refs[0]
    policy_source = records["policy-approved"].source_refs[0]
    owner_query_ref = _query_ref("queries/dirty-history-owner")
    policy_query_ref = _query_ref("queries/dirty-history-policy")
    refs = (
        *_all_record_refs(service, records.values()),
        owner_query_ref,
        policy_query_ref,
    )
    registry, _descriptor, readers, _revocations = _registered_company(service, refs)

    def statuses(
        active_registry, active_readers, *, known_at: datetime = historical_known_at
    ) -> tuple[str, str]:
        owner = _dispatch(
            active_registry,
            active_readers,
            COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
            owner_query_ref,
            ServiceOwnershipQuery(
                service_id="service-alpha",
                valid_at=THURSDAY,
                known_at=known_at,
            ).model_dump(mode="json"),
        )
        policy = _dispatch(
            active_registry,
            active_readers,
            COMPANY_POLICY_QUERY_OPERATOR_ID,
            policy_query_ref,
            EffectivePolicyQuery(
                policy_id="deploy-policy",
                scope="production",
                valid_at=THURSDAY,
                known_at=known_at,
            ).model_dump(mode="json"),
        )
        return owner.output["status"], policy.output["status"]

    for source in (owner_source, policy_source):
        projection.mark_dirty(
            source, projection_id="company-knowledge", reason="first", at=first
        )
    assert statuses(registry, readers) == ("stale", "stale")

    projection.recompute(
        projection_id="company-knowledge",
        valid_at=THURSDAY,
        known_at=recomputed,
        at=recomputed,
    )
    assert statuses(registry, readers) == ("stale", "stale")

    for source in (owner_source, policy_source):
        projection.mark_dirty(
            source, projection_id="company-knowledge", reason="second", at=second
        )
    assert statuses(registry, readers) == ("stale", "stale")
    assert statuses(registry, readers, known_at=second + timedelta(minutes=30)) == (
        "stale",
        "stale",
    )

    restarted_service = CompanyKnowledgeService(
        TemporalProjectionService(
            str(tmp_path / "company-dirty-history.json"),
            tenant_id="acme",
            project_id="platform",
            clock=lambda: second,
        )
    )
    restarted_registry, _descriptor, restarted_readers, _revocations = (
        _registered_company(restarted_service, refs)
    )
    assert statuses(restarted_registry, restarted_readers) == ("stale", "stale")


def test_company_upgrade_seeds_available_legacy_dirty_interval_before_reenqueue(tmp_path):
    """K21-DIRTY-HISTORY: upgrade preserves the exact legacy interval on disk."""

    path = tmp_path / "company-legacy-dirty-history.json"
    service, records = _company_fixture(path)
    projection = service.projection
    first = THURSDAY + timedelta(hours=1)
    historical_known_at = first + timedelta(minutes=30)
    recomputed = first + timedelta(hours=2)
    second = first + timedelta(hours=3)
    sources = (
        records["alpha-transfer"].source_refs[0],
        records["policy-approved"].source_refs[0],
    )
    for source in sources:
        projection.mark_dirty(
            source, projection_id="company-knowledge", reason="legacy-first", at=first
        )
    projection.recompute(
        projection_id="company-knowledge",
        valid_at=THURSDAY,
        known_at=recomputed,
        at=recomputed,
    )

    # Upgrade fault injection: retain the old mutable queue rows while removing
    # the event kind that did not exist in a pre-ledger store.
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["records"] = [
        row
        for row in stored["records"]
        if row["record_kind"] != "procedure_projection_dirty_event"
    ]
    path.write_text(
        json.dumps(stored, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    restarted = CompanyKnowledgeService(
        TemporalProjectionService(
            str(path),
            tenant_id="acme",
            project_id="platform",
            clock=lambda: second,
        )
    )
    refs = _all_record_refs(restarted, records.values())
    _registry, _descriptor, readers, _revocations = _registered_company(
        restarted, refs
    )

    def direct_statuses(known_at: datetime) -> tuple[str, str]:
        owner, _owner_refs = restarted.owner(
            ServiceOwnershipQuery(
                service_id="service-alpha",
                valid_at=THURSDAY,
                known_at=known_at,
            ),
            reader=readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID],
        )
        policy, _policy_refs = restarted.effective_policy(
            EffectivePolicyQuery(
                policy_id="deploy-policy",
                scope="production",
                valid_at=THURSDAY,
                known_at=known_at,
            ),
            reader=readers[COMPANY_POLICY_QUERY_OPERATOR_ID],
        )
        return owner.status, policy.status

    assert direct_statuses(historical_known_at) == ("stale", "stale")
    for source in sources:
        restarted.projection.mark_dirty(
            source, projection_id="company-knowledge", reason="new-after-upgrade", at=second
        )
    assert direct_statuses(historical_known_at) == ("stale", "stale")
    assert direct_statuses(second + timedelta(minutes=30)) == ("stale", "stale")


def test_company_deleted_evidence_and_live_revocation_fail_closed_after_restart(tmp_path):
    path = tmp_path / "company.json"
    service, records = _company_fixture(path)
    query_ref = _query_ref("queries/after-delete")
    stale_query_ref = _query_ref("queries/stale-record")
    catalog = records["beta"].authority
    stale_beta = _record(
        schema=OWNERSHIP_SCHEMA,
        entity_id="service-beta",
        revision="stale-2",
        authority=catalog,
        kind="approved_state",
        payload={
            "service_id": "service-beta",
            "owner_team_id": "team-red",
            "assertion_status": "approved",
            "source_authority": catalog,
        },
        valid_from=THURSDAY,
        recorded_at=THURSDAY,
        source=_ref("ownership/service-beta/stale", authority=catalog),
        supersedes_digest=records["beta"].record_digest,
        freshness="stale",
    )
    service.ingest(stale_beta, at=THURSDAY, writer=ExactProjectionWriter(stale_beta))
    records["beta-stale"] = stale_beta
    refs = (
        *_all_record_refs(service, records.values()),
        query_ref,
        stale_query_ref,
    )
    registry, _descriptor, readers, revocations = _registered_company(service, refs)
    stale_record = _dispatch(
        registry,
        readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        stale_query_ref,
        ServiceOwnershipQuery(
            service_id="service-beta", valid_at=THURSDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    assert stale_record.output["status"] == "stale"
    assert "team-red" not in str(stale_record.output)
    current = _dispatch(
        registry,
        readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        query_ref,
        ServiceOwnershipQuery(
            service_id="service-alpha", valid_at=THURSDAY, known_at=THURSDAY
        ).model_dump(mode="json"),
    )
    assert current.output["owner_team_id"] == "team-blue"

    transfer_source = records["alpha-transfer"].source_refs[0]
    service.projection.clock = lambda: FRIDAY
    service.projection.tombstone(
        transfer_source,
        event_id="delete:ownership-alpha-transfer",
        reason="source evidence deleted under retention policy",
        effective_at=FRIDAY,
        recorded_at=FRIDAY,
        authorizer=ExactInvalidationAuthorizer(transfer_source),
    )
    restarted_projection = TemporalProjectionService(
        str(path), tenant_id="acme", project_id="platform", clock=lambda: FRIDAY
    )
    restarted_projection.rebuild(
        projection_id="company-knowledge",
        valid_at=FRIDAY,
        known_at=FRIDAY,
        at=FRIDAY,
    )
    restarted = CompanyKnowledgeService(restarted_projection)
    restarted_registry, _descriptor, restarted_readers, restarted_revocations = (
        _registered_company(restarted, refs)
    )
    stale = _dispatch(
        restarted_registry,
        restarted_readers,
        COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
        query_ref,
        ServiceOwnershipQuery(
            service_id="service-alpha", valid_at=FRIDAY, known_at=FRIDAY
        ).model_dump(mode="json"),
    )
    assert stale.output["status"] == "stale"
    assert stale.output["owner_team_id"] is None
    assert "team-blue" not in str(stale.output)

    revoked_reader = restarted_readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID]
    restarted_revocations.revoke_context(
        revoked_reader.context.context_digest, revoked_at=FRIDAY
    )
    with pytest.raises(PermissionError, match="extension access denied"):
        _dispatch(
            restarted_registry,
            restarted_readers,
            COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
            query_ref,
            ServiceOwnershipQuery(
                service_id="service-alpha", valid_at=FRIDAY, known_at=FRIDAY
            ).model_dump(mode="json"),
        )
    assert revocations is not restarted_revocations


def test_robotics_registered_object_state_and_freshness_use_persisted_temporal_state(tmp_path):
    path = tmp_path / "robotics.json"
    memory = TabletopWorldMemory(
        str(path), tenant_id="robotics", project_id="table", clock=lambda: MONDAY
    )
    source = _ref("camera/cup", authority="fixture://camera")
    record = memory.record(
        WorldObject(world_id="table-a", object_id="cup", class_label="cup"),
        Pose(
            frame_id="table",
            metres=(0.1, 0.2, 0.0),
            quaternion_xyzw=(0.0, 0.0, 0.0, 1.0),
            observed_at=MONDAY,
            uncertainty_metres=0.01,
            revision="1",
            map_revision="map-1",
        ),
        kind="observation",
        evidence=(source,),
        recorded_at=MONDAY,
    )
    state_query_ref = _query_ref("robotics/object-state")
    freshness_query_ref = _query_ref("robotics/freshness")
    registry = DomainExtensionRegistry()
    descriptor = register_world_memory_extension(registry, memory, LocalReader())
    state = registry.dispatch(
        "robotics.object-state.query",
        "1.0.0",
        (
            (
                state_query_ref,
                ObjectStateQuery(
                    world_id="table-a",
                    object_id="cup",
                    valid_at=MONDAY,
                    known_at=MONDAY,
                    include_estimates=False,
                ).model_dump(mode="json"),
            ),
        ),
        config={},
        authorizer=LocalReader(),
    )
    freshness = registry.dispatch(
        "robotics.object-state.validate-freshness",
        "1.0.0",
        (
            (
                freshness_query_ref,
                ActionFreshnessQuery(
                    world_id="table-a",
                    object_id="cup",
                    valid_at=MONDAY,
                    known_at=MONDAY,
                    required_frame_id="table",
                    required_map_revision="map-1",
                ).model_dump(mode="json"),
            ),
        ),
        config={},
        authorizer=LocalReader(),
    )
    assert state.output["status"] == "observed"
    assert state.output["pose"]["frame_id"] == "table"
    assert freshness.output["status"] == "usable"
    assert {source.exact_key, memory.record_ref(record).exact_key} <= {
        ref.exact_key for ref in state.input_refs
    }
    for result in (state, freshness):
        operator = next(
            item for item in descriptor.operators if item.operator_id == result.operator_id
        )
        verify_invocation_integrity(result, operator)

    invalidated_at = MONDAY + timedelta(minutes=1)
    memory.invalidate_map_revision(
        source,
        reason="camera evidence deleted",
        at=invalidated_at,
    )
    restarted = TabletopWorldMemory(
        str(path),
        tenant_id="robotics",
        project_id="table",
        clock=lambda: invalidated_at,
    )
    restarted_registry = DomainExtensionRegistry()
    register_world_memory_extension(restarted_registry, restarted, LocalReader())
    stale = restarted_registry.dispatch(
        "robotics.object-state.validate-freshness",
        "1.0.0",
        (
            (
                freshness_query_ref,
                ActionFreshnessQuery(
                    world_id="table-a",
                    object_id="cup",
                    valid_at=invalidated_at,
                    known_at=invalidated_at,
                    required_frame_id="table",
                    required_map_revision="map-1",
                ).model_dump(mode="json"),
            ),
        ),
        config={},
        authorizer=LocalReader(),
    )
    assert stale.output["status"] == "needs-refresh"


def test_core_registry_import_does_not_load_optional_domain_modules():
    rail_path = Path(__file__).resolve().parents[1]
    script = f"""
import builtins
import sys
sys.path.insert(0, {str(rail_path)!r})
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in {{'rail.robotics_world_memory', 'rail.company_knowledge'}}:
        raise AssertionError('optional domain import attempted: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import rail.extension_registry
assert 'rail.robotics_world_memory' not in sys.modules
assert 'rail.company_knowledge' not in sys.modules

"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


class _GuidanceAllow:
    def authorize(self, ref, *, at=None):
        return None


class _GuidanceTrust:
    def verify(self, receipt):
        return None


class _GuidanceReview:
    def authorize_review(self, candidate_digest, reviewer_ref, evidence_refs, *, lineage_refs=(), at):
        return None


class _GuidanceInvalidator:
    def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
        return None


def _signed_procedure_reader(candidate, reviewed, evidence, test_artifact):
    decision = reviewed.decision
    decision_ref = ResourceRef(
        authority="krail://procedural-memory",
        resource_type="procedure-review",
        resource_id=decision.decision_id,
        version=decision.schema_version,
        digest=decision.decision_digest,
    )
    refs = tuple(
        dict.fromkeys(
            (
                procedure_record_ref(candidate.record),
                procedure_record_ref(reviewed.promoted_record),
                *candidate.record.command_refs,
                *candidate.record.environment_refs,
                *reviewed.promoted_record.command_refs,
                *reviewed.promoted_record.environment_refs,
                *reviewed.promoted_record.test_evidence_refs,
                decision.reviewer_ref,
                reviewed.promoted_record.review_ref,
                decision_ref,
            )
        )
    )
    authority = AccessContextAuthority(
        {"procedure-reader": b"company-procedure-reader"},
        issuer="https://control.example.test",
        revocations=MemoryRevocationRegistry(),
    )
    claims = AccessClaims(
        issuer="https://control.example.test",
        tenant_id="acme",
        project_id="platform",
        subject="reader/company-knowledge",
        delegator="user/company-admin",
        delegation_id="delegation/company-procedure",
        capability_id="krail.procedure-explanation",
        capability_version="1.1.0",
        capability_digest="sha256:" + "e" * 64,
        actions=("context.read",),
        source_ids=tuple(dict.fromkeys(ref.resource_id for ref in refs)),
        classifications=("internal",),
        policy_digest="sha256:" + "f" * 64,
        issued_at=MONDAY,
        not_before=MONDAY,
        expires_at=FRIDAY + timedelta(days=1),
        nonce="nonce/company-procedure",
    )
    context = authority.issue(claims, key_id="procedure-reader")
    return HostedAccessContextAuthorizer(
        authority, context, exact_refs=refs, clock=lambda: FRIDAY
    )


class _ArmingReader:
    def __init__(self, delegate, repository, changed_ref):
        self._delegate = delegate
        self._repository = repository
        self._changed_ref = changed_ref
        self.armed = False
        self._revoked = False

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def authorize(self, ref, *, at=None):
        if self.armed and not self._revoked:
            self._repository.record_invalidation(
                event_id="invalidate:company-guidance-race",
                changed_ref=self._changed_ref,
                reason="review revoked during company recheck",
                at=FRIDAY,
                authorizer=_GuidanceInvalidator(),
            )
            self._revoked = True
        return self._delegate.authorize(ref, at=at)


class _ArmingProcedure:
    def __init__(self, delegate, reader):
        self._delegate = delegate
        self._reader = reader

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def actionable_guidance(self, request, *, authorizer):
        result = self._delegate.actionable_guidance(request, authorizer=authorizer)
        self._reader.armed = True
        return result


class _CoordinatedRefreshProcedure:
    def __init__(self, delegate, readers):
        self._delegate = delegate
        self._readers = readers
        self._counter = 0

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def actionable_guidance(self, request, *, authorizer):
        result = self._delegate.actionable_guidance(request, authorizer=authorizer)
        self._counter += 1
        for reader in self._readers:
            current = reader.context.claims
            claims = current.model_copy(
                update={"nonce": f"coordinated-refresh-{self._counter}-{current.subject}"}
            )
            reader.context = reader.authority.issue(claims, key_id=reader.context.key_id)
        return result


def _company_guidance_fixture(tmp_path):
    company, records = _company_fixture(tmp_path / "company-guidance.json")
    refs = _all_record_refs(company, records.values())
    _registry, _descriptor, readers, _revocations = _registered_company(company, refs)
    repository = CoreProvenanceRepository(
        tmp_path / "procedures.json", tenant_id="acme", project_id="platform"
    )
    source = records["service-alpha"].source_refs[0]
    command = source.model_copy(
        update={
            "authority": "opensaddle://core",
            "resource_type": "command",
            "resource_id": "opensaddle/command/review",
        }
    )
    environment = source.model_copy(
        update={
            "authority": "opensaddle://core",
            "resource_type": "environment",
            "resource_id": "opensaddle/environment-revision/review",
        }
    )
    receipt = create_core_provenance_receipt(
        receipt_id="receipt:company-guidance",
        command_ref=command,
        environment_ref=environment,
        observed_at=FRIDAY,
    )
    candidate = CoreProvenanceService(
        repository=repository, clock=lambda: FRIDAY
    ).ingest(receipt, authorizer=_GuidanceAllow(), trust=_GuidanceTrust())
    evidence = company.projection.record_ref(records["policy-approved"])
    ownership_evidence = company.projection.record_ref(records["alpha-transfer"])
    test_artifact_path = tmp_path / "artifacts" / "company-guidance-test.json"
    test_artifact_path.parent.mkdir()
    test_artifact_bytes = b'{"status":"passed","checks":["owner","policy"]}\n'
    test_artifact_path.write_bytes(test_artifact_bytes)
    test_artifact = ResourceRef(
        authority="opensaddle://core",
        resource_type="test-result",
        resource_id=str(test_artifact_path.relative_to(tmp_path)),
        version="1",
        digest="sha256:" + sha256(test_artifact_bytes).hexdigest(),
    )
    reviewed = ProcedureReviewService(
        repository=repository, clock=lambda: FRIDAY
    ).review(
        candidate.record.record_digest,
        decision_id="review:company-guidance",
        reviewer_ref=records["team-red"].source_refs[0],
        evidence_refs=(ownership_evidence, evidence, test_artifact),
        accepted=True,
        authorizer=_GuidanceAllow(),
        review_authorizer=_GuidanceReview(),
    )
    assert reviewed.promoted_record is not None
    procedure_reader = _signed_procedure_reader(
        candidate, reviewed, evidence, test_artifact
    )
    request = CompanyOperationalGuidanceRequest(
        service_id="service-alpha",
        policy_id="deploy-policy",
        policy_scope="production",
        valid_at=FRIDAY,
        known_at=FRIDAY,
        procedure=ProcedureExplanationRequest(
            candidate_digest=candidate.record.record_digest
        ),
    )
    facade = CompanyOperationalGuidanceService(
        company,
        ProcedureExplanationService(repository=repository, clock=lambda: FRIDAY),
        owner_reader=readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID],
        policy_reader=readers[COMPANY_POLICY_QUERY_OPERATOR_ID],
        procedure_authorizer=procedure_reader,
    )
    return company, records, facade, repository, request, reviewed, evidence, test_artifact


def test_company_guidance_candidate_review_restart_and_current_authority(tmp_path):
    company, records, facade, repository, request, reviewed, _evidence, test_artifact = _company_guidance_fixture(tmp_path)
    result = facade.read(request)
    assert result.status == "guidance"
    assert result.owner_team_id == "team-blue"
    assert result.policy_rule == "Two reviewers are required."
    assert result.procedure is not None
    assert result.procedure.reviewed_digest == reviewed.promoted_record.record_digest
    assert test_artifact in result.procedure.evidence_refs
    assert company.projection.record_ref(records["alpha-transfer"]) in result.company_lineage

    restarted_projection = TemporalProjectionService(
        str(company.projection.store.path),
        tenant_id="acme",
        project_id="platform",
        clock=lambda: FRIDAY,
    )
    restarted_company = CompanyKnowledgeService(restarted_projection)
    refs = _all_record_refs(restarted_company, records.values())
    _registry, _descriptor, readers, _revocations = _registered_company(restarted_company, refs)
    restarted = CompanyOperationalGuidanceService(
        restarted_company,
        ProcedureExplanationService(
            repository=CoreProvenanceRepository(
                str(repository.store.path),
                tenant_id="acme",
                project_id="platform",
            ),
            clock=lambda: FRIDAY,
        ),
        owner_reader=readers[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID],
        policy_reader=readers[COMPANY_POLICY_QUERY_OPERATOR_ID],
        procedure_authorizer=facade.procedure_authorizer,
    ).read(request)
    assert restarted == result


def test_company_guidance_rejects_cross_service_procedure_substitution(tmp_path):
    _company, _records, facade, _repository, request, _reviewed, _evidence, _artifact = (
        _company_guidance_fixture(tmp_path)
    )
    substituted = facade.read(request.model_copy(update={"service_id": "service-beta"}))
    assert substituted.status == "abstained"
    assert substituted.procedure is None


def test_company_guidance_rejects_mismatched_signed_reader_scope(tmp_path):
    company, records, facade, _repository, request, _reviewed, _evidence, _artifact = (
        _company_guidance_fixture(tmp_path)
    )
    reader = facade.owner_reader
    bad_claims = reader.context.claims.model_copy(
        update={"project_id": "other-project", "nonce": "mismatched-scope"}
    )
    bad_context = reader.authority.issue(bad_claims, key_id="reader")
    bad_reader = HostedAccessContextAuthorizer(
        reader.authority,
        bad_context,
        exact_refs=_all_record_refs(company, records.values()),
        clock=lambda: FRIDAY,
    )
    with pytest.raises(PermissionError, match="authority scopes do not match"):
        CompanyOperationalGuidanceService(
            company,
            facade.procedures,
            owner_reader=bad_reader,
            policy_reader=facade.policy_reader,
            procedure_authorizer=facade.procedure_authorizer,
        )


def test_company_guidance_rechecks_refreshed_subject_scope(tmp_path):
    company, records, facade, _repository, request, _reviewed, _evidence, _artifact = (
        _company_guidance_fixture(tmp_path)
    )
    reader = facade.owner_reader
    swapped_claims = reader.context.claims.model_copy(
        update={"subject": "reader/other-user", "nonce": "subject-swap"}
    )
    reader.context = reader.authority.issue(swapped_claims, key_id="reader")
    with pytest.raises(PermissionError, match="authority scopes do not match"):
        facade.read(request)


def test_company_guidance_rejects_procedure_repository_scope_mismatch(tmp_path):
    company, records, facade, repository, request, _reviewed, _evidence, _artifact = (
        _company_guidance_fixture(tmp_path)
    )
    wrong_repository = CoreProvenanceRepository(
        str(repository.store.path), tenant_id="other-company", project_id="platform"
    )
    with pytest.raises(PermissionError, match="procedure scope is unavailable"):
        CompanyOperationalGuidanceService(
            company,
            ProcedureExplanationService(repository=wrong_repository, clock=lambda: FRIDAY),
            owner_reader=facade.owner_reader,
            policy_reader=facade.policy_reader,
            procedure_authorizer=facade.procedure_authorizer,
        )


@pytest.mark.parametrize("changed", ["service", "policy"])
def test_company_guidance_source_revocation_abstains_without_substituting_state(tmp_path, changed):
    company, records, facade, _repository, request, _reviewed, _evidence, _artifact = _company_guidance_fixture(tmp_path)
    changed_ref = (
        records["service-alpha"].source_refs[0]
        if changed == "service"
        else records["policy-approved"].source_refs[0]
    )
    company.projection.tombstone(
        changed_ref,
        event_id=f"revoke:{changed}",
        reason="source revoked",
        effective_at=FRIDAY,
        recorded_at=FRIDAY,
        authorizer=_GuidanceInvalidator(),
    )
    result = facade.read(request)
    assert result.status == "abstained"
    assert result.owner_team_id is None
    assert result.policy_rule is None
    assert result.procedure is None


def test_company_guidance_review_evidence_revocation_abstains(tmp_path):
    company, _records, facade, repository, request, _reviewed, evidence, _artifact = _company_guidance_fixture(tmp_path)
    repository.record_invalidation(
        event_id="invalidate:company-guidance",
        changed_ref=evidence,
        reason="verified evidence replaced",
        at=FRIDAY,
        authorizer=_GuidanceInvalidator(),
    )
    result = facade.read(request)
    assert result.status == "abstained"
    assert result.procedure is None


def test_company_guidance_signed_procedure_context_revocation_abstains(tmp_path):
    _company, _records, facade, _repository, request, _reviewed, _evidence, _artifact = (
        _company_guidance_fixture(tmp_path)
    )
    facade.procedure_authorizer.authority.revocations.revoke_context(
        facade.procedure_authorizer.context.context_digest, revoked_at=FRIDAY
    )
    result = facade.read(request)
    assert result.status == "abstained"
    assert result.procedure is None


def test_company_guidance_rechecks_procedure_after_company_authority_race(tmp_path):
    company, _records, facade, repository, request, _reviewed, evidence, _artifact = (
        _company_guidance_fixture(tmp_path)
    )
    policy_reader = _ArmingReader(
        facade.policy_reader, repository, evidence
    )
    raced = CompanyOperationalGuidanceService(
        company,
        _ArmingProcedure(facade.procedures, policy_reader),
        owner_reader=facade.owner_reader,
        policy_reader=policy_reader,
        procedure_authorizer=facade.procedure_authorizer,
    )
    result = raced.read(request)
    assert result.status == "abstained"
    assert result.procedure is None


def test_company_guidance_abstains_on_coordinated_context_refresh_mid_read(tmp_path):
    company, _records, facade, _repository, request, _reviewed, _evidence, _artifact = (
        _company_guidance_fixture(tmp_path)
    )
    refreshed = _CoordinatedRefreshProcedure(
        facade.procedures,
        (facade.owner_reader, facade.policy_reader, facade.procedure_authorizer),
    )
    raced = CompanyOperationalGuidanceService(
        company,
        refreshed,
        owner_reader=facade.owner_reader,
        policy_reader=facade.policy_reader,
        procedure_authorizer=facade.procedure_authorizer,
    )
    result = raced.read(request)
    assert result.status == "abstained"
    assert result.procedure is None


def test_company_guidance_packet_signed_readers_restart_and_source_invalidation(tmp_path):
    company, records, facade, _repository, guidance_request, _reviewed, _evidence, _artifact = (
        _company_guidance_fixture(tmp_path)
    )
    guidance = facade.read(guidance_request)
    assert guidance.status == "guidance"
    exact_refs = tuple(dict.fromkeys((*guidance.company_lineage, *guidance.procedure_lineage)))
    descriptor = company_guidance_packet_descriptor()
    authority = AccessContextAuthority({"packet": b"packet-fixture"}, issuer="https://packet.example")
    source_ids = tuple(dict.fromkeys(ref.resource_id for ref in exact_refs))

    def issue(subject: str, *, actions=("context.read",)):
        claims = AccessClaims(
            issuer="https://packet.example", tenant_id="acme", project_id="platform",
            subject=subject, delegator="packet/admin", delegation_id=f"packet/{subject}",
            capability_id="krail.company-guidance-packet", capability_version="1.0.0",
            capability_digest=descriptor.descriptor_digest, actions=actions,
            source_ids=source_ids, classifications=("internal",),
            policy_digest="sha256:" + "a" * 64, issued_at=MONDAY,
            not_before=MONDAY, expires_at=FRIDAY + timedelta(days=1), nonce=f"packet-{subject}",
        )
        return authority.issue(claims, key_id="packet")

    service = CompanyGuidancePacketService(
        lambda _context, _refs: facade, project_path=tmp_path,
        authority=authority, tenant_id="acme", project_id="platform",
        capability_descriptor_digest=descriptor.descriptor_digest,
        current_ref_resolver=lambda ref: ref, clock=lambda: FRIDAY,
    )
    context = issue("packet-reader")
    unsigned = CompanyGuidancePacketCreateRequest(
        access_context=context,
        request_binding=authority.issue_packet_request_binding(
            PacketRequestBinding(
                access_context_digest=context.context_digest, tenant_id="acme", project_id="platform",
                capability_id="krail.company-guidance-packet", capability_version="1.0.0",
                capability_digest=descriptor.descriptor_digest, request_digest="sha256:" + "0" * 64,
                purpose="company-guidance-followup", scope="service-alpha", issued_at=MONDAY,
                not_before=MONDAY, expires_at=FRIDAY + timedelta(days=1), nonce="binding-placeholder",
            ), key_id="packet"
        ), exact_refs=exact_refs, guidance_request=guidance_request,
        purpose="company-guidance-followup", scope="service-alpha",
    )
    request_digest = service._request_digest(unsigned)
    binding = authority.issue_packet_request_binding(
        PacketRequestBinding(
            access_context_digest=context.context_digest, tenant_id="acme", project_id="platform",
            capability_id="krail.company-guidance-packet", capability_version="1.0.0",
            capability_digest=descriptor.descriptor_digest, request_digest=request_digest,
            purpose="company-guidance-followup", scope="service-alpha", issued_at=MONDAY,
            not_before=MONDAY, expires_at=FRIDAY + timedelta(days=1), nonce="binding-create",
        ), key_id="packet"
    )
    request = unsigned.model_copy(update={"request_binding": binding})
    packet = service.create(request)
    assert packet.packet_digest == packet.packet_id
    assert packet.exact_lineage_refs == exact_refs

    fresh = issue("packet-reader-fresh")
    read_binding = authority.issue_packet_request_binding(
        PacketRequestBinding(
            access_context_digest=fresh.context_digest, tenant_id="acme", project_id="platform",
            capability_id="krail.company-guidance-packet", capability_version="1.0.0",
            capability_digest=descriptor.descriptor_digest, request_digest=packet.request_digest,
            purpose=packet.purpose, scope=packet.scope, issued_at=MONDAY,
            not_before=MONDAY, expires_at=FRIDAY + timedelta(days=1), nonce="binding-read",
        ), key_id="packet"
    )
    read_request = CompanyGuidancePacketReadRequest(
        packet_digest=packet.packet_digest, access_context=fresh, request_binding=read_binding,
        exact_refs=exact_refs, purpose=packet.purpose, scope=packet.scope,
    )
    result = service.read(read_request)
    assert result.status == "available"
    assert result.packet == packet
    assert result.current_authorization_digest == fresh.context_digest

    restarted = CompanyGuidancePacketService(
        lambda _context, _refs: facade, project_path=tmp_path,
        authority=authority, tenant_id="acme", project_id="platform",
        capability_descriptor_digest=descriptor.descriptor_digest,
        current_ref_resolver=lambda ref: ref, clock=lambda: FRIDAY,
    )
    assert restarted.read(read_request).status == "available"

    maint = issue("packet-maintainer", actions=("retention.enforce",))
    removed = service.invalidate_for_sources(
        maint, exact_refs=(company.projection.record_ref(records["policy-approved"]),), reason="source-revoked"
    )
    assert removed == (packet.packet_digest,)
    assert restarted.read(read_request).status == "packet_unavailable"
