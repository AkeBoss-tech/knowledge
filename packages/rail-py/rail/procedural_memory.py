"""Evidence-backed, versioned environment procedure records.

This is a data contract, not an activation or execution service.  OpenSaddle
owns effective activation, grants, scheduling, and runtime observation; KRAIL
stores why a procedure exists, what exact revisions it depends on, and which
evidence supports its reviewed form.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo, field_validator, model_validator

from krail.provider.v1 import ResourceRef
from rail.temporal_records import TemporalRecord, create_temporal_record


PROCEDURE_VERSION = "krail.procedure-memory.v1"
PROCEDURE_PAYLOAD_SCHEMA_VERSION = "1.0.0"
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
Lifecycle = Literal["desired", "observed_activation", "reviewed"]
Freshness = Literal["current", "stale"]
ReviewOutcome = Literal["accepted", "rejected"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class ProcedureRecord(StrictModel):
    """One immutable desired, observed, or reviewed procedure revision."""

    schema_version: Literal["krail.procedure-memory.v1"] = PROCEDURE_VERSION
    record_digest: Digest
    procedure_id: Identifier
    procedure_version: Identifier
    lifecycle: Lifecycle
    authority: Identifier
    writer_family: Identifier
    valid_from: datetime
    valid_to: datetime | None = None
    recorded_at: datetime
    package_refs: tuple[ResourceRef, ...] = Field(max_length=32)
    command_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=64)
    environment_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=32)
    test_evidence_refs: tuple[ResourceRef, ...] = Field(max_length=64)
    dependency_refs: tuple[ResourceRef, ...] = Field(max_length=128)
    rationale: Summary
    activation_ref: ResourceRef | None = None
    review_ref: ResourceRef | None = None
    supersedes_digest: Digest | None = None
    freshness: Freshness = "current"
    stale_reasons: tuple[Summary, ...] = Field(max_length=16)

    @field_validator("valid_from", "valid_to", "recorded_at")
    @classmethod
    def _timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("procedure timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _validate_semantics(self, info: ValidationInfo) -> "ProcedureRecord":
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from")
        refs = self.package_refs + self.command_refs + self.environment_refs + self.test_evidence_refs + self.dependency_refs
        if self.activation_ref is not None:
            refs += (self.activation_ref,)
        if self.review_ref is not None:
            refs += (self.review_ref,)
        keys = [ref.exact_key for ref in refs]
        if len(keys) != len(set(keys)):
            raise ValueError("procedure lineage references must be unique exact versions")
        if self.lifecycle == "desired" and self.activation_ref is not None:
            raise ValueError("desired procedures cannot claim an observed activation")
        if self.lifecycle == "observed_activation" and self.activation_ref is None:
            raise ValueError("observed activations require an exact activation reference")
        if self.lifecycle == "reviewed" and not self.test_evidence_refs:
            raise ValueError("reviewed procedures require exact test evidence")
        if self.lifecycle == "reviewed" and self.review_ref is None:
            raise ValueError("reviewed procedures require an exact review decision reference")
        if self.lifecycle != "reviewed" and self.review_ref is not None:
            raise ValueError("only reviewed procedures can carry a review decision reference")
        if self.freshness == "current" and self.stale_reasons:
            raise ValueError("current procedures cannot carry stale reasons")
        if self.freshness == "stale" and not self.stale_reasons:
            raise ValueError("stale procedures require a reason")
        expected = _record_digest(self)
        if not (info.context and info.context.get("building_digest")) and self.record_digest != expected:
            raise ValueError("procedure record digest does not match its immutable content")
        return self


class ProcedureReviewDecision(StrictModel):
    """Immutable explicit review attestation for one exact procedure digest."""

    schema_version: Literal["krail.procedure-review.v1"] = "krail.procedure-review.v1"
    decision_id: Identifier
    candidate_digest: Digest
    outcome: ReviewOutcome
    reviewer_ref: ResourceRef
    evidence_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=64)
    recorded_at: datetime
    decision_digest: Digest

    @field_validator("recorded_at")
    @classmethod
    def _review_timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _validate_review(self, info: ValidationInfo) -> "ProcedureReviewDecision":
        refs = (self.reviewer_ref, *self.evidence_refs)
        if len({ref.exact_key for ref in refs}) != len(refs):
            raise ValueError("review lineage references must be unique exact versions")
        if not (info.context and info.context.get("building_digest")) and self.decision_digest != _review_digest(self):
            raise ValueError("review decision digest does not match immutable content")
        return self


def _record_digest(record: ProcedureRecord) -> str:
    return _digest(record.model_dump(mode="json", exclude={"record_digest"}))


def _review_digest(decision: ProcedureReviewDecision) -> str:
    return _digest(decision.model_dump(mode="json", exclude={"decision_digest"}))


def create_review_decision(**values: object) -> ProcedureReviewDecision:
    values = dict(values)
    normalized = ProcedureReviewDecision.model_validate(
        {**values, "decision_digest": "sha256:" + "0" * 64},
        context={"building_digest": True},
    )
    values = normalized.model_dump(mode="python")
    values["decision_digest"] = _review_digest(normalized)
    return ProcedureReviewDecision.model_validate(values)


def verify_review_integrity(decision: ProcedureReviewDecision) -> ProcedureReviewDecision:
    if decision.decision_digest != _review_digest(decision):
        raise ValueError("review decision content was mutated after validation")
    return decision


def promote_reviewed_procedure(
    candidate: ProcedureRecord,
    decision: ProcedureReviewDecision,
) -> ProcedureRecord | None:
    """Apply an explicit decision; rejection never creates a promoted record."""

    verify_procedure_integrity(candidate)
    verify_review_integrity(decision)
    if candidate.record_digest != decision.candidate_digest:
        raise ValueError("review decision does not bind the exact candidate digest")
    if decision.outcome == "rejected":
        return None
    if candidate.lifecycle != "desired":
        raise ValueError("only desired procedures can be promoted by review")
    values = candidate.model_dump(mode="python")
    values.update(
        lifecycle="reviewed",
        test_evidence_refs=decision.evidence_refs,
        procedure_version=f"{candidate.procedure_version}+review.{decision.decision_id}",
        supersedes_digest=candidate.record_digest,
        review_ref=ResourceRef(
            authority="krail://procedural-memory",
            resource_type="procedure-review",
            resource_id=decision.decision_id,
            version=decision.schema_version,
            digest=decision.decision_digest,
        ),
        record_digest="sha256:" + "0" * 64,
    )
    normalized = ProcedureRecord.model_validate(values, context={"building_digest": True})
    values["record_digest"] = _record_digest(normalized)
    return ProcedureRecord.model_validate(values)


def create_procedure(**values: object) -> ProcedureRecord:
    """Create an immutable current procedure revision with a derived digest."""

    values = dict(values)
    values.setdefault("freshness", "current")
    values.setdefault("stale_reasons", ())
    normalized = ProcedureRecord.model_validate(
        {**values, "record_digest": "sha256:" + "0" * 64},
        context={"building_digest": True},
    )
    values = normalized.model_dump(mode="python")
    values["record_digest"] = _record_digest(normalized)
    return ProcedureRecord.model_validate(values)


def verify_procedure_integrity(record: ProcedureRecord) -> ProcedureRecord:
    if record.record_digest != _record_digest(record):
        raise ValueError("procedure content was mutated after validation")
    return record


def invalidate_for_dependency(record: ProcedureRecord, changed_ref: ResourceRef, *, reason: str | None = None) -> ProcedureRecord:
    """Mark a record stale only when an exact declared dependency changed."""

    verify_procedure_integrity(record)
    declared = {ref.exact_key for ref in record.package_refs + record.command_refs + record.environment_refs + record.test_evidence_refs + record.dependency_refs}
    if changed_ref.exact_key not in declared:
        return record
    stale_reason = reason or f"dependency changed: {changed_ref.resource_id}@{changed_ref.version}"
    if record.freshness == "stale" and (reason is None or stale_reason in record.stale_reasons):
        return record
    reasons = tuple(dict.fromkeys((*record.stale_reasons, stale_reason)))
    updated = record.model_copy(update={"freshness": "stale", "stale_reasons": reasons})
    return updated.model_copy(update={"record_digest": _record_digest(updated)})


def supersede(previous: ProcedureRecord, replacement: ProcedureRecord) -> ProcedureRecord:
    """Require explicit lineage before accepting a replacement revision."""

    verify_procedure_integrity(previous)
    verify_procedure_integrity(replacement)
    if previous.procedure_id != replacement.procedure_id:
        raise ValueError("a procedure can supersede only the same procedure identity")
    if (previous.authority, previous.writer_family) != (replacement.authority, replacement.writer_family):
        raise ValueError("a procedure supersession cannot change authority or writer family")
    if replacement.supersedes_digest != previous.record_digest:
        raise ValueError("replacement must name the exact prior procedure digest")
    return replacement


def _procedure_temporal_record(record: ProcedureRecord, *, supersedes_digest: Digest | None = None) -> TemporalRecord:
    """Compose one procedure revision after its temporal supersession is resolved."""

    verify_procedure_integrity(record)
    refs = record.package_refs + record.command_refs + record.environment_refs + record.test_evidence_refs + record.dependency_refs
    return create_temporal_record(
        record_id=f"{record.procedure_id}:{record.procedure_version}",
        entity_id=record.procedure_id,
        entity_authority=record.authority,
        payload_schema=PROCEDURE_VERSION,
        payload_schema_version=PROCEDURE_PAYLOAD_SCHEMA_VERSION,
        kind={"desired": "proposed_change", "observed_activation": "observation", "reviewed": "approved_state"}[record.lifecycle],
        authority=record.authority,
        writer_family=record.writer_family,
        valid_from=record.valid_from,
        valid_to=record.valid_to,
        recorded_at=record.recorded_at,
        source_refs=refs,
        provenance_refs=tuple(
            ref for ref in (record.activation_ref, record.review_ref) if ref is not None
        ),
        revision=record.procedure_version,
        freshness=record.freshness,
        payload={
            "procedure_id": record.procedure_id,
            "procedure_record_digest": record.record_digest,
            "lifecycle": record.lifecycle,
            "rationale": record.rationale,
        },
        supersedes_digest=supersedes_digest,
    )


def procedure_temporal_record(record: ProcedureRecord) -> TemporalRecord:
    """Compose a standalone procedure revision into the generic envelope.

    A replacement's procedure digest cannot be used as a temporal envelope
    digest. Call ``procedure_temporal_history`` when the record supersedes an
    earlier revision so the exact generated envelope link can be resolved.
    """

    verify_procedure_integrity(record)
    if record.supersedes_digest is not None:
        raise ValueError("procedure supersession requires procedure_temporal_history")
    return _procedure_temporal_record(record)


def procedure_temporal_history(
    records: tuple[ProcedureRecord, ...] | list[ProcedureRecord],
) -> tuple[TemporalRecord, ...]:
    """Convert a procedure history while preserving exact supersession links."""

    ordered = replay_procedure_history(records)
    by_digest = {record.record_digest: record for record in ordered}
    converted: dict[str, TemporalRecord] = {}
    visiting: set[str] = set()

    def convert(record: ProcedureRecord) -> TemporalRecord:
        if record.record_digest in converted:
            return converted[record.record_digest]
        if record.record_digest in visiting:
            raise ValueError("procedure supersession cannot contain a cycle")
        visiting.add(record.record_digest)
        parent_digest: Digest | None = None
        if record.supersedes_digest is not None:
            parent = by_digest.get(record.supersedes_digest)
            if parent is None:
                raise ValueError("procedure supersession must reference a supplied exact revision")
            parent_digest = convert(parent).record_digest
        temporal = _procedure_temporal_record(record, supersedes_digest=parent_digest)
        visiting.remove(record.record_digest)
        converted[record.record_digest] = temporal
        return temporal

    return tuple(convert(record) for record in ordered)


def replay_procedure_history(records: tuple[ProcedureRecord, ...] | list[ProcedureRecord]) -> tuple[ProcedureRecord, ...]:
    """Replay procedure revisions deterministically regardless of arrival order."""

    if not records:
        return ()
    if len({(record.procedure_id, record.authority, record.writer_family) for record in records}) != 1:
        raise ValueError("procedure history cannot mix identities")
    if len({record.record_digest for record in records}) != len(records):
        raise ValueError("procedure history contains duplicate revisions")
    if len({record.procedure_version for record in records}) != len(records):
        raise ValueError("procedure history contains conflicting revisions")
    for record in records:
        verify_procedure_integrity(record)
    ordered = tuple(sorted(records, key=lambda item: (item.valid_from, item.recorded_at, item.procedure_version, item.record_digest)))
    known = {record.record_digest for record in ordered}
    for record in ordered:
        if record.supersedes_digest is not None and record.supersedes_digest not in known:
            raise ValueError("procedure supersession must reference a supplied exact revision")
    return ordered


class ProcedureAuthorizer(Protocol):
    """Caller-owned live authorization for one exact evidence reference."""

    def authorize(self, ref: ResourceRef, *, at: datetime | None = None) -> None: ...


def authorize_procedure(record: ProcedureRecord, authorizer: ProcedureAuthorizer) -> ProcedureRecord:
    """Authorize every evidence/lineage ref before exposing procedure metadata."""

    verify_procedure_integrity(record)
    refs = record.package_refs + record.command_refs + record.environment_refs + record.test_evidence_refs + record.dependency_refs
    if record.activation_ref is not None:
        refs += (record.activation_ref,)
    if record.review_ref is not None:
        refs += (record.review_ref,)
    try:
        for ref in refs:
            authorizer.authorize(ref)
    except PermissionError as exc:
        # Do not identify the hidden ref or reveal whether rationale/metadata
        # would have been returned. Callers receive the whole record only after
        # every exact dependency is authorized.
        raise PermissionError("procedure access denied") from exc
    return record


__all__ = [
    "PROCEDURE_VERSION",
    "ProcedureRecord",
    "ProcedureReviewDecision",
    "create_review_decision",
    "promote_reviewed_procedure",
    "verify_review_integrity",
    "create_procedure",
    "authorize_procedure",
    "invalidate_for_dependency",
    "procedure_temporal_record",
    "procedure_temporal_history",
    "verify_procedure_integrity",
    "replay_procedure_history",
    "supersede",
]
