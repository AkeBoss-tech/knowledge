"""Exact opaque Run artifact evidence, separate from v1 operator invocations.

The source is a trusted-local adapter. Its observations prove neither the
provider's identity nor the correctness of the bytes it reports.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from krail.provider.v1 import ResourceRef
from rail.semantic.repository import JsonSemanticStore, SemanticRow


OBSERVED_RUN_ARTIFACT_VERSION = "krail.observed-run-artifact.v1"
MAX_RUN_ARTIFACT_BYTES = 1_048_576
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunArtifactObservation(StrictModel):
    """Caller-observed Core identity; artifact digest is over the original bytes."""

    project_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    run_ref: ResourceRef
    artifact_ref: ResourceRef
    core_run_updated_at: datetime
    representation: Literal["opaque_bytes"] = "opaque_bytes"
    provider_metadata_state: Literal["not_attested"] = "not_attested"

    @field_validator("core_run_updated_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Core Run update time must include a timezone")
        return value

    @model_validator(mode="after")
    def _exact_identity(self) -> "RunArtifactObservation":
        if (self.run_ref.resource_type != "run" or self.run_ref.resource_id != self.run_id
                or self.artifact_ref.resource_type != "artifact"
                or self.artifact_ref.authority != self.run_ref.authority):
            raise ValueError("observed Run and artifact references differ")
        return self


class ObservedRunArtifactEvidence(StrictModel):
    schema_version: Literal["krail.observed-run-artifact.v1"] = OBSERVED_RUN_ARTIFACT_VERSION
    evidence_state: Literal["caller_observed_unverified"] = "caller_observed_unverified"
    observation: RunArtifactObservation
    input_refs: tuple[ResourceRef, ...] = Field(max_length=32)
    evidence_digest: Digest

    @model_validator(mode="after")
    def _validate_integrity(self) -> "ObservedRunArtifactEvidence":
        if self.evidence_digest != _digest(self.model_dump(mode="json", exclude={"evidence_digest"})):
            raise ValueError("observed Run evidence digest does not match")
        return self

    def exact_ref(self) -> ResourceRef:
        return ResourceRef(
            authority="krail://observed-run-artifact",
            resource_type="observed-run-artifact",
            resource_id=self.evidence_digest,
            version=self.schema_version,
            digest=self.evidence_digest,
        )


class TrustedRunArtifactSource(Protocol):
    def identify(self, run_id: str) -> RunArtifactObservation: ...

    def read_artifact(self, ref: ResourceRef, *, max_bytes: int) -> bytes: ...


class RunArtifactAuthorizer(Protocol):
    def authorize(self, ref: ResourceRef) -> None: ...


def _authorize(authorizer: RunArtifactAuthorizer, refs: tuple[ResourceRef, ...]) -> None:
    try:
        for ref in refs:
            authorizer.authorize(ref)
    except PermissionError as exc:
        raise PermissionError("observed Run artifact access denied") from exc


def record_observed_run_artifact(
    run_id: str, *, source: TrustedRunArtifactSource,
    authorizer: RunArtifactAuthorizer, input_refs: tuple[ResourceRef, ...] = (),
) -> ObservedRunArtifactEvidence:
    """Observe exact authorized bytes without decoding or certifying their content."""

    if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
        raise ValueError("Run ID is invalid")
    if not isinstance(input_refs, tuple) or len(input_refs) > 32:
        raise ValueError("observed inputs must be a bounded tuple")
    _authorize(authorizer, input_refs)
    first = source.identify(run_id)
    if not isinstance(first, RunArtifactObservation) or first.run_id != run_id:
        raise ValueError("observed Run identity differs")
    refs = (*input_refs, first.run_ref, first.artifact_ref)
    _authorize(authorizer, refs)
    payload = source.read_artifact(first.artifact_ref, max_bytes=MAX_RUN_ARTIFACT_BYTES)
    if (type(payload) is not bytes or len(payload) > MAX_RUN_ARTIFACT_BYTES
            or not first.artifact_ref.verifies(payload)):
        raise ValueError("observed artifact bytes differ")
    second = source.identify(run_id)
    if second != first:
        raise ValueError("observed Run artifact changed during read")
    _authorize(authorizer, refs)
    body = {"schema_version": OBSERVED_RUN_ARTIFACT_VERSION,
            "evidence_state": "caller_observed_unverified",
            "observation": first, "input_refs": input_refs}
    digest = _digest(ObservedRunArtifactEvidence.model_construct(
        **body, evidence_digest="sha256:" + "0" * 64,
    ).model_dump(mode="json", exclude={"evidence_digest"}))
    return ObservedRunArtifactEvidence.model_validate({**body, "evidence_digest": digest})


def verify_observed_run_artifact_integrity(evidence: ObservedRunArtifactEvidence) -> ObservedRunArtifactEvidence:
    ObservedRunArtifactEvidence.model_validate(evidence.model_dump(mode="python"))
    return evidence


class ObservedRunArtifactRepository:
    """Project-scoped evidence metadata; byte authority is never persisted."""

    record_kind = "observed_run_artifact"

    def __init__(self, path: str, *, tenant_id: str, project_id: str) -> None:
        self.store = JsonSemanticStore(path)
        self.tenant_id = tenant_id
        self.project_id = project_id

    def save(self, evidence: ObservedRunArtifactEvidence, *, source: TrustedRunArtifactSource,
             authorizer: RunArtifactAuthorizer, at: datetime) -> ObservedRunArtifactEvidence:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("record time must include a timezone")
        verify_observed_run_artifact_integrity(evidence)
        if evidence.observation.project_id != self.project_id:
            raise PermissionError("observed Run project differs")
        if record_observed_run_artifact(evidence.observation.run_id, source=source,
                                        authorizer=authorizer, input_refs=evidence.input_refs) != evidence:
            raise ValueError("observed Run artifact changed")
        payload = evidence.model_dump(mode="json")
        with self.store.transaction():
            current = self.store.get(self.tenant_id, self.project_id, self.record_kind,
                                     evidence.evidence_digest)
            if current is not None:
                stored = ObservedRunArtifactEvidence.model_validate(current.payload)
                if stored != evidence:
                    raise ValueError("observed evidence digest collision")
                return stored
            self.store.put(SemanticRow(
                tenant_id=self.tenant_id, project_id=self.project_id,
                record_kind=self.record_kind, record_id=evidence.evidence_digest,
                revision=1, payload=payload, created_at=at, updated_at=at,
            ), expected_revision=0)
        return evidence

    def read_exact(self, ref: ResourceRef, *, source: TrustedRunArtifactSource,
                   authorizer: RunArtifactAuthorizer) -> ObservedRunArtifactEvidence:
        _authorize(authorizer, (ref,))
        if (ref.authority != "krail://observed-run-artifact"
                or ref.resource_type != "observed-run-artifact"
                or ref.version != OBSERVED_RUN_ARTIFACT_VERSION
                or ref.resource_id != ref.digest):
            raise ValueError("observed evidence reference differs")
        row = self.store.get(self.tenant_id, self.project_id, self.record_kind, ref.resource_id)
        if row is None:
            raise LookupError("observed evidence is unavailable")
        evidence = ObservedRunArtifactEvidence.model_validate(row.payload)
        verify_observed_run_artifact_integrity(evidence)
        if evidence.exact_ref() != ref or evidence.observation.project_id != self.project_id:
            raise ValueError("observed evidence scope differs")
        if record_observed_run_artifact(evidence.observation.run_id, source=source,
                                        authorizer=authorizer, input_refs=evidence.input_refs) != evidence:
            raise ValueError("observed Run artifact changed")
        _authorize(authorizer, (ref,))
        return evidence


class RunArtifactAwareProcedureAuthorizer:
    """Resolve evidence and Core refs on every procedure access."""

    def __init__(self, repository: ObservedRunArtifactRepository, source: TrustedRunArtifactSource,
                 delegate: RunArtifactAuthorizer) -> None:
        self.repository = repository
        self.source = source
        self.delegate = delegate

    def authorize(self, ref: ResourceRef) -> None:
        if ref.authority == "krail://observed-run-artifact" and ref.resource_type == "observed-run-artifact":
            try:
                self.repository.read_exact(ref, source=self.source, authorizer=self.delegate)
            except (LookupError, ValueError, PermissionError, TimeoutError, OSError) as exc:
                raise PermissionError("observed Run artifact access denied") from exc
        else:
            self.delegate.authorize(ref)


__all__ = [
    "MAX_RUN_ARTIFACT_BYTES", "OBSERVED_RUN_ARTIFACT_VERSION", "ObservedRunArtifactEvidence",
    "ObservedRunArtifactRepository", "RunArtifactAwareProcedureAuthorizer", "RunArtifactObservation",
    "RunArtifactAuthorizer", "TrustedRunArtifactSource", "record_observed_run_artifact",
    "verify_observed_run_artifact_integrity",
]
