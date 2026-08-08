"""KRAIL-owned history for evidence assembly and semantic changes.

This is deliberately narrower than an operation journal or event ledger.  It
stores only KRAIL epistemic records and exposes digest-only references that may
optionally carry caller-supplied correlation metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


HISTORY_SCHEMA_VERSION = "krail.epistemic-history.v1"
DOMAIN_EVENT_REF_VERSION = "krail.domain-event-ref.v1"

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]
RecordKind = Literal["context-brief-assembled", "semantic-change", "correction", "supersession"]
RecordStatus = Literal["active", "corrected", "superseded", "erased"]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OperationContext(StrictModel):
    """Optional correlation identifiers supplied by an external caller."""

    operation_id: NonEmpty | None = None
    correlation_id: NonEmpty | None = None
    causation_id: NonEmpty | None = None


class DomainEventRef(StrictModel):
    """Digest/correlation-only projection of a KRAIL-owned history record."""

    schema_version: Literal["krail.domain-event-ref.v1"] = DOMAIN_EVENT_REF_VERSION
    event_type: NonEmpty
    event_digest: Digest
    operation_id: NonEmpty | None = None
    correlation_id: NonEmpty | None = None
    causation_id: NonEmpty | None = None

    @classmethod
    def for_digest(
        cls,
        *,
        event_type: str,
        digest: str,
        context: OperationContext | None = None,
    ) -> "DomainEventRef":
        correlation = context or OperationContext()
        return cls(event_type=event_type, event_digest=digest, **correlation.model_dump())


class EpistemicRecord(StrictModel):
    schema_version: Literal["krail.epistemic-history.v1"] = HISTORY_SCHEMA_VERSION
    record_id: Digest
    sequence: int = Field(ge=1)
    kind: RecordKind
    subject_digest: Digest
    detail_digest: Digest
    details: dict[str, Any] | None
    created_at: datetime
    retention_until: datetime | None = None
    operation_context: OperationContext | None = None
    supersedes: Digest | None = None
    corrects: Digest | None = None
    status: RecordStatus = "active"
    replaced_by: Digest | None = None
    erased_at: datetime | None = None
    erasure_reason_digest: Digest | None = None

    @field_validator("created_at", "retention_until", "erased_at")
    @classmethod
    def _timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("history timestamps must include a timezone")
        return value

    def domain_event_ref(self) -> DomainEventRef:
        return DomainEventRef.for_digest(
            event_type=f"krail.{self.kind}.v1",
            digest=self.record_id,
            context=self.operation_context,
        )


class EpistemicHistory:
    """Small JSON-backed store for KRAIL epistemic records only."""

    def __init__(self, project_path: str | Path, *, relative_path: str = "research_plan/state/epistemic_history.json") -> None:
        self.project_path = Path(project_path).resolve()
        self.path = (self.project_path / relative_path).resolve()
        try:
            self.path.relative_to(self.project_path)
        except ValueError as exc:
            raise ValueError("epistemic history must remain inside the KRAIL project") from exc

    def list(self) -> list[EpistemicRecord]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != HISTORY_SCHEMA_VERSION:
            raise ValueError("unsupported epistemic history schema")
        return [EpistemicRecord.model_validate(item) for item in payload.get("records", [])]

    def append(
        self,
        *,
        kind: RecordKind,
        subject_digest: str,
        details: dict[str, Any],
        created_at: datetime,
        retention_until: datetime | None = None,
        operation_context: OperationContext | None = None,
        supersedes: str | None = None,
        corrects: str | None = None,
    ) -> EpistemicRecord:
        records = self.list()
        detail_digest = _digest(details)
        identity = {
            "kind": kind,
            "subject_digest": subject_digest,
            "detail_digest": detail_digest,
            "created_at": created_at.isoformat(),
            "retention_until": retention_until.isoformat() if retention_until else None,
            "operation_context": operation_context.model_dump() if operation_context else None,
            "supersedes": supersedes,
            "corrects": corrects,
        }
        record_id = _digest(identity)
        existing = next((item for item in records if item.record_id == record_id), None)
        if existing is not None:
            return existing
        record = EpistemicRecord(
            record_id=record_id,
            sequence=len(records) + 1,
            kind=kind,
            subject_digest=subject_digest,
            detail_digest=detail_digest,
            details=json.loads(_canonical_json(details)),
            created_at=created_at,
            retention_until=retention_until,
            operation_context=operation_context,
            supersedes=supersedes,
            corrects=corrects,
        )
        records.append(record)
        self._write(records)
        return record

    def supersede(
        self,
        record_id: str,
        *,
        details: dict[str, Any],
        created_at: datetime,
        retention_until: datetime | None = None,
        operation_context: OperationContext | None = None,
    ) -> EpistemicRecord:
        return self._replace(
            record_id,
            kind="supersession",
            status="superseded",
            details=details,
            created_at=created_at,
            retention_until=retention_until,
            operation_context=operation_context,
        )

    def correct(
        self,
        record_id: str,
        *,
        details: dict[str, Any],
        created_at: datetime,
        retention_until: datetime | None = None,
        operation_context: OperationContext | None = None,
    ) -> EpistemicRecord:
        return self._replace(
            record_id,
            kind="correction",
            status="corrected",
            details=details,
            created_at=created_at,
            retention_until=retention_until,
            operation_context=operation_context,
        )

    def erase(self, record_id: str, *, erased_at: datetime, reason: str) -> EpistemicRecord:
        records = self.list()
        index, current = self._locate(records, record_id)
        if current.status == "erased":
            return current
        tombstone = EpistemicRecord.model_validate(
            {
                **current.model_dump(mode="python"),
                "details": None,
                "status": "erased",
                "erased_at": erased_at,
                "erasure_reason_digest": _digest({"reason": reason}),
            }
        )
        records[index] = tombstone
        self._write(records)
        return tombstone

    def enforce_retention(self, *, as_of: datetime) -> list[EpistemicRecord]:
        erased: list[EpistemicRecord] = []
        for record in self.list():
            if record.status != "erased" and record.retention_until is not None and record.retention_until <= as_of:
                erased.append(self.erase(record.record_id, erased_at=as_of, reason="retention-expired"))
        return erased

    def _replace(
        self,
        record_id: str,
        *,
        kind: Literal["correction", "supersession"],
        status: Literal["corrected", "superseded"],
        details: dict[str, Any],
        created_at: datetime,
        retention_until: datetime | None,
        operation_context: OperationContext | None,
    ) -> EpistemicRecord:
        records = self.list()
        index, current = self._locate(records, record_id)
        if current.status == "erased":
            raise ValueError("erased epistemic records cannot be replaced")
        relation = {"corrects": record_id} if kind == "correction" else {"supersedes": record_id}
        replacement = self.append(
            kind=kind,
            subject_digest=current.subject_digest,
            details=details,
            created_at=created_at,
            retention_until=retention_until,
            operation_context=operation_context,
            **relation,
        )
        records = self.list()
        index, current = self._locate(records, record_id)
        records[index] = EpistemicRecord.model_validate(
            {**current.model_dump(mode="python"), "status": status, "replaced_by": replacement.record_id}
        )
        self._write(records)
        return replacement

    @staticmethod
    def _locate(records: list[EpistemicRecord], record_id: str) -> tuple[int, EpistemicRecord]:
        for index, record in enumerate(records):
            if record.record_id == record_id:
                return index, record
        raise KeyError(f"unknown epistemic record: {record_id}")

    def _write(self, records: list[EpistemicRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "records": [item.model_dump(mode="json") for item in sorted(records, key=lambda item: item.sequence)],
        }
        fd, temporary = tempfile.mkstemp(prefix="epistemic-history-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


__all__ = [
    "DOMAIN_EVENT_REF_VERSION",
    "HISTORY_SCHEMA_VERSION",
    "DomainEventRef",
    "EpistemicHistory",
    "EpistemicRecord",
    "OperationContext",
]
