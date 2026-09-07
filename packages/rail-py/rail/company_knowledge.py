"""Bounded company ownership and policy semantics over temporal projections.

The module is deliberately read-only at query time.  Operational systems keep
their authority in each immutable ``TemporalRecord`` and its exact source refs;
KRAIL validates, projects, and answers without becoming a second writer.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from krail.provider.v1 import ResourceRef
from rail.authorized_context import HostedAccessContextAuthorizer
from rail.extension_registry import (
    DomainExtensionRegistry,
    ExtensionDescriptor,
    HANDLER_LINEAGE_REFS,
    OperatorDescriptor,
    describe_extension,
    describe_operator,
)
from rail.procedure_projection import ProjectionWriter, TemporalProjectionService
from rail.temporal_records import (
    TemporalRecord,
    query_temporal_records,
    verify_temporal_record_integrity,
)


COMPANY_EXTENSION_ID = "company.knowledge"
COMPANY_EXTENSION_VERSION = "1.0.0"
COMPANY_OWNERSHIP_QUERY_OPERATOR_ID = "company.service-ownership.query"
COMPANY_EFFECTIVE_TIME_OPERATOR_ID = (
    "company.service-ownership.validate-effective-time"
)
COMPANY_POLICY_QUERY_OPERATOR_ID = "company.policy.query"
COMPANY_OPERATOR_VERSION = "1.0.0"

TEAM_SCHEMA = "company.team.v1"
SERVICE_SCHEMA = "company.service.v1"
OWNERSHIP_SCHEMA = "company.service_ownership.v1"
POLICY_SCHEMA = "company.policy_revision.v1"
OWNERSHIP_QUERY_SCHEMA = "company.service-ownership-query.v1"
OWNERSHIP_ANSWER_SCHEMA = "company.service-ownership-answer.v1"
EFFECTIVE_TIME_QUERY_SCHEMA = "company.effective-time-validation-query.v1"
EFFECTIVE_TIME_ANSWER_SCHEMA = "company.effective-time-validation.v1"
POLICY_QUERY_SCHEMA = "company.policy-query.v1"
POLICY_ANSWER_SCHEMA = "company.policy-answer.v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompanyTeam(StrictModel):
    team_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    source_authority: str = Field(min_length=1, max_length=512)


class CompanyService(StrictModel):
    service_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    source_authority: str = Field(min_length=1, max_length=512)


class ServiceOwnership(StrictModel):
    """Business ownership stays distinct from the record/source authority."""

    service_id: str = Field(min_length=1, max_length=200)
    owner_team_id: str = Field(min_length=1, max_length=200)
    assertion_status: Literal["approved", "reported", "draft"]
    source_authority: str = Field(min_length=1, max_length=512)


class CompanyPolicyRevision(StrictModel):
    policy_id: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=200)
    rule: str = Field(min_length=1, max_length=4000)
    assertion_status: Literal["approved", "reported", "draft"]
    source_authority: str = Field(min_length=1, max_length=512)


class _BitemporalQuery(StrictModel):
    valid_at: datetime
    known_at: datetime

    @field_validator("valid_at", "known_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("company query timestamps must include a timezone")
        return value


class ServiceOwnershipQuery(_BitemporalQuery):
    service_id: str = Field(min_length=1, max_length=200)


class ServiceOwnershipAnswer(StrictModel):
    status: Literal["owned", "unknown", "conflict", "stale"]
    reason: Literal[
        "current",
        "no-approved-state",
        "insufficient-authorized-evidence",
        "conflicting-authoritative-state",
        "stale-evidence",
        "knowledge-changed-during-query",
    ]
    service_id: str
    valid_at: datetime
    known_at: datetime
    owner_team_id: str | None = None
    ownership_ref: ResourceRef | None = None
    evidence_refs: tuple[ResourceRef, ...] = ()

    @model_validator(mode="after")
    def _answer_shape(self) -> "ServiceOwnershipAnswer":
        complete = self.owner_team_id is not None and self.ownership_ref is not None
        if (self.status == "owned") != complete:
            raise ValueError("only an owned answer may disclose an owner")
        if self.status != "owned" and self.evidence_refs:
            raise ValueError("non-owned answers cannot disclose candidate evidence")
        return self


class EffectiveTimeValidationRequest(_BitemporalQuery):
    record: TemporalRecord


class EffectiveTimeValidationAnswer(StrictModel):
    status: Literal["effective", "not-effective", "not-known", "not-authoritative"]
    record_digest: str
    valid_at: datetime
    known_at: datetime
    effective_at_valid_time: bool
    known_at_recorded_time: bool
    authoritative_approved_state: bool


class EffectivePolicyQuery(_BitemporalQuery):
    policy_id: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=200)


class EffectivePolicyAnswer(StrictModel):
    status: Literal["effective", "unknown", "conflict", "stale"]
    reason: Literal[
        "current",
        "no-approved-state",
        "insufficient-authorized-evidence",
        "conflicting-authoritative-state",
        "stale-evidence",
        "knowledge-changed-during-query",
    ]
    policy_id: str
    scope: str
    valid_at: datetime
    known_at: datetime
    rule: str | None = None
    policy_ref: ResourceRef | None = None
    evidence_refs: tuple[ResourceRef, ...] = ()

    @model_validator(mode="after")
    def _answer_shape(self) -> "EffectivePolicyAnswer":
        complete = self.rule is not None and self.policy_ref is not None
        if (self.status == "effective") != complete:
            raise ValueError("only an effective answer may disclose a policy")
        if self.status != "effective" and self.evidence_refs:
            raise ValueError("non-effective answers cannot disclose candidate evidence")
        return self


CompanyPayload = CompanyTeam | CompanyService | ServiceOwnership | CompanyPolicyRevision


def validate_company_record(record: TemporalRecord) -> CompanyPayload:
    """Validate bounded company semantics without changing the v1 envelope."""

    verify_temporal_record_integrity(record)
    if record.payload_schema_version != "1.0.0":
        raise ValueError("unsupported company payload schema version")
    if record.payload_schema == TEAM_SCHEMA:
        payload: CompanyPayload = CompanyTeam.model_validate(record.payload)
        expected_entity = payload.team_id
        if record.kind != "approved_state":
            raise ValueError("company team records must be approved state")
    elif record.payload_schema == SERVICE_SCHEMA:
        payload = CompanyService.model_validate(record.payload)
        expected_entity = payload.service_id
        if record.kind != "approved_state":
            raise ValueError("company service records must be approved state")
    elif record.payload_schema == OWNERSHIP_SCHEMA:
        payload = ServiceOwnership.model_validate(record.payload)
        expected_entity = payload.service_id
        expected_kind = {
            "approved": "approved_state",
            "reported": "reported_claim",
            "draft": "proposed_change",
        }[payload.assertion_status]
        if record.kind != expected_kind:
            raise ValueError("ownership assertion status does not match record kind")
    elif record.payload_schema == POLICY_SCHEMA:
        payload = CompanyPolicyRevision.model_validate(record.payload)
        expected_entity = payload.policy_id
        expected_kind = {
            "approved": "approved_state",
            "reported": "reported_claim",
            "draft": "proposed_change",
        }[payload.assertion_status]
        if record.kind != expected_kind:
            raise ValueError("policy assertion status does not match record kind")
    else:
        raise ValueError("unsupported company payload schema")
    if record.entity_id != expected_entity:
        raise ValueError("company record entity does not match its typed payload")
    if payload.source_authority != record.authority:
        raise ValueError("company payload source authority does not match record authority")
    return payload


def company_knowledge_extension() -> ExtensionDescriptor:
    operators = (
        describe_operator(
            operator_id=COMPANY_OWNERSHIP_QUERY_OPERATOR_ID,
            version=COMPANY_OPERATOR_VERSION,
            input_schema=OWNERSHIP_QUERY_SCHEMA,
            output_schema=OWNERSHIP_ANSWER_SCHEMA,
            deterministic=False,
        ),
        describe_operator(
            operator_id=COMPANY_EFFECTIVE_TIME_OPERATOR_ID,
            version=COMPANY_OPERATOR_VERSION,
            input_schema=EFFECTIVE_TIME_QUERY_SCHEMA,
            output_schema=EFFECTIVE_TIME_ANSWER_SCHEMA,
            deterministic=True,
        ),
        describe_operator(
            operator_id=COMPANY_POLICY_QUERY_OPERATOR_ID,
            version=COMPANY_OPERATOR_VERSION,
            input_schema=POLICY_QUERY_SCHEMA,
            output_schema=POLICY_ANSWER_SCHEMA,
            deterministic=False,
        ),
    )
    return describe_extension(
        extension_id=COMPANY_EXTENSION_ID,
        version=COMPANY_EXTENSION_VERSION,
        payload_schemas=(
            TEAM_SCHEMA,
            SERVICE_SCHEMA,
            OWNERSHIP_SCHEMA,
            POLICY_SCHEMA,
            OWNERSHIP_QUERY_SCHEMA,
            OWNERSHIP_ANSWER_SCHEMA,
            EFFECTIVE_TIME_QUERY_SCHEMA,
            EFFECTIVE_TIME_ANSWER_SCHEMA,
            POLICY_QUERY_SCHEMA,
            POLICY_ANSWER_SCHEMA,
        ),
        operators=operators,
    )


class CompanyKnowledgeService:
    """Typed company reads over the existing canonical temporal projection."""

    def __init__(
        self,
        projection: TemporalProjectionService,
        *,
        projection_id: str = "company-knowledge",
    ) -> None:
        self.projection = projection
        self.projection_id = projection_id
        self.tenant_id = projection.tenant_id
        self.project_id = projection.project_id

    def ingest(
        self, record: TemporalRecord, *, at: datetime, writer: ProjectionWriter
    ) -> TemporalRecord:
        validate_company_record(record)
        return self.projection.ingest(record, at=at, writer=writer)

    def _records(self, schema: str, entity_id: str) -> tuple[TemporalRecord, ...]:
        return tuple(
            record
            for record in self.projection.temporal_records()
            if record.payload_schema == schema and record.entity_id == entity_id
        )

    @staticmethod
    def _current(
        records: tuple[TemporalRecord, ...], *, valid_at: datetime, known_at: datetime
    ) -> tuple[TemporalRecord, ...]:
        groups: dict[tuple[str, str, str, str, str, str], list[TemporalRecord]] = (
            defaultdict(list)
        )
        for record in records:
            key = (
                record.entity_authority,
                record.entity_id,
                record.payload_schema,
                record.payload_schema_version,
                record.authority,
                record.writer_family,
            )
            groups[key].append(record)
        return tuple(
            record
            for key in sorted(groups)
            for record in query_temporal_records(
                groups[key], valid_at=valid_at, known_at=known_at
            )
        )

    def _stale_keys(self, *, valid_at: datetime, known_at: datetime) -> set[tuple[str, str, str, str, str]]:
        active = self.projection.active_invalidation_refs(
            valid_at=valid_at, known_at=known_at
        )
        stale = {ref.exact_key for ref in active}
        for ref in active:
            stale.update(item.exact_key for item in self.projection.affected_region(ref))

        # Pending recomputation is a recorded-time fact. It applies after the
        # correction was learned, but never leaks backwards into an earlier
        # known-at query.
        stale.update(
            entry.output_ref.exact_key
            for entry in self.projection.dirty_entries_at(
                self.projection_id, known_at=known_at
            )
        )

        # Recompute may clear the work queue, but it cannot make an older
        # reviewed output valid against a source revision that is no longer
        # current. Follow only explicit source/provenance refs; a supersession
        # edge by itself does not taint the replacement record.
        records = self.projection.temporal_records()
        current = self._current(records, valid_at=valid_at, known_at=known_at)
        temporal_keys = {
            self.projection.record_ref(record).exact_key for record in records
        }
        current_keys = {
            self.projection.record_ref(record).exact_key for record in current
        }
        for record in current:
            if record.freshness != "current":
                stale.add(self.projection.record_ref(record).exact_key)

        changed = True
        while changed:
            changed = False
            for record in current:
                output_key = self.projection.record_ref(record).exact_key
                dependencies = record.source_refs + record.provenance_refs
                if output_key in stale or not any(
                    ref.exact_key in stale
                    or (
                        ref.exact_key in temporal_keys
                        and ref.exact_key not in current_keys
                    )
                    for ref in dependencies
                ):
                    continue
                stale.add(output_key)
                changed = True
        return stale

    def _authorized_record_refs(
        self,
        record: TemporalRecord,
        reader: HostedAccessContextAuthorizer,
        *,
        valid_at: datetime,
        known_at: datetime,
        stale_keys: set[tuple[str, str, str, str, str]],
    ) -> tuple[Literal["current", "stale", "hidden"], tuple[ResourceRef, ...]]:
        record_ref = self.projection.record_ref(record)
        refs = tuple(dict.fromkeys((record_ref, *record.source_refs, *record.provenance_refs)))
        claims = reader.authority.verify(reader.context, as_of=reader.clock())
        if record.visibility not in claims.classifications:
            return "hidden", ()
        try:
            for ref in refs:
                reader.authorize(ref)
        except PermissionError:
            return "hidden", ()
        if (
            record.freshness != "current"
            or record_ref.exact_key in stale_keys
            or any(ref.exact_key in stale_keys for ref in record.source_refs + record.provenance_refs)
        ):
            return "stale", refs
        if not (
            record.valid_from <= valid_at
            and (record.valid_to is None or valid_at < record.valid_to)
            and (record.ingested_at or record.recorded_at) <= known_at
        ):
            raise ValueError("company query selected an inapplicable temporal record")
        return "current", refs

    def _definitions(
        self,
        schema: str,
        entity_ids: tuple[str, ...],
        *,
        valid_at: datetime,
        known_at: datetime,
        reader: HostedAccessContextAuthorizer,
        stale_keys: set[tuple[str, str, str, str, str]],
    ) -> tuple[Literal["current", "stale", "hidden", "missing"], tuple[ResourceRef, ...]]:
        lineage: list[ResourceRef] = []
        for entity_id in entity_ids:
            selected = self._current(
                self._records(schema, entity_id),
                valid_at=valid_at,
                known_at=known_at,
            )
            approved = []
            hidden = stale = False
            for record in selected:
                payload = validate_company_record(record)
                state, refs = self._authorized_record_refs(
                    record,
                    reader,
                    valid_at=valid_at,
                    known_at=known_at,
                    stale_keys=stale_keys,
                )
                hidden |= state == "hidden"
                stale |= state == "stale"
                if state != "hidden":
                    lineage.extend(refs)
                if state == "current" and record.kind == "approved_state":
                    approved.append(payload)
            if hidden:
                return "hidden", ()
            if stale and not approved:
                return "stale", ()
            if len(approved) != 1:
                return "missing", ()
        return "current", tuple(dict.fromkeys(lineage))

    def owner(
        self,
        query: ServiceOwnershipQuery,
        *,
        reader: HostedAccessContextAuthorizer,
    ) -> tuple[ServiceOwnershipAnswer, tuple[ResourceRef, ...]]:
        operator = _company_operator(COMPANY_OWNERSHIP_QUERY_OPERATOR_ID)
        _verify_operator_authority(self, reader, operator)
        cursor = self.projection.scope_cursor()
        stale_keys = self._stale_keys(
            valid_at=query.valid_at, known_at=query.known_at
        )
        current = self._current(
            self._records(OWNERSHIP_SCHEMA, query.service_id),
            valid_at=query.valid_at,
            known_at=query.known_at,
        )
        approved: list[tuple[TemporalRecord, ServiceOwnership, tuple[ResourceRef, ...]]] = []
        lineage: list[ResourceRef] = []
        hidden_approved = stale_approved = False
        for record in current:
            payload = validate_company_record(record)
            assert isinstance(payload, ServiceOwnership)
            state, refs = self._authorized_record_refs(
                record,
                reader,
                valid_at=query.valid_at,
                known_at=query.known_at,
                stale_keys=stale_keys,
            )
            is_approved = payload.assertion_status == "approved"
            hidden_approved |= is_approved and state == "hidden"
            stale_approved |= is_approved and state == "stale"
            if state != "hidden":
                lineage.extend(refs)
            if is_approved and state == "current":
                approved.append((record, payload, refs))

        common = dict(
            service_id=query.service_id,
            valid_at=query.valid_at,
            known_at=query.known_at,
        )
        if hidden_approved:
            answer = ServiceOwnershipAnswer(
                status="unknown",
                reason="insufficient-authorized-evidence",
                **common,
            )
            lineage = []
        elif not approved:
            answer = ServiceOwnershipAnswer(
                status="stale" if stale_approved else "unknown",
                reason="stale-evidence" if stale_approved else "no-approved-state",
                **common,
            )
            lineage = []
        elif len({payload.owner_team_id for _record, payload, _refs in approved}) != 1:
            answer = ServiceOwnershipAnswer(
                status="conflict",
                reason="conflicting-authoritative-state",
                **common,
            )
        else:
            owner_team_id = approved[0][1].owner_team_id
            definition_state, definition_refs = self._definitions(
                SERVICE_SCHEMA,
                (query.service_id,),
                valid_at=query.valid_at,
                known_at=query.known_at,
                reader=reader,
                stale_keys=stale_keys,
            )
            if definition_state == "current":
                definition_state, team_refs = self._definitions(
                    TEAM_SCHEMA,
                    (owner_team_id,),
                    valid_at=query.valid_at,
                    known_at=query.known_at,
                    reader=reader,
                    stale_keys=stale_keys,
                )
                definition_refs += team_refs
            if definition_state != "current":
                answer = ServiceOwnershipAnswer(
                    status="stale" if definition_state == "stale" else "unknown",
                    reason=(
                        "stale-evidence"
                        if definition_state == "stale"
                        else "insufficient-authorized-evidence"
                    ),
                    **common,
                )
                lineage = []
            else:
                record, payload, refs = approved[0]
                lineage.extend(definition_refs)
                answer = ServiceOwnershipAnswer(
                    status="owned",
                    reason="current",
                    owner_team_id=payload.owner_team_id,
                    ownership_ref=self.projection.record_ref(record),
                    evidence_refs=record.source_refs,
                    **common,
                )
                lineage.extend(refs)
        if self.projection.scope_cursor() != cursor:
            _verify_operator_authority(self, reader, operator)
            return (
                ServiceOwnershipAnswer(
                    status="unknown",
                    reason="knowledge-changed-during-query",
                    **common,
                ),
                (),
            )
        for ref in tuple(dict.fromkeys(lineage)):
            reader.authorize(ref)
        _verify_operator_authority(self, reader, operator)
        return answer, tuple(dict.fromkeys(lineage))

    def effective_policy(
        self,
        query: EffectivePolicyQuery,
        *,
        reader: HostedAccessContextAuthorizer,
    ) -> tuple[EffectivePolicyAnswer, tuple[ResourceRef, ...]]:
        operator = _company_operator(COMPANY_POLICY_QUERY_OPERATOR_ID)
        _verify_operator_authority(self, reader, operator)
        cursor = self.projection.scope_cursor()
        stale_keys = self._stale_keys(
            valid_at=query.valid_at, known_at=query.known_at
        )
        current = self._current(
            self._records(POLICY_SCHEMA, query.policy_id),
            valid_at=query.valid_at,
            known_at=query.known_at,
        )
        approved: list[tuple[TemporalRecord, CompanyPolicyRevision, tuple[ResourceRef, ...]]] = []
        lineage: list[ResourceRef] = []
        hidden_approved = stale_approved = False
        for record in current:
            payload = validate_company_record(record)
            assert isinstance(payload, CompanyPolicyRevision)
            if payload.scope != query.scope:
                continue
            state, refs = self._authorized_record_refs(
                record,
                reader,
                valid_at=query.valid_at,
                known_at=query.known_at,
                stale_keys=stale_keys,
            )
            is_approved = payload.assertion_status == "approved"
            hidden_approved |= is_approved and state == "hidden"
            stale_approved |= is_approved and state == "stale"
            if state != "hidden":
                lineage.extend(refs)
            if is_approved and state == "current":
                approved.append((record, payload, refs))
        common = dict(
            policy_id=query.policy_id,
            scope=query.scope,
            valid_at=query.valid_at,
            known_at=query.known_at,
        )
        if hidden_approved:
            answer = EffectivePolicyAnswer(
                status="unknown",
                reason="insufficient-authorized-evidence",
                **common,
            )
            lineage = []
        elif not approved:
            answer = EffectivePolicyAnswer(
                status="stale" if stale_approved else "unknown",
                reason="stale-evidence" if stale_approved else "no-approved-state",
                **common,
            )
            lineage = []
        elif len({payload.rule for _record, payload, _refs in approved}) != 1:
            answer = EffectivePolicyAnswer(
                status="conflict",
                reason="conflicting-authoritative-state",
                **common,
            )
        else:
            record, payload, refs = approved[0]
            answer = EffectivePolicyAnswer(
                status="effective",
                reason="current",
                rule=payload.rule,
                policy_ref=self.projection.record_ref(record),
                evidence_refs=record.source_refs,
                **common,
            )
            lineage.extend(refs)
        if self.projection.scope_cursor() != cursor:
            _verify_operator_authority(self, reader, operator)
            return (
                EffectivePolicyAnswer(
                    status="unknown",
                    reason="knowledge-changed-during-query",
                    **common,
                ),
                (),
            )
        for ref in tuple(dict.fromkeys(lineage)):
            reader.authorize(ref)
        _verify_operator_authority(self, reader, operator)
        return answer, tuple(dict.fromkeys(lineage))

    def validate_effective_time(
        self,
        request: EffectiveTimeValidationRequest,
        *,
        reader: HostedAccessContextAuthorizer,
    ) -> EffectiveTimeValidationAnswer:
        operator = _company_operator(COMPANY_EFFECTIVE_TIME_OPERATOR_ID)
        _verify_operator_authority(self, reader, operator)
        persisted = next(
            (
                record
                for record in self.projection.temporal_records()
                if record.record_digest == request.record.record_digest
            ),
            None,
        )
        if persisted is None or persisted != request.record:
            raise ValueError(
                "effective-time validation requires an exact persisted record"
            )
        payload = validate_company_record(request.record)
        if not isinstance(payload, ServiceOwnership):
            raise ValueError("effective-time validation requires service ownership")
        effective = request.record.valid_from <= request.valid_at and (
            request.record.valid_to is None
            or request.valid_at < request.record.valid_to
        )
        known = (
            request.record.ingested_at or request.record.recorded_at
        ) <= request.known_at
        authoritative = (
            request.record.kind == "approved_state"
            and payload.assertion_status == "approved"
        )
        status: Literal[
            "effective", "not-effective", "not-known", "not-authoritative"
        ]
        if not authoritative:
            status = "not-authoritative"
        elif not known:
            status = "not-known"
        elif not effective:
            status = "not-effective"
        else:
            status = "effective"
        answer = EffectiveTimeValidationAnswer(
            status=status,
            record_digest=request.record.record_digest,
            valid_at=request.valid_at,
            known_at=request.known_at,
            effective_at_valid_time=effective,
            known_at_recorded_time=known,
            authoritative_approved_state=authoritative,
        )
        refs = tuple(
            dict.fromkeys(
                (
                    self.projection.record_ref(request.record),
                    *request.record.source_refs,
                    *request.record.provenance_refs,
                )
            )
        )
        for ref in refs:
            reader.authorize(ref)
        _verify_operator_authority(self, reader, operator)
        return answer


def _company_operator(operator_id: str) -> OperatorDescriptor:
    return next(
        operator
        for operator in company_knowledge_extension().operators
        if operator.operator_id == operator_id
    )


def _verify_operator_authority(
    service: CompanyKnowledgeService,
    reader: HostedAccessContextAuthorizer,
    operator: OperatorDescriptor,
) -> None:
    try:
        claims = reader.authority.verify(reader.context, as_of=reader.clock())
    except PermissionError as exc:
        raise PermissionError("company knowledge access denied") from exc
    if (
        (claims.tenant_id, claims.project_id)
        != (service.tenant_id, service.project_id)
        or claims.capability_id != operator.operator_id
        or claims.capability_version != operator.version
        or claims.capability_digest != operator.descriptor_digest
        or "context.read" not in claims.actions
        or claims.source_ids == ("*",)
        or not reader.exact_refs
    ):
        raise PermissionError("company knowledge access denied")


def register_company_knowledge_extension(
    registry: DomainExtensionRegistry,
    service: CompanyKnowledgeService,
    readers: Mapping[str, HostedAccessContextAuthorizer],
) -> ExtensionDescriptor:
    """Register bounded operators with one signed exact-ref reader each."""

    descriptor = company_knowledge_extension()
    by_id = {operator.operator_id: operator for operator in descriptor.operators}
    if set(readers) != set(by_id):
        raise ValueError("company knowledge readers must exactly match operators")
    if not all(isinstance(reader, HostedAccessContextAuthorizer) for reader in readers.values()):
        raise TypeError("company knowledge requires signed live readers")

    def ownership(inputs, config):
        operator = by_id[COMPANY_OWNERSHIP_QUERY_OPERATOR_ID]
        reader = readers[operator.operator_id]
        _verify_operator_authority(service, reader, operator)
        if len(inputs) != 1 or config:
            raise ValueError("ownership query requires one input and empty config")
        answer, refs = service.owner(
            ServiceOwnershipQuery.model_validate(inputs[0]), reader=reader
        )
        _verify_operator_authority(service, reader, operator)
        return {**answer.model_dump(mode="json"), HANDLER_LINEAGE_REFS: refs}

    def effective_time(inputs, config):
        operator = by_id[COMPANY_EFFECTIVE_TIME_OPERATOR_ID]
        reader = readers[operator.operator_id]
        _verify_operator_authority(service, reader, operator)
        if len(inputs) != 1 or config:
            raise ValueError(
                "effective-time validation requires one input and empty config"
            )
        request = EffectiveTimeValidationRequest.model_validate(inputs[0])
        answer = service.validate_effective_time(request, reader=reader)
        refs = tuple(
            dict.fromkeys(
                (
                    service.projection.record_ref(request.record),
                    *request.record.source_refs,
                    *request.record.provenance_refs,
                )
            )
        )
        for ref in refs:
            reader.authorize(ref)
        _verify_operator_authority(service, reader, operator)
        return {**answer.model_dump(mode="json"), HANDLER_LINEAGE_REFS: refs}

    def policy(inputs, config):
        operator = by_id[COMPANY_POLICY_QUERY_OPERATOR_ID]
        reader = readers[operator.operator_id]
        _verify_operator_authority(service, reader, operator)
        if len(inputs) != 1 or config:
            raise ValueError("policy query requires one input and empty config")
        answer, refs = service.effective_policy(
            EffectivePolicyQuery.model_validate(inputs[0]), reader=reader
        )
        _verify_operator_authority(service, reader, operator)
        return {**answer.model_dump(mode="json"), HANDLER_LINEAGE_REFS: refs}

    registry.register(
        descriptor,
        {
            COMPANY_OWNERSHIP_QUERY_OPERATOR_ID: ownership,
            COMPANY_EFFECTIVE_TIME_OPERATOR_ID: effective_time,
            COMPANY_POLICY_QUERY_OPERATOR_ID: policy,
        },
    )
    return descriptor


__all__ = [
    "COMPANY_EFFECTIVE_TIME_OPERATOR_ID",
    "COMPANY_EXTENSION_ID",
    "COMPANY_OPERATOR_VERSION",
    "COMPANY_OWNERSHIP_QUERY_OPERATOR_ID",
    "COMPANY_POLICY_QUERY_OPERATOR_ID",
    "CompanyKnowledgeService",
    "CompanyPolicyRevision",
    "CompanyService",
    "CompanyTeam",
    "EffectivePolicyAnswer",
    "EffectivePolicyQuery",
    "EffectiveTimeValidationAnswer",
    "EffectiveTimeValidationRequest",
    "OWNERSHIP_SCHEMA",
    "POLICY_SCHEMA",
    "SERVICE_SCHEMA",
    "TEAM_SCHEMA",
    "ServiceOwnership",
    "ServiceOwnershipAnswer",
    "ServiceOwnershipQuery",
    "company_knowledge_extension",
    "register_company_knowledge_extension",
    "validate_company_record",
]
