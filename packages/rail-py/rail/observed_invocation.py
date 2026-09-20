"""Bounded, caller-observed evidence for a non-deterministic extension result.

The observer is an already-configured trusted-local adapter. KRAIL verifies
exact bytes and live read access; it does not authenticate an external issuer,
execute a model, or turn an observation into a verified outcome.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

from krail.provider.v1 import ResourceRef
from rail.extension_registry import InvocationResult, OperatorDescriptor, verify_invocation_integrity
from rail.semantic.repository import JsonSemanticStore, SemanticRow


OBSERVED_INVOCATION_VERSION = "krail.observed-invocation.v1"
MAX_OBSERVED_OUTPUT_BYTES = 1_048_576
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunObservation(StrictModel):
    """A trusted-local adapter's exact declaration, not an issuer signature."""

    run_ref: ResourceRef
    output_artifact_ref: ResourceRef
    model_provider: Name
    model_id: Name
    model_version: Name
    execution_config_digest: Digest
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed Run time must include a timezone")
        return value


class ObservedInvocationEvidence(StrictModel):
    schema_version: Literal["krail.observed-invocation.v1"] = OBSERVED_INVOCATION_VERSION
    evidence_state: Literal["caller_observed_unverified"] = "caller_observed_unverified"
    invocation: InvocationResult
    operator_descriptor_digest: Digest
    observation: RunObservation
    output_encoding: Literal["canonical-json-v1"] = "canonical-json-v1"
    evidence_digest: Digest

    @model_validator(mode="after")
    def _validate_integrity(self) -> "ObservedInvocationEvidence":
        if self.observation.execution_config_digest != self.invocation.config_digest:
            raise ValueError("observation execution config differs from invocation")
        if self.observation.output_artifact_ref.digest != self.invocation.output_digest:
            raise ValueError("observation artifact differs from invocation output")
        if self.evidence_digest != _digest(self.model_dump(mode="json", exclude={"evidence_digest"})):
            raise ValueError("observed invocation evidence digest does not match")
        return self

    def exact_ref(self) -> ResourceRef:
        return ResourceRef(
            authority="krail://observed-invocation",
            resource_type="observed-invocation",
            resource_id=self.evidence_digest,
            version=self.schema_version,
            digest=self.evidence_digest,
        )


class TrustedLocalObservationSource(Protocol):
    def identify(self, invocation: InvocationResult) -> RunObservation: ...

    def read_artifact(self, ref: ResourceRef, *, max_bytes: int) -> bytes: ...


class ObservationAuthorizer(Protocol):
    def authorize(self, ref: ResourceRef) -> None: ...


def _authorize(authorizer: ObservationAuthorizer, refs: tuple[ResourceRef, ...]) -> None:
    try:
        for ref in refs:
            authorizer.authorize(ref)
    except PermissionError as exc:
        raise PermissionError("observed invocation access denied") from exc


def record_observed_invocation(
    invocation: InvocationResult,
    descriptor: OperatorDescriptor,
    *,
    source: TrustedLocalObservationSource,
    authorizer: ObservationAuthorizer,
) -> ObservedInvocationEvidence:
    """Bind a non-deterministic result to exact, currently readable output bytes."""

    if descriptor.deterministic:
        raise ValueError("deterministic operators do not need observed-run evidence")
    verify_invocation_integrity(invocation, descriptor)
    _authorize(authorizer, invocation.input_refs)

    observation = source.identify(invocation)
    if not isinstance(observation, RunObservation):
        raise TypeError("observer must return a RunObservation")
    observation_snapshot = observation.model_dump(mode="json")
    refs = (*invocation.input_refs, observation.run_ref, observation.output_artifact_ref)
    _authorize(authorizer, refs)

    artifact = source.read_artifact(observation.output_artifact_ref, max_bytes=MAX_OBSERVED_OUTPUT_BYTES)
    if not isinstance(artifact, bytes) or len(artifact) > MAX_OBSERVED_OUTPUT_BYTES:
        raise ValueError("observed artifact must be bounded bytes")
    expected = _canonical(invocation.output)
    if len(expected) > MAX_OBSERVED_OUTPUT_BYTES or artifact != expected:
        raise ValueError("observed artifact is not the exact canonical output")
    if "sha256:" + hashlib.sha256(artifact).hexdigest() != observation.output_artifact_ref.digest:
        raise ValueError("observed artifact digest does not match its bytes")
    if observation.execution_config_digest != invocation.config_digest:
        raise ValueError("observed execution configuration differs from invocation")
    current = source.identify(invocation)
    if (not isinstance(current, RunObservation)
            or observation.model_dump(mode="json") != observation_snapshot
            or current.model_dump(mode="json") != observation_snapshot):
        raise ValueError("observed Run identity changed while reading the artifact")
    _authorize(authorizer, refs)
    verify_invocation_integrity(invocation, descriptor)

    body = {
        "schema_version": OBSERVED_INVOCATION_VERSION,
        "evidence_state": "caller_observed_unverified",
        "invocation": invocation,
        "operator_descriptor_digest": descriptor.descriptor_digest,
        "observation": observation,
        "output_encoding": "canonical-json-v1",
    }
    evidence_digest = _digest(ObservedInvocationEvidence.model_construct(
        **body, evidence_digest="sha256:" + "0" * 64,
    ).model_dump(mode="json", exclude={"evidence_digest"}))
    return ObservedInvocationEvidence.model_validate({**body, "evidence_digest": evidence_digest})


