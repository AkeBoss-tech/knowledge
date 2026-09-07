"""Small domain-neutral typed temporal record envelope.

The envelope carries identity, authority, bitemporal timestamps, exact source
lineage, and an opaque domain payload. Domain schemas and executable operators
remain outside this module; an extension registry is a later #21 slice.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo, field_validator, model_validator

from krail.provider.v1 import ResourceRef


TEMPORAL_RECORD_VERSION = "krail.temporal-record.v1"
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
SchemaName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]
Kind = Literal["observation", "estimate", "reported_claim", "approved_state", "hypothesis", "proposed_change"]
Freshness = Literal["current", "stale", "unknown"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class TemporalRecord(StrictModel):
    """Immutable typed state with effective and known time kept distinct."""

    schema_version: Literal["krail.temporal-record.v1"] = TEMPORAL_RECORD_VERSION
    record_id: Identifier
    entity_id: Identifier
    entity_authority: Identifier
    payload_schema: SchemaName
    payload_schema_version: Identifier
    kind: Kind
    authority: Identifier
    writer_family: Identifier
    valid_from: datetime
    valid_to: datetime | None = None
    recorded_at: datetime
    ingested_at: datetime | None = None
    source_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=128)
    provenance_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple, max_length=128)
    revision: Identifier
    freshness: Freshness = "current"
    visibility: Literal["public", "internal", "restricted"] = "internal"
    payload: dict[str, object]
    supersedes_digest: Digest | None = None
    record_digest: Digest

    @field_validator("valid_from", "valid_to", "recorded_at", "ingested_at")
    @classmethod
    def _timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("temporal timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _validate_record(self, info: ValidationInfo) -> "TemporalRecord":
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from")
        refs = self.source_refs + self.provenance_refs
        if len({ref.exact_key for ref in refs}) != len(refs):
            raise ValueError("temporal lineage references must be unique exact versions")
        if self.ingested_at is not None and self.ingested_at < self.recorded_at:
            raise ValueError("ingested_at cannot precede recorded_at")
        if not (info.context and info.context.get("building_digest")) and self.record_digest != _record_digest(self):
            raise ValueError("temporal record digest does not match immutable content")
        return self


def _record_digest(record: TemporalRecord) -> str:
    return _digest(record.model_dump(mode="json", exclude={"record_digest"}))


def create_temporal_record(**values: object) -> TemporalRecord:
    values = dict(values)
    normalized = TemporalRecord.model_validate(
        {**values, "record_digest": "sha256:" + "0" * 64},
        context={"building_digest": True},
    )
    values = normalized.model_dump(mode="python")
    values["record_digest"] = _record_digest(normalized)
    return TemporalRecord.model_validate(values)


def verify_temporal_record_integrity(record: TemporalRecord) -> TemporalRecord:
    if record.record_digest != _record_digest(record):
        raise ValueError("temporal record content was mutated after validation")
    return record


def replay_temporal_records(records: list[TemporalRecord] | tuple[TemporalRecord, ...]) -> tuple[TemporalRecord, ...]:
    """Produce byte-stable order for out-of-order arrivals of one entity."""

    if not records:
        return ()
    identity = {
        (item.entity_authority, item.entity_id, item.payload_schema, item.payload_schema_version, item.authority, item.writer_family)
        for item in records
    }
    if len(identity) != 1:
        raise ValueError("temporal replay cannot mix entity or payload identities")
    if len({item.record_digest for item in records}) != len(records):
        raise ValueError("temporal replay contains duplicate revisions")
    if len({item.revision for item in records}) != len(records):
        raise ValueError("temporal replay contains conflicting revisions")
    for item in records:
        verify_temporal_record_integrity(item)
    ordered = tuple(sorted(records, key=lambda item: (item.valid_from, item.recorded_at, item.revision, item.record_digest)))
    known = {item.record_digest for item in ordered}
    for item in ordered:
        if item.supersedes_digest is not None and item.supersedes_digest not in known:
            raise ValueError("temporal supersession must reference a supplied exact revision")
    return ordered


def query_temporal_records(
    records: list[TemporalRecord] | tuple[TemporalRecord, ...],
    *,
    valid_at: datetime,
    known_at: datetime,
) -> tuple[TemporalRecord, ...]:
    """Return records effective at ``valid_at`` that were known by ``known_at``.

    ``recorded_at`` is the default known time; an explicit ``ingested_at``
    takes precedence when present. Superseding corrections only affect a query
    once the correction itself is within the known-time cutoff. Results remain
    immutable history views and are not a materialized current-state projection.
    """

    for value, name in ((valid_at, "valid_at"), (known_at, "known_at")):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone")
    ordered = replay_temporal_records(records)
    known = tuple(
        item
        for item in ordered
        if (item.ingested_at or item.recorded_at) <= known_at
    )
    superseded = {item.supersedes_digest for item in known if item.supersedes_digest is not None}
    applicable = tuple(
        item
        for item in known
        if item.record_digest not in superseded
        and item.valid_from <= valid_at
        and (item.valid_to is None or valid_at < item.valid_to)
    )
    return tuple(sorted(applicable, key=lambda item: (item.valid_from, item.recorded_at, item.revision, item.record_digest)))


def supersede_temporal_record(previous: TemporalRecord, replacement: TemporalRecord) -> TemporalRecord:
    verify_temporal_record_integrity(previous)
    verify_temporal_record_integrity(replacement)
    if (
        previous.entity_authority,
        previous.entity_id,
        previous.payload_schema,
        previous.payload_schema_version,
        previous.authority,
        previous.writer_family,
    ) != (
        replacement.entity_authority,
        replacement.entity_id,
        replacement.payload_schema,
        replacement.payload_schema_version,
        replacement.authority,
        replacement.writer_family,
    ):
        raise ValueError("temporal supersession cannot change entity, payload, authority, or writer identity")
    if replacement.supersedes_digest != previous.record_digest:
        raise ValueError("temporal replacement must name the exact prior digest")
    return replacement


__all__ = [
    "TEMPORAL_RECORD_VERSION",
    "TemporalRecord",
    "create_temporal_record",
    "replay_temporal_records",
    "query_temporal_records",
    "supersede_temporal_record",
    "verify_temporal_record_integrity",
]
