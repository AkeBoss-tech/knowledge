"""Strict records shared by local and hosted persistence implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from krail.provider.v1 import ResourceRef


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]
RecordKind = Literal["capture", "capture_revision", "projection", "idempotency", "tombstone"]
CaptureState = Literal["active", "tombstoned", "erased"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostedRecord(StrictModel):
    """Opaque metadata row with an optimistic aggregate revision."""

    schema_version: Literal["krail.hosted-record.v1"] = "krail.hosted-record.v1"
    tenant_id: NonEmpty
    project_id: NonEmpty
    record_kind: RecordKind
    record_id: NonEmpty
    revision: int = Field(ge=1)
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("hosted timestamps must include a timezone")
        return value


class CaptureRecord(StrictModel):
    schema_version: Literal["krail.hosted-capture.v1"] = "krail.hosted-capture.v1"
    tenant_id: NonEmpty
    project_id: NonEmpty
    capture_id: NonEmpty
    resource_ref: ResourceRef | None
    revision: int = Field(ge=1)
    content_digest: Digest | None
    object_key: NonEmpty | None
    media_type: NonEmpty | None
    byte_size: int | None = Field(default=None, ge=0)
    created_at: datetime
    retention_until: datetime | None = None
    state: CaptureState = "active"
    tombstoned_at: datetime | None = None
    erased_at: datetime | None = None
    erasure_reason_digest: Digest | None = None

    @field_validator("created_at", "retention_until", "tombstoned_at", "erased_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("hosted timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def exact_resource_relationship(self) -> "CaptureRecord":
        if self.state != "erased" and not all((self.resource_ref, self.content_digest, self.object_key, self.media_type, self.byte_size is not None)):
            raise ValueError("active and tombstoned captures require exact content metadata")
        if self.resource_ref is not None and self.capture_id != self.resource_ref.resource_id:
            raise ValueError("capture_id must equal the exact ResourceRef resource_id")
        if self.resource_ref is not None and self.content_digest != self.resource_ref.digest:
            raise ValueError("capture content digest must equal the exact ResourceRef digest")
        if self.state == "active" and (self.tombstoned_at or self.erased_at or self.erasure_reason_digest):
            raise ValueError("active captures cannot contain deletion metadata")
        if self.state == "tombstoned" and self.tombstoned_at is None:
            raise ValueError("tombstoned captures require tombstoned_at")
        if self.state == "erased" and (self.erased_at is None or self.erasure_reason_digest is None):
            raise ValueError("erased captures require erased_at and a reason digest")
        if self.state == "erased" and any((self.resource_ref, self.content_digest, self.object_key, self.media_type, self.byte_size is not None)):
            raise ValueError("erased captures cannot retain content identity or object metadata")
        return self


class ProjectionRecord(StrictModel):
    schema_version: Literal["krail.hosted-projection.v1"] = "krail.hosted-projection.v1"
    tenant_id: NonEmpty
    project_id: NonEmpty
    projection_id: NonEmpty
    projection_kind: Literal["search", "index", "vector", "analytical"]
    rebuildable: Literal[True] = True
    source_authority: Literal["authoritative-metadata-and-objects"] = "authoritative-metadata-and-objects"
    revision: int = Field(ge=1)
    source_digest: Digest
    projection_digest: Digest
    value: dict[str, Any]
    rebuilt_at: datetime

    @field_validator("rebuilt_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("hosted timestamps must include a timezone")
        return value


class BackupBundle(StrictModel):
    schema_version: Literal["krail.hosted-backup.v1"] = "krail.hosted-backup.v1"
    tenant_id: NonEmpty
    project_id: NonEmpty
    created_at: datetime
    records: list[HostedRecord]
    objects: dict[str, str]
    bundle_digest: Digest

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("hosted timestamps must include a timezone")
        return value