def verify_observed_invocation_integrity(
    evidence: ObservedInvocationEvidence, descriptor: OperatorDescriptor,
) -> ObservedInvocationEvidence:
    verify_invocation_integrity(evidence.invocation, descriptor)
    if descriptor.deterministic or evidence.operator_descriptor_digest != descriptor.descriptor_digest:
        raise ValueError("observed invocation operator declaration differs")
    # Pydantic's frozen model does not deeply freeze the output mapping.
    ObservedInvocationEvidence.model_validate(evidence.model_dump(mode="python"))
    return evidence


class ObservedInvocationRepository:
    """Project-scoped durable observations; every read rechecks live sources."""

    record_kind = "observed_invocation"

    def __init__(self, path: str, *, tenant_id: str, project_id: str) -> None:
        self.store = JsonSemanticStore(path)
        self.tenant_id = tenant_id
        self.project_id = project_id

    def save(self, evidence: ObservedInvocationEvidence, descriptor: OperatorDescriptor, *,
             source: TrustedLocalObservationSource, authorizer: ObservationAuthorizer,
             at: datetime) -> ObservedInvocationEvidence:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("observed evidence record time must include a timezone")
        verify_observed_invocation_integrity(evidence, descriptor)
        if record_observed_invocation(evidence.invocation, descriptor, source=source,
                                      authorizer=authorizer) != evidence:
            raise ValueError("observed evidence no longer matches the exact Run artifact")
        payload = evidence.model_dump(mode="json")
        ObservedInvocationEvidence.model_validate(payload)
        with self.store.transaction():
            current = self.store.get(self.tenant_id, self.project_id, self.record_kind,
                                     evidence.evidence_digest)
            if current is not None:
                stored = ObservedInvocationEvidence.model_validate(current.payload)
                verify_observed_invocation_integrity(stored, descriptor)
                if stored != evidence:
                    raise ValueError("observed evidence digest collision")
                return stored
            self.store.put(SemanticRow(
                tenant_id=self.tenant_id, project_id=self.project_id,
                record_kind=self.record_kind, record_id=evidence.evidence_digest,
                revision=1, payload=payload,
                created_at=at, updated_at=at,
            ), expected_revision=0)
        return evidence

    def read_exact(self, ref: ResourceRef, descriptor: OperatorDescriptor, *,
                   source: TrustedLocalObservationSource,
                   authorizer: ObservationAuthorizer) -> ObservedInvocationEvidence:
        _authorize(authorizer, (ref,))
        if (ref.authority != "krail://observed-invocation"
                or ref.resource_type != "observed-invocation"
                or ref.version != OBSERVED_INVOCATION_VERSION
                or ref.resource_id != ref.digest):
            raise ValueError("observed evidence reference shape differs")
        row = self.store.get(self.tenant_id, self.project_id, self.record_kind, ref.resource_id)
        if row is None:
            raise LookupError("observed evidence is not available")
        evidence = ObservedInvocationEvidence.model_validate(row.payload)
        verify_observed_invocation_integrity(evidence, descriptor)
        if evidence.exact_ref() != ref:
            raise ValueError("stored observed evidence reference differs")
        if record_observed_invocation(evidence.invocation, descriptor, source=source,
                                      authorizer=authorizer) != evidence:
            raise ValueError("stored observed evidence no longer matches the exact Run artifact")
        _authorize(authorizer, (ref,))
        verify_observed_invocation_integrity(evidence, descriptor)
        return evidence


class ObservationAwareProcedureAuthorizer:
    """Resolve observed-evidence refs while preserving the caller's live grants."""

    def __init__(self, repository: ObservedInvocationRepository, descriptor: OperatorDescriptor,
                 source: TrustedLocalObservationSource, delegate: ObservationAuthorizer) -> None:
        self.repository = repository
        self.descriptor = descriptor
        self.source = source
        self.delegate = delegate

    def authorize(self, ref: ResourceRef) -> None:
        if ref.authority == "krail://observed-invocation" and ref.resource_type == "observed-invocation":
            try:
                self.repository.read_exact(ref, self.descriptor, source=self.source, authorizer=self.delegate)
            except (LookupError, ValueError, PermissionError) as exc:
                raise PermissionError("observed invocation access denied") from exc
        else:
            self.delegate.authorize(ref)


__all__ = [
    "MAX_OBSERVED_OUTPUT_BYTES", "OBSERVED_INVOCATION_VERSION", "ObservedInvocationEvidence",
    "ObservedInvocationRepository", "ObservationAwareProcedureAuthorizer",
    "ObservationAuthorizer", "RunObservation", "TrustedLocalObservationSource",
    "record_observed_invocation", "verify_observed_invocation_integrity",
]
