"""Provider-authoritative outcome observation ingestion for governed changes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from krail.epistemic_history import DomainEventRef, OperationContext
from krail.provider.v1 import ResourceRef
from rail.context_brief import ProcessingVersion


OUTCOME_OBSERVATION_VERSION = "krail.outcome-observation.v1"
OUTCOME_PROCESSING_VERSION = "krail.outcome-ingestion.v1"

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
OutcomeState = Literal[
    "confirmed",
    "missing",
    "inaccessible",
    "redacted",
    "partial",
    "corrected",
    "retained",
    "erased",
]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OutcomeEvidenceLinks(StrictModel):
    context_brief_digest: Digest
    context_evidence_packet_id: NonEmpty
    verification_evidence_digest: Digest
    verification_evidence_packet_id: NonEmpty
    operation_receipt_ref: DomainEventRef | None = None
    operation_context: OperationContext | None = None

    @model_validator(mode="after")
    def _correlation_matches_receipt(self) -> "OutcomeEvidenceLinks":
        if self.operation_receipt_ref is None:
            return self
        expected = self.operation_context or OperationContext()
        for field in ("operation_id", "correlation_id", "causation_id"):
            receipt_value = getattr(self.operation_receipt_ref, field)
            context_value = getattr(expected, field)
            if receipt_value is not None and receipt_value != context_value:
                raise ValueError("operation receipt correlation must match the supplied stable correlation context")
        return self


class ProviderObservationInput(StrictModel):
    provider: NonEmpty
    resource_kind: Literal["commit", "pull-request", "review", "ci-run", "check-run"]
    observed_at: datetime
    state: OutcomeState
    resource_ref: ResourceRef | None = None
    provider_payload_digest: Digest | None = None
    bounded_summary: Summary | None = None
    supersedes_observation_digest: Digest | None = None
    retention_until: datetime | None = None
    erasure_reason_digest: Digest | None = None

    @field_validator("observed_at", "retention_until")
    @classmethod
    def _timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("outcome timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _state_shape_is_non_leaking(self) -> "ProviderObservationInput":
        hidden = self.state in {"missing", "inaccessible", "redacted", "erased"}
        if hidden and any((self.resource_ref, self.provider_payload_digest, self.bounded_summary)):
            raise ValueError("unavailable outcome states must not expose resource identity, payload, summary, or counts")
        if self.state in {"confirmed", "partial", "corrected", "retained"}:
            if self.resource_ref is None or self.provider_payload_digest is None:
                raise ValueError("available outcome observations require exact provider resource and payload digests")
        if self.state in {"corrected", "retained", "erased"} and self.supersedes_observation_digest is None:
            raise ValueError("lifecycle outcome observations require an explicit superseded observation digest")
        if self.state == "erased" and self.erasure_reason_digest is None:
            raise ValueError("erased outcome observations require only a reason digest")
        if self.state != "erased" and self.erasure_reason_digest is not None:
            raise ValueError("erasure reason digest is valid only for erased observations")
        return self


class OutcomeAssertionInput(StrictModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16_384)]
    source_ref: ResourceRef


class OutcomeAssertion(OutcomeAssertionInput):
    assertion_id: Digest
    processing_versions: tuple[ProcessingVersion, ...] = Field(min_length=1, max_length=16)


class OutcomeIngestRequest(StrictModel):
    observation: ProviderObservationInput
    links: OutcomeEvidenceLinks
    semantic_assertions: tuple[OutcomeAssertionInput, ...] = Field(max_length=64)
    processing_versions: tuple[ProcessingVersion, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _assertions_cite_only_observed_provider_resource(self) -> "OutcomeIngestRequest":
        ref = self.observation.resource_ref
        if ref is None and self.semantic_assertions:
            raise ValueError("unavailable observations cannot support semantic assertions")
        if ref is not None and any(item.source_ref.exact_key != ref.exact_key for item in self.semantic_assertions):
            raise ValueError("KRAIL outcome assertions must cite the exact provider-observed resource version")
        return self


class ObservationDrift(StrictModel):
    prior_observation_digest: Digest
    changed_fields: tuple[
        Literal["observed-at", "state", "resource-version", "resource-digest", "provider-payload"], ...
    ] = Field(
        min_length=1,
        max_length=4,
    )


class OutcomeObservation(StrictModel):
    schema_version: Literal["krail.outcome-observation.v1"] = OUTCOME_OBSERVATION_VERSION
    observation_digest: Digest
    authority: Literal["provider-observed"] = "provider-observed"
    provider: NonEmpty
    resource_kind: Literal["commit", "pull-request", "review", "ci-run", "check-run"]
    observed_at: datetime
    state: OutcomeState
    resource_ref: ResourceRef | None
    provider_payload_digest: Digest | None
    bounded_summary: Summary | None
    supersedes_observation_digest: Digest | None
    retention_until: datetime | None
    erasure_reason_digest: Digest | None
    links: OutcomeEvidenceLinks
    semantic_assertions: tuple[OutcomeAssertion, ...]
    processing_versions: tuple[ProcessingVersion, ...]
    drift: ObservationDrift | None = None
    domain_event_ref: DomainEventRef


class OutcomeIngestEnvelope(StrictModel):
    """Complete deterministic input for one outcome-ingestion operation.

    The exact prior observation is part of the operation input rather than an
    out-of-band argument so every public transport validates and digests the
    same supersession state.
    """

    request: OutcomeIngestRequest
    prior_observation: OutcomeObservation | None = None


class OutcomeObservationService:
    """Pure ingestion that preserves provider state and never fetches newer state."""

    def ingest(
        self,
        request: OutcomeIngestRequest,
        *,
        previous: OutcomeObservation | None = None,
    ) -> OutcomeObservation:
        supplied_parent = request.observation.supersedes_observation_digest
        if previous is None and supplied_parent is not None:
            raise ValueError("supersession requires the exact prior observation")
        if previous is not None and supplied_parent != previous.observation_digest:
            raise ValueError("new provider state must explicitly supersede the pinned prior observation")
        if previous is not None and request.observation.observed_at < previous.observed_at:
            raise ValueError("a superseding provider observation cannot predate the pinned observation")
        if previous is not None and (
            request.observation.provider != previous.provider
            or request.observation.resource_kind != previous.resource_kind
        ):
            raise ValueError("supersession cannot change provider authority or resource kind")
        if (
            previous is not None
            and previous.resource_ref is not None
            and request.observation.resource_ref is not None
            and previous.resource_ref.identity != request.observation.resource_ref.identity
        ):
            raise ValueError("supersession cannot replace the pinned provider resource identity")

        drift = self._drift(previous, request.observation) if previous else None
        assertions = tuple(
            OutcomeAssertion(
                **item.model_dump(mode="python"),
                assertion_id=_digest(
                    {
                        **item.model_dump(mode="json"),
                        "processing_versions": [value.model_dump(mode="json") for value in request.processing_versions],
                    }
                ),
                processing_versions=request.processing_versions,
            )
            for item in request.semantic_assertions
        )
        body = {
            "schema_version": OUTCOME_OBSERVATION_VERSION,
            "authority": "provider-observed",
            **request.observation.model_dump(mode="json"),
            "links": request.links.model_dump(mode="json"),
            "semantic_assertions": [item.model_dump(mode="json") for item in assertions],
            "processing_versions": [item.model_dump(mode="json") for item in request.processing_versions],
            "drift": drift.model_dump(mode="json") if drift else None,
        }
        observation_digest = _digest(body)
        event_ref = DomainEventRef.for_digest(
            event_type="krail.provider-outcome-observed.v1",
            digest=observation_digest,
            context=request.links.operation_context,
        )
        return OutcomeObservation(
            observation_digest=observation_digest,
            authority="provider-observed",
            **request.observation.model_dump(mode="python"),
            links=request.links,
            semantic_assertions=assertions,
            processing_versions=request.processing_versions,
            drift=drift,
            domain_event_ref=event_ref,
        )

    @staticmethod
    def _drift(previous: OutcomeObservation, current: ProviderObservationInput) -> ObservationDrift:
        fields: list[
            Literal["observed-at", "state", "resource-version", "resource-digest", "provider-payload"]
        ] = []
        if current.observed_at != previous.observed_at:
            fields.append("observed-at")
        if current.state != previous.state:
            fields.append("state")
        if current.resource_ref and previous.resource_ref:
            if current.resource_ref.version != previous.resource_ref.version:
                fields.append("resource-version")
            if current.resource_ref.digest != previous.resource_ref.digest:
                fields.append("resource-digest")
        elif current.resource_ref != previous.resource_ref:
            fields.append("resource-version")
        if current.provider_payload_digest != previous.provider_payload_digest:
            fields.append("provider-payload")
        if not fields:
            raise ValueError("a superseding observation must explicitly change observed provider state or observation time")
        return ObservationDrift(prior_observation_digest=previous.observation_digest, changed_fields=tuple(fields))


__all__ = [
    "OUTCOME_OBSERVATION_VERSION",
    "OUTCOME_PROCESSING_VERSION",
    "ObservationDrift",
    "OutcomeAssertion",
    "OutcomeAssertionInput",
    "OutcomeEvidenceLinks",
    "OutcomeIngestEnvelope",
    "OutcomeIngestRequest",
    "OutcomeObservation",
    "OutcomeObservationService",
    "OutcomeState",
    "ProviderObservationInput",
]
