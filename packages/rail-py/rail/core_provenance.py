"""Evidence-only ingestion of exact OpenSaddle command/environment provenance.

Core remains the authority for command execution and environment state. This
module validates and records the exact refs Core emits as proposed provenance
evidence, under caller-owned authorization. It never executes, reviews,
activates, or verifies the referenced operation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo, field_validator, model_validator

from krail.provider.v1 import ResourceRef
from rail.procedural_memory import (
    ProcedureRecord,
    ProcedureReviewDecision,
    create_procedure,
    create_review_decision,
    procedure_temporal_history,
    procedure_temporal_record,
    promote_reviewed_procedure,
)
from rail.procedure_projection import ProjectionWriter, TemporalProjectionService
from rail.semantic.repository import JsonSemanticStore, SemanticRow


CORE_PROVENANCE_VERSION = "opensaddle.core-provenance.v1"
CORE_ISSUER = "opensaddle://core"
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CoreProvenanceReceipt(StrictModel):
    """Core-issued exact provenance, with no executable or review authority."""

    schema_version: Literal["opensaddle.core-provenance.v1"] = CORE_PROVENANCE_VERSION
    receipt_id: Identifier
    command_ref: ResourceRef | None
    environment_ref: ResourceRef | None
    observed_at: datetime
    evidence_state: Literal["complete", "partial", "missing"] = "complete"
    missing_evidence: tuple[Identifier, ...] = Field(default_factory=tuple, max_length=4)
    configuration_observed: Literal[True] = True
    activation_observed: Literal[False] = False
    execution_verified: Literal[False] = False
    invocation_id: Identifier | None = None
    receipt_digest: Digest

    @field_validator("observed_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Core provenance timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _validate_receipt(self, info: ValidationInfo) -> "CoreProvenanceReceipt":
        if self.command_ref is None or self.environment_ref is None:
            raise ValueError("Core provenance requires command and environment exact refs")
        if self.evidence_state != "complete":
            raise ValueError("partial or missing Core provenance evidence cannot be ingested")
        if self.missing_evidence:
            raise ValueError("complete Core provenance cannot list missing evidence")
        for ref, expected_type, prefix in (
            (self.command_ref, "command", "opensaddle/command/"),
            (self.environment_ref, "environment", "opensaddle/environment-revision/"),
        ):
            if ref.authority != CORE_ISSUER or ref.resource_type != expected_type or not ref.resource_id.startswith(prefix):
                raise ValueError("Core issuer and exact command/environment resource shape are required")
        if not (info.context and info.context.get("building_digest")) and self.receipt_digest != _receipt_digest(self):
            raise ValueError("Core provenance receipt digest does not match")
        return self


def _receipt_digest(receipt: CoreProvenanceReceipt) -> str:
    return _digest(receipt.model_dump(mode="json", exclude={"receipt_digest"}))


def create_core_provenance_receipt(**values: object) -> CoreProvenanceReceipt:
    values = dict(values)
    normalized = CoreProvenanceReceipt.model_validate(
        {**values, "receipt_digest": "sha256:" + "0" * 64},
        context={"building_digest": True},
    )
    values = normalized.model_dump(mode="python")
    values["receipt_digest"] = _receipt_digest(normalized)
    return CoreProvenanceReceipt.model_validate(values)


class CoreProvenanceAuthorizer(Protocol):
    def authorize(self, ref: ResourceRef, *, at: datetime | None = None) -> None: ...


class CoreProvenanceTrust(Protocol):
    """Caller-owned authenticated Core boundary; a digest is not authentication."""

    def verify(self, receipt: CoreProvenanceReceipt) -> None: ...


class ProcedureReviewAuthorizer(Protocol):
    """Caller-owned authenticated authorization for the review action."""

    def authorize_review(
        self,
        candidate_digest: str,
        reviewer_ref: ResourceRef,
        evidence_refs: tuple[ResourceRef, ...],
        *,
        lineage_refs: tuple[ResourceRef, ...] = (),
        at: datetime,
    ) -> None: ...


class ProcedureInvalidationAuthorizer(Protocol):
    """Caller-owned authenticated authorization for stale-marking writes."""

    def authorize_invalidation(
        self,
        event_id: str,
        changed_ref: ResourceRef,
        event_digest: str,
        *,
        at: datetime,
    ) -> None: ...


class CoreProvenanceIngestion(StrictModel):
    receipt: CoreProvenanceReceipt
    receipt_ref: ResourceRef
    record: ProcedureRecord
    temporal_record: object


class ProcedureReviewResult(StrictModel):
    decision: ProcedureReviewDecision
    promoted_record: ProcedureRecord | None = None


PROCEDURE_EXPLANATION_VERSION = "krail.procedure-explanation.v1"
PROCEDURE_INVALIDATION_VERSION = "krail.procedure-invalidation.v1"


class ProcedureExplanationRequest(StrictModel):
    """Bounded lookup of one exact persisted procedure candidate."""

    candidate_digest: Digest
    max_reviews: int = Field(default=8, ge=1, le=32)
    max_total_bytes: int = Field(default=131_072, ge=4_096, le=131_072)


class ProcedureInvalidationEvent(StrictModel):
    schema_version: Literal["krail.procedure-invalidation.v1"] = PROCEDURE_INVALIDATION_VERSION
    event_id: Identifier
    changed_ref: ResourceRef
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    recorded_at: datetime
    event_digest: Digest

    @field_validator("recorded_at")
    @classmethod
    def _event_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("invalidation timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _event_digest_matches(self, info: ValidationInfo) -> "ProcedureInvalidationEvent":
        expected = _digest(self.model_dump(mode="json", exclude={"event_digest"}))
        if not (info.context and info.context.get("building_digest")) and self.event_digest != expected:
            raise ValueError("procedure invalidation event digest does not match")
        return self


class ProcedureFreshnessProjection(StrictModel):
    schema_version: Literal["krail.procedure-freshness.v1"] = "krail.procedure-freshness.v1"
    procedure_digest: Digest
    freshness: Literal["current", "stale"]
    stale_reasons: tuple[Annotated[str, StringConstraints(min_length=1, max_length=2048)], ...] = Field(max_length=1024)
    invalidation_refs: tuple[ResourceRef, ...] = Field(max_length=1024)
    projection_digest: Digest


def create_invalidation_event(
    *, event_id: str, changed_ref: ResourceRef, reason: str, recorded_at: datetime
) -> ProcedureInvalidationEvent:
    provisional = ProcedureInvalidationEvent.model_validate(
        {
            "event_id": event_id,
            "changed_ref": changed_ref,
            "reason": reason,
            "recorded_at": recorded_at,
            "event_digest": "sha256:" + "0" * 64,
        },
        context={"building_digest": True},
    )
    return ProcedureInvalidationEvent.model_validate(
        provisional.model_copy(
            update={"event_digest": _digest(provisional.model_dump(mode="json", exclude={"event_digest"}))}
        ).model_dump(mode="python")
    )


class ProcedureExplanation(StrictModel):
    """Read-only, digest-bound explanation of procedure history and support."""

    schema_version: Literal["krail.procedure-explanation.v1"] = PROCEDURE_EXPLANATION_VERSION
    explanation_digest: Digest
    candidate: ProcedureRecord
    reviewed: ProcedureRecord | None = None
    decisions: tuple[ProcedureReviewDecision, ...] = Field(max_length=32)
    package_refs: tuple[ResourceRef, ...] = Field(max_length=32)
    command_refs: tuple[ResourceRef, ...] = Field(max_length=64)
    environment_refs: tuple[ResourceRef, ...] = Field(max_length=32)
    evidence_refs: tuple[ResourceRef, ...] = Field(max_length=64)
    dependency_refs: tuple[ResourceRef, ...] = Field(max_length=128)
    decision_refs: tuple[ResourceRef, ...] = Field(max_length=32)
    invalidation_refs: tuple[ResourceRef, ...] = Field(max_length=16)
    lifecycle: Literal["desired", "reviewed"]
    review_status: Literal["missing", "accepted", "rejected", "conflicting"]
    activation_status: Literal["not_observed", "observed"]
    support_status: Literal["current", "stale", "missing", "conflicting"]
    change_explanation: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    gaps: tuple[Annotated[str, StringConstraints(min_length=1, max_length=1024)], ...] = Field(max_length=16)
    truncated: bool = False

    @model_validator(mode="after")
    def _digest_matches(self, info: ValidationInfo) -> "ProcedureExplanation":
        expected = _digest(self.model_dump(mode="json", exclude={"explanation_digest"}))
        if not (info.context and info.context.get("building_digest")) and self.explanation_digest != expected:
            raise ValueError("procedure explanation digest does not match")
        return self


def _procedure_refs(record: ProcedureRecord) -> tuple[ResourceRef, ...]:
    refs = record.package_refs + record.command_refs + record.environment_refs
    refs += record.test_evidence_refs + record.dependency_refs
    if record.activation_ref is not None:
        refs += (record.activation_ref,)
    if record.review_ref is not None:
        refs += (record.review_ref,)
    return refs


def _decision_ref(decision: ProcedureReviewDecision) -> ResourceRef:
    return ResourceRef(
        authority="krail://procedural-memory",
        resource_type="procedure-review",
        resource_id=decision.decision_id,
        version=decision.schema_version,
        digest=decision.decision_digest,
    )


def _decision_refs(decision: ProcedureReviewDecision) -> tuple[ResourceRef, ...]:
    """Return every exact lineage ref nested in a decision plus its row ref."""

    return (decision.reviewer_ref, *decision.evidence_refs, _decision_ref(decision))


def _invalidation_ref(event: ProcedureInvalidationEvent) -> ResourceRef:
    return ResourceRef(
        authority="krail://procedural-memory",
        resource_type="procedure-invalidation",
        resource_id=event.event_id,
        version=event.schema_version,
        digest=event.event_digest,
    )


def _review_action_refs(record: ProcedureRecord) -> tuple[ResourceRef, ...]:
    """Refs the signed review action must scope; decision refs use read auth."""

    return tuple(ref for ref in _procedure_refs(record) if ref != record.review_ref)


class CoreProvenanceRepository:
    """Project-scoped durable authority backed by the canonical semantic store."""

    record_kind = "core_provenance"
    review_kind = "procedure_review"

    def __init__(self, path: str, *, tenant_id: str, project_id: str) -> None:
        self.store = JsonSemanticStore(path)
        self.tenant_id = tenant_id
        self.project_id = project_id

    def load(self, receipt_id: str) -> CoreProvenanceIngestion | None:
        row = self.store.get(self.tenant_id, self.project_id, self.record_kind, receipt_id)
        if row is None:
            return None
        receipt = CoreProvenanceReceipt.model_validate(row.payload["receipt"])
        if receipt.receipt_id != receipt_id:
            raise ValueError("stored Core provenance receipt identifier is inconsistent")
        record = ProcedureRecord.model_validate(row.payload["record"])
        _validate_record_binding(receipt, record)
        receipt_ref = ResourceRef(
            authority=CORE_ISSUER,
            resource_type="provenance-receipt",
            resource_id=receipt.receipt_id,
            version=receipt.schema_version,
            digest=receipt.receipt_digest,
        )
        return CoreProvenanceIngestion(
            receipt=receipt,
            receipt_ref=receipt_ref,
            record=record,
            temporal_record=procedure_temporal_record(record),
        )

    def save(self, value: CoreProvenanceIngestion, *, at: datetime) -> CoreProvenanceIngestion:
        payload = {
            "receipt": value.receipt.model_dump(mode="json"),
            "record": value.record.model_dump(mode="json"),
        }
        with self.store.transaction():
            current = self.store.get(self.tenant_id, self.project_id, self.record_kind, value.receipt.receipt_id)
            if current is not None:
                existing = self.load(value.receipt.receipt_id)
                if existing is None or existing.receipt.receipt_digest != value.receipt.receipt_digest:
                    raise ValueError("conflicting receipt identifier replay")
                return existing
            row = SemanticRow(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                record_kind=self.record_kind,
                record_id=value.receipt.receipt_id,
                revision=1,
                payload=payload,
                created_at=at,
                updated_at=at,
            )
            self.store.put(row, expected_revision=0)
        return value

    def find_procedure(self, candidate_digest: str) -> ProcedureRecord | None:
        """Find an exact proposed procedure in this canonical project scope."""

        for row in self.store.list(self.tenant_id, self.project_id, kind=self.record_kind):
            try:
                receipt = CoreProvenanceReceipt.model_validate(row.payload["receipt"])
                record = ProcedureRecord.model_validate(row.payload["record"])
                _validate_record_binding(receipt, record)
            except Exception as exc:
                raise ValueError("stored Core provenance procedure is invalid") from exc
            if record.record_digest == candidate_digest:
                return record
        return None

    def load_review(self, decision_id: str) -> ProcedureReviewResult | None:
        row = self.store.get(self.tenant_id, self.project_id, self.review_kind, decision_id)
        if row is None:
            return None
        decision = ProcedureReviewDecision.model_validate(row.payload["decision"])
        promoted = row.payload.get("promoted_record")
        candidate = self.find_procedure(decision.candidate_digest)
        if candidate is None:
            raise ValueError("stored review candidate is stale or unavailable")
        expected = promote_reviewed_procedure(candidate, decision)
        if (promoted is None) != (expected is None):
            raise ValueError("stored review promotion does not match its decision")
        stored = ProcedureRecord.model_validate(promoted) if promoted is not None else None
        if stored != expected:
            raise ValueError("stored review promotion is not derived from its candidate")
        return ProcedureReviewResult(
            decision=decision,
            promoted_record=stored,
        )

    def list_reviews_for_candidate(
        self, candidate_digest: str, *, max_reviews: int = 32
    ) -> tuple[ProcedureReviewResult, ...]:
        """Return validated review history without changing canonical rows."""

        if not 1 <= max_reviews <= 32:
            raise ValueError("max_reviews must be between 1 and 32")
        results: list[ProcedureReviewResult] = []
        for row in self.store.list(self.tenant_id, self.project_id, kind=self.review_kind):
            decision = ProcedureReviewDecision.model_validate(row.payload["decision"])
            if decision.candidate_digest != candidate_digest:
                continue
            loaded = self.load_review(decision.decision_id)
            if loaded is None:  # pragma: no cover - concurrent deletion is fail closed
                raise ValueError("stored procedure review disappeared during read")
            results.append(loaded)
            if len(results) > max_reviews:
                raise ValueError("procedure review history exceeds requested bound")
        return tuple(sorted(results, key=lambda item: item.decision.decision_id))

    def _invalidation_events(self) -> tuple[ProcedureInvalidationEvent, ...]:
        events: list[ProcedureInvalidationEvent] = []
        for row in self.store.list(self.tenant_id, self.project_id, kind="procedure_invalidation"):
            events.append(ProcedureInvalidationEvent.model_validate(row.payload["event"]))
        return tuple(sorted(events, key=lambda event: (event.recorded_at, event.event_id, event.event_digest)))

    def record_invalidation(
        self,
        *,
        event_id: str,
        changed_ref: ResourceRef,
        reason: str,
        at: datetime,
        authorizer: ProcedureInvalidationAuthorizer,
        projection: TemporalProjectionService | None = None,
        projection_id: str = "core-provenance",
    ) -> ProcedureInvalidationEvent:
        if not callable(getattr(authorizer, "authorize_invalidation", None)):
            raise PermissionError("procedure invalidation action denied")
        event = create_invalidation_event(
            event_id=event_id, changed_ref=changed_ref, reason=reason, recorded_at=at
        )
        try:
            authorizer.authorize_invalidation(event_id, changed_ref, event.event_digest, at=at)
        except PermissionError as exc:
            raise PermissionError("procedure invalidation access denied") from exc
        with self.store.transaction():
            current = self.store.get(self.tenant_id, self.project_id, "procedure_invalidation", event_id)
            if current is not None:
                existing = ProcedureInvalidationEvent.model_validate(current.payload["event"])
                if existing.event_digest != event.event_digest:
                    raise ValueError("conflicting invalidation identifier replay")
                final_at = at
                try:
                    authorizer.authorize_invalidation(event_id, changed_ref, event.event_digest, at=final_at)
                except PermissionError as exc:
                    raise PermissionError("procedure invalidation access denied") from exc
                return existing
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id,
                    project_id=self.project_id,
                    record_kind="procedure_invalidation",
                    record_id=event_id,
                    revision=1,
                    payload={"event": event.model_dump(mode="json")},
                    created_at=at,
                    updated_at=at,
                ),
                expected_revision=0,
            )
        try:
            authorizer.authorize_invalidation(event_id, changed_ref, event.event_digest, at=at)
        except PermissionError as exc:
            raise PermissionError("procedure invalidation access denied") from exc
        if projection is not None:
            projection.mark_dirty(changed_ref, projection_id=projection_id, reason=reason, at=at)
            projection.recompute(projection_id=projection_id, valid_at=at, known_at=at, at=at)
        return event

    def rebuild_freshness_projection(self, *, at: datetime) -> tuple[ProcedureFreshnessProjection, ...]:
        events = self._invalidation_events()
        records: list[ProcedureRecord] = []
        for row in self.store.list(self.tenant_id, self.project_id, kind=self.record_kind):
            receipt = CoreProvenanceReceipt.model_validate(row.payload["receipt"])
            record = ProcedureRecord.model_validate(row.payload["record"])
            _validate_record_binding(receipt, record)
            records.append(record)
        for row in self.store.list(self.tenant_id, self.project_id, kind=self.review_kind):
            decision = ProcedureReviewDecision.model_validate(row.payload["decision"])
            loaded = self.load_review(decision.decision_id)
            if loaded and loaded.promoted_record is not None:
                records.append(loaded.promoted_record)
        projections: list[ProcedureFreshnessProjection] = []
        for record in records:
            affected = tuple(
                event for event in events
                if event.changed_ref.exact_key in {
                    ref.exact_key for ref in _procedure_refs(record)
                }
            )
            refs = tuple(_invalidation_ref(event) for event in affected)
            reasons = tuple(dict.fromkeys(event.reason for event in affected))
            freshness = "stale" if affected else record.freshness
            body = {
                "schema_version": "krail.procedure-freshness.v1",
                "procedure_digest": record.record_digest,
                "freshness": freshness,
                "stale_reasons": reasons,
                "invalidation_refs": [ref.model_dump(mode="json") for ref in refs],
            }
            projection = ProcedureFreshnessProjection(
                **body,
                projection_digest=_digest(body),
            )
            projections.append(projection)
            with self.store.transaction():
                current = self.store.get(self.tenant_id, self.project_id, "procedure_freshness", record.record_digest)
                revision = current.revision if current else 0
                if current is None or current.payload != {"projection": projection.model_dump(mode="json")}:
                    self.store.put(
                        SemanticRow(
                            tenant_id=self.tenant_id,
                            project_id=self.project_id,
                            record_kind="procedure_freshness",
                            record_id=record.record_digest,
                            revision=revision + 1,
                            payload={"projection": projection.model_dump(mode="json")},
                            created_at=current.created_at if current else at,
                            updated_at=at,
                        ),
                        expected_revision=revision,
                    )
        return tuple(sorted(projections, key=lambda item: item.procedure_digest))

    def freshness_for(
        self,
        procedure_digest: str,
        *,
        lineage_refs: tuple[ResourceRef, ...] = (),
        at: datetime | None = None,
    ) -> ProcedureFreshnessProjection | None:
        """Read a projection only after reconciling newer matching events."""

        row = self.store.get(self.tenant_id, self.project_id, "procedure_freshness", procedure_digest)
        projection = ProcedureFreshnessProjection.model_validate(row.payload["projection"]) if row else None
        expected = tuple(
            _invalidation_ref(event)
            for event in self._invalidation_events()
            if event.changed_ref.exact_key in {ref.exact_key for ref in lineage_refs}
        )
        actual = projection.invalidation_refs if projection is not None else ()
        if tuple(ref.exact_key for ref in expected) != tuple(ref.exact_key for ref in actual):
            self.rebuild_freshness_projection(at=at or datetime.now(UTC))
            row = self.store.get(self.tenant_id, self.project_id, "procedure_freshness", procedure_digest)
            projection = ProcedureFreshnessProjection.model_validate(row.payload["projection"]) if row else None
            actual = projection.invalidation_refs if projection is not None else ()
            if tuple(ref.exact_key for ref in expected) != tuple(ref.exact_key for ref in actual):
                raise ValueError("procedure freshness projection is out of date")
        return projection

    def save_review(self, result: ProcedureReviewResult, *, at: datetime) -> ProcedureReviewResult:
        candidate = self.find_procedure(result.decision.candidate_digest)
        if candidate is None:
            raise ValueError("procedure candidate digest is stale or unavailable")
        expected = promote_reviewed_procedure(candidate, result.decision)
        if result.promoted_record != expected:
            raise ValueError("review promotion is not derived from its candidate")
        payload = {
            "decision": result.decision.model_dump(mode="json"),
            "promoted_record": result.promoted_record.model_dump(mode="json") if result.promoted_record else None,
        }
        with self.store.transaction():
            current = self.store.get(self.tenant_id, self.project_id, self.review_kind, result.decision.decision_id)
            if current is not None:
                existing = self.load_review(result.decision.decision_id)
                if existing is None or existing.decision.decision_digest != result.decision.decision_digest:
                    raise ValueError("conflicting review identifier replay")
                return existing
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id,
                    project_id=self.project_id,
                    record_kind=self.review_kind,
                    record_id=result.decision.decision_id,
                    revision=1,
                    payload=payload,
                    created_at=at,
                    updated_at=at,
                ),
                expected_revision=0,
            )
        return result


class ProcedureReviewService:
    """Explicit, authorized review and promotion over Core-provenance candidates."""

    def __init__(self, *, repository: CoreProvenanceRepository, clock=None, projection: TemporalProjectionService | None = None, projection_writer: ProjectionWriter | None = None, projection_id: str = "core-provenance") -> None:
        if (projection is None) != (projection_writer is None):
            raise ValueError("projection and explicit projection writer must be paired")
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._projection = projection
        self._projection_writer = projection_writer
        self._projection_id = projection_id

    def _sync_projection(self, records: tuple[ProcedureRecord, ...], *, at: datetime) -> None:
        if self._projection is None or self._projection_writer is None:
            return
        for temporal in procedure_temporal_history(records):
            self._projection.ingest(temporal, at=at, writer=self._projection_writer)
        self._projection.rebuild(projection_id=self._projection_id, valid_at=at, known_at=at, at=at)

    def review(
        self,
        candidate_digest: str,
        *,
        decision_id: str,
        reviewer_ref: ResourceRef,
        evidence_refs: tuple[ResourceRef, ...],
        accepted: bool,
        authorizer: CoreProvenanceAuthorizer,
        review_authorizer: ProcedureReviewAuthorizer,
    ) -> ProcedureReviewResult:
        now = self._clock()
        existing = self._repository.load_review(decision_id)
        if existing is not None:
            if (
                existing.decision.candidate_digest != candidate_digest
                or existing.decision.outcome != ("accepted" if accepted else "rejected")
                or existing.decision.reviewer_ref != reviewer_ref
                or existing.decision.evidence_refs != evidence_refs
            ):
                raise ValueError("conflicting review identifier replay")
            candidate = self._repository.find_procedure(candidate_digest)
            if candidate is None:
                raise ValueError("procedure candidate digest is stale or unavailable")
            reviewed = existing.promoted_record
            try:
                review_authorizer.authorize_review(
                    candidate_digest,
                    existing.decision.reviewer_ref,
                    existing.decision.evidence_refs,
                    lineage_refs=_review_action_refs(reviewed or candidate),
                    at=now,
                )
            except PermissionError as exc:
                raise PermissionError("procedure review action denied") from exc
            refs = _procedure_refs(reviewed or candidate)
            refs += (existing.decision.reviewer_ref, *existing.decision.evidence_refs)
            try:
                for ref in refs:
                    authorizer.authorize(ref, at=now)
            except PermissionError as exc:
                raise PermissionError("procedure review access denied") from exc
            final_now = self._clock()
            try:
                review_authorizer.authorize_review(
                    candidate_digest,
                    existing.decision.reviewer_ref,
                    existing.decision.evidence_refs,
                    lineage_refs=_review_action_refs(reviewed or candidate),
                    at=final_now,
                )
            except PermissionError as exc:
                raise PermissionError("procedure review action denied") from exc
            try:
                for ref in refs:
                    authorizer.authorize(ref, at=final_now)
            except PermissionError as exc:
                raise PermissionError("procedure review access denied") from exc
            self._sync_projection(tuple(record for record in (candidate, reviewed) if record is not None), at=now)
            return existing
        candidate = self._repository.find_procedure(candidate_digest)
        if candidate is None:
            raise ValueError("procedure candidate digest is stale or unavailable")
        refs = _procedure_refs(candidate)
        refs += (reviewer_ref, *evidence_refs)
        try:
            for ref in refs:
                authorizer.authorize(ref, at=now)
        except PermissionError as exc:
            raise PermissionError("procedure review access denied") from exc
        try:
            review_authorizer.authorize_review(
                candidate_digest,
                reviewer_ref,
                evidence_refs,
                lineage_refs=_review_action_refs(candidate),
                at=now,
            )
        except PermissionError as exc:
            raise PermissionError("procedure review action denied") from exc
        decision = create_review_decision(
            decision_id=decision_id,
            candidate_digest=candidate_digest,
            outcome="accepted" if accepted else "rejected",
            reviewer_ref=reviewer_ref,
            evidence_refs=evidence_refs,
            recorded_at=now,
        )
        promoted = promote_reviewed_procedure(candidate, decision)
        result = ProcedureReviewResult(decision=decision, promoted_record=promoted)
        result = self._repository.save_review(result, at=now)
        final_now = self._clock()
        try:
            for ref in refs + (_procedure_refs(promoted) if promoted is not None else ()):
                authorizer.authorize(ref, at=final_now)
        except PermissionError as exc:
            raise PermissionError("procedure review access denied") from exc
        try:
            review_authorizer.authorize_review(
                candidate_digest,
                reviewer_ref,
                evidence_refs,
                lineage_refs=tuple(
                    dict.fromkeys(
                        _review_action_refs(candidate)
                        + (_review_action_refs(promoted) if promoted is not None else ())
                    )
                ),
                at=final_now,
            )
        except PermissionError as exc:
            raise PermissionError("procedure review action denied") from exc
        self._sync_projection(tuple(record for record in (candidate, result.promoted_record) if record is not None), at=final_now)
        return result


class ProcedureExplanationService:
    """Authorized read-only explanation over the canonical procedure history."""

    def __init__(self, *, repository: CoreProvenanceRepository, clock=None, projection: TemporalProjectionService | None = None, projection_id: str = "core-provenance") -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._projection = projection
        self._projection_id = projection_id

    def explain(
        self,
        request: ProcedureExplanationRequest,
        *,
        authorizer: CoreProvenanceAuthorizer,
    ) -> ProcedureExplanation:
        at = self._clock()
        candidate = self._repository.find_procedure(request.candidate_digest)
        if candidate is None:
            raise ValueError("procedure candidate digest is stale or unavailable")
        reviews = self._repository.list_reviews_for_candidate(
            request.candidate_digest, max_reviews=request.max_reviews
        )
        decisions = tuple(item.decision for item in reviews)
        accepted = tuple(item for item in reviews if item.decision.outcome == "accepted")
        rejected = tuple(item for item in reviews if item.decision.outcome == "rejected")
        if accepted and rejected:
            review_status = "conflicting"
        elif len(accepted) > 1:
            review_status = "conflicting"
        elif accepted:
            review_status = "accepted"
        elif rejected:
            review_status = "rejected"
        else:
            review_status = "missing"
        reviewed = accepted[0].promoted_record if review_status == "accepted" else None
        all_records = (candidate,) + ((reviewed,) if reviewed is not None else ())
        projections = tuple(
            projection for record in all_records
            if (projection := self._repository.freshness_for(
                record.record_digest, lineage_refs=_procedure_refs(record), at=at
            )) is not None
        )
        invalidation_refs = tuple(dict.fromkeys(
            ref for projection in projections for ref in projection.invalidation_refs
        ))
        if len(invalidation_refs) > 16:
            raise ValueError("procedure freshness history exceeds explanation bound")
        projection_reasons = tuple(dict.fromkeys(
            reason for projection in projections for reason in projection.stale_reasons
        ))
        if len(projection_reasons) > 16:
            raise ValueError("procedure freshness causes exceed explanation bound")
        package_refs = tuple(dict.fromkeys(ref for record in all_records for ref in record.package_refs))
        command_refs = tuple(dict.fromkeys(ref for record in all_records for ref in record.command_refs))
        environment_refs = tuple(dict.fromkeys(ref for record in all_records for ref in record.environment_refs))
        evidence_refs = tuple(dict.fromkeys(
            [ref for record in all_records for ref in record.test_evidence_refs]
            + [ref for item in reviews for ref in item.decision.evidence_refs]
        ))
        dependency_refs = tuple(dict.fromkeys(ref for record in all_records for ref in record.dependency_refs))
        decision_refs = tuple(_decision_ref(item.decision) for item in reviews)
        refs = tuple(dict.fromkeys(
            (*(ref for record in all_records for ref in _procedure_refs(record)),
             *(ref for item in decisions for ref in _decision_refs(item)),
             *invalidation_refs)
        ))
        try:
            for ref in refs:
                authorizer.authorize(ref, at=at)
        except PermissionError as exc:
            raise PermissionError("procedure explanation access denied") from exc
        stale = any(
            record.freshness == "stale"
            or any(projection.freshness == "stale" for projection in projections
                   if projection.procedure_digest == record.record_digest)
            for record in all_records
        )
        if self._projection is not None:
            temporal = procedure_temporal_history(all_records)
            temporal_digests = {item.record_digest for item in temporal}
            projection_stale = any(
                any(digest in temporal_digests for digest in state.record_digests)
                and any(item.status != "current" for item in state.dependency_states)
                for state in self._projection.current_state(self._projection_id)
            )
            stale = stale or projection_stale
        gaps: list[str] = []
        if review_status == "missing":
            gaps.append("no persisted review decision supports this candidate")
        if review_status == "conflicting":
            gaps.append("persisted review decisions conflict; no reviewed revision is selected")
        if stale:
            gaps.extend(reason for record in all_records for reason in record.stale_reasons)
            gaps.extend(reason for projection in projections for reason in projection.stale_reasons)
            if self._projection is not None and not projection_reasons:
                gaps.append("temporal dependency projection reports stale or dirty exact input state")
        if reviewed is None and accepted:
            gaps.append("accepted review history is not uniquely selectable")
        if review_status == "conflicting":
            support_status = "conflicting"
        elif stale:
            support_status = "stale"
        elif review_status == "missing":
            support_status = "missing"
        else:
            support_status = "current"
        activation = any(record.activation_ref is not None for record in all_records)
        if reviewed is not None:
            change = (
                f"Reviewed revision {reviewed.record_digest} supersedes candidate "
                f"{candidate.record_digest}; review rationale is preserved on the candidate "
                "and exact decision refs. Activation remains an external observation."
            )
        else:
            change = (
                f"Candidate {candidate.record_digest} remains {candidate.lifecycle}; "
                "no reviewed revision or activation is inferred from this read."
            )
        values = dict(
            schema_version=PROCEDURE_EXPLANATION_VERSION,
            explanation_digest="sha256:" + "0" * 64,
            candidate=candidate,
            reviewed=reviewed,
            decisions=decisions,
            package_refs=package_refs,
            command_refs=command_refs,
            environment_refs=environment_refs,
            evidence_refs=evidence_refs,
            dependency_refs=dependency_refs,
            decision_refs=decision_refs,
            invalidation_refs=invalidation_refs,
            lifecycle=reviewed.lifecycle if reviewed is not None else candidate.lifecycle,
            review_status=review_status,
            activation_status="observed" if activation else "not_observed",
            support_status=support_status,
            change_explanation=change,
            gaps=tuple(dict.fromkeys(gaps)),
            truncated=False,
        )
        provisional = ProcedureExplanation.model_validate(values, context={"building_digest": True})
        values["explanation_digest"] = _digest(provisional.model_dump(mode="json", exclude={"explanation_digest"}))
        result = ProcedureExplanation.model_validate(values)
        if len(result.model_dump_json().encode("utf-8")) > request.max_total_bytes:
            raise ValueError("procedure explanation exceeds requested byte bound")
        # Close the revocation race between assembling and returning the packet.
        final_at = self._clock()
        try:
            for ref in refs:
                authorizer.authorize(ref, at=final_at)
        except PermissionError as exc:
            raise PermissionError("procedure explanation access denied") from exc
        return result


class CoreProvenanceService:
    """Pure idempotent receipt ingestion; durable storage is caller-owned."""

    def __init__(self, *, clock=None, repository: CoreProvenanceRepository | None = None, projection: TemporalProjectionService | None = None, projection_writer: ProjectionWriter | None = None, projection_id: str = "core-provenance") -> None:
        if (projection is None) != (projection_writer is None):
            raise ValueError("projection and explicit projection writer must be paired")
        self._receipts: dict[str, CoreProvenanceIngestion] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = repository
        self._projection = projection
        self._projection_writer = projection_writer
        self._projection_id = projection_id

    def ingest(
        self,
        receipt: CoreProvenanceReceipt,
        *,
        authorizer: CoreProvenanceAuthorizer,
        trust: CoreProvenanceTrust,
    ) -> CoreProvenanceIngestion:
        try:
            trust.verify(receipt)
        except PermissionError as exc:
            raise PermissionError("Core provenance origin is not authenticated") from exc
        now = self._clock()
        refs = (receipt.command_ref, receipt.environment_ref)
        try:
            for ref in refs:
                authorizer.authorize(ref, at=now)
        except PermissionError as exc:
            raise PermissionError("Core provenance access denied") from exc

        existing = self._receipts.get(receipt.receipt_id)
        if existing is None and self._repository is not None:
            existing = self._repository.load(receipt.receipt_id)
            if existing is not None:
                self._receipts[receipt.receipt_id] = existing
        if existing is not None:
            if existing.receipt.receipt_digest != receipt.receipt_digest:
                raise ValueError("conflicting receipt identifier replay")
            replay_now = self._clock()
            try:
                for ref in refs:
                    authorizer.authorize(ref, at=replay_now)
            except PermissionError as exc:
                raise PermissionError("Core provenance access denied") from exc
            if self._projection is not None and self._projection_writer is not None:
                self._projection.ingest(existing.temporal_record, at=replay_now, writer=self._projection_writer)
            return existing

        receipt_ref = ResourceRef(
            authority=CORE_ISSUER,
            resource_type="provenance-receipt",
            resource_id=receipt.receipt_id,
            version=receipt.schema_version,
            digest=receipt.receipt_digest,
        )
        record = create_procedure(
            procedure_id=f"opensaddle/environment/{receipt.environment_ref.resource_id}",
            procedure_version=receipt.environment_ref.version,
            lifecycle="desired",
            authority=receipt.command_ref.authority,
            writer_family="core-provenance-adapter",
            valid_from=receipt.observed_at,
            recorded_at=now,
            package_refs=(),
            command_refs=(receipt.command_ref,),
            environment_refs=(receipt.environment_ref,),
            test_evidence_refs=(),
            dependency_refs=(),
            rationale="Observed Core command and environment provenance; execution and review remain external.",
        )
        temporal = procedure_temporal_record(record)
        final_now = self._clock()
        try:
            for ref in refs:
                authorizer.authorize(ref, at=final_now)
        except PermissionError as exc:
            raise PermissionError("Core provenance access denied") from exc
        result = CoreProvenanceIngestion(
            receipt=receipt,
            receipt_ref=receipt_ref,
            record=record,
            temporal_record=temporal,
        )
        if self._repository is not None:
            result = self._repository.save(result, at=now)
        if self._projection is not None and self._projection_writer is not None:
            self._projection.ingest(result.temporal_record, at=final_now, writer=self._projection_writer)
            self._projection.rebuild(projection_id=self._projection_id, valid_at=final_now, known_at=final_now, at=final_now)
        self._receipts[receipt.receipt_id] = result
        return result


def _validate_record_binding(receipt: CoreProvenanceReceipt, record: ProcedureRecord) -> None:
    """Reject an independently valid stored procedure under the wrong receipt."""

    if (
        record.lifecycle != "desired"
        or record.procedure_id != f"opensaddle/environment/{receipt.environment_ref.resource_id}"
        or record.procedure_version != receipt.environment_ref.version
        or record.authority != receipt.command_ref.authority
        or record.writer_family != "core-provenance-adapter"
        or record.valid_from != receipt.observed_at
        or record.activation_ref is not None
        or record.command_refs != (receipt.command_ref,)
        or record.environment_refs != (receipt.environment_ref,)
        or record.package_refs
        or record.test_evidence_refs
        or record.dependency_refs
        or record.review_ref is not None
    ):
        raise ValueError("stored Core provenance procedure is not bound to its receipt")


__all__ = [
    "CORE_ISSUER",
    "CORE_PROVENANCE_VERSION",
    "CoreProvenanceIngestion",
    "CoreProvenanceReceipt",
    "CoreProvenanceRepository",
    "CoreProvenanceService",
    "CoreProvenanceTrust",
    "ProcedureReviewResult",
    "ProcedureReviewAuthorizer",
    "ProcedureReviewService",
    "ProcedureExplanation",
    "ProcedureExplanationRequest",
    "ProcedureExplanationService",
    "PROCEDURE_EXPLANATION_VERSION",
    "ProcedureInvalidationEvent",
    "ProcedureFreshnessProjection",
    "PROCEDURE_INVALIDATION_VERSION",
    "create_invalidation_event",
    "create_core_provenance_receipt",
]
