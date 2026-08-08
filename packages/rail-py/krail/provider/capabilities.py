"""Implementation-independent contracts for immutable capability publication."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


CAPABILITY_DESCRIPTOR_SCHEMA = "krail.capability-descriptor.v1"
CAPABILITY_NEGOTIATION_SCHEMA = "krail.capability-negotiation.v1"

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
SemVer = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"),
]
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]


def _canonical(value: Any) -> bytes:
    def _model(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        raise TypeError(f"unsupported descriptor value: {type(value).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_model,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _major(version: str) -> int:
    return int(version.split(".", 1)[0])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityOperation(StrictModel):
    operation_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=128)]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class EffectDeclaration(StrictModel):
    classification: Literal["read-only"] = "read-only"
    external_effects: Literal[False] = False


class CapabilityLimit(StrictModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=128)]
    value: int = Field(ge=1)
    unit: Literal["items", "utf8-bytes", "entries"]


class SemanticProcessingVersion(StrictModel):
    component: NonEmpty
    version: NonEmpty


class CompatibilityDeclaration(StrictModel):
    consumer_version_range: Literal[">=1.0.0,<2.0.0"] = ">=1.0.0,<2.0.0"
    negotiation: Literal["same-major-and-optional-digest-pin"] = "same-major-and-optional-digest-pin"


class AuthorizationDeclaration(StrictModel):
    descriptor_grants_authorization: Literal[False] = False
    note: Literal[
        "Descriptor availability does not grant authorization; registration, policy, approval, credentials, and execution are external concerns."
    ] = "Descriptor availability does not grant authorization; registration, policy, approval, credentials, and execution are external concerns."


class CapabilityDescriptor(StrictModel):
    schema_version: Literal["krail.capability-descriptor.v1"] = CAPABILITY_DESCRIPTOR_SCHEMA
    provider: NonEmpty
    capability_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9.-]*$", max_length=256)]
    semantic_version: SemVer
    descriptor_digest: Digest
    operations: tuple[CapabilityOperation, ...] = Field(min_length=1, max_length=16)
    effects: EffectDeclaration = Field(default_factory=EffectDeclaration)
    limits: tuple[CapabilityLimit, ...] = Field(min_length=1, max_length=32)
    semantic_processing_versions: tuple[SemanticProcessingVersion, ...] = Field(min_length=1, max_length=32)
    compatibility: CompatibilityDeclaration = Field(default_factory=CompatibilityDeclaration)
    authorization: AuthorizationDeclaration = Field(default_factory=AuthorizationDeclaration)

    @classmethod
    def issue(cls, **values: Any) -> "CapabilityDescriptor":
        body = {**values, "schema_version": CAPABILITY_DESCRIPTOR_SCHEMA}
        body.setdefault("effects", EffectDeclaration())
        body.setdefault("compatibility", CompatibilityDeclaration())
        body.setdefault("authorization", AuthorizationDeclaration())
        body["descriptor_digest"] = _digest(body)
        return cls.model_validate(body)

    @model_validator(mode="after")
    def _verify_digest_and_uniqueness(self) -> "CapabilityDescriptor":
        body = self.model_dump(mode="json", exclude={"descriptor_digest"})
        if self.descriptor_digest != _digest(body):
            raise ValueError("descriptor digest does not match its immutable content")
        operation_ids = [item.operation_id for item in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("capability operation ids must be unique")
        limit_names = [item.name for item in self.limits]
        if len(limit_names) != len(set(limit_names)):
            raise ValueError("capability limit names must be unique")
        return self


class CapabilityNegotiationRequest(StrictModel):
    schema_version: Literal["krail.capability-negotiation.v1"] = CAPABILITY_NEGOTIATION_SCHEMA
    capability_id: NonEmpty
    consumer_version: SemVer
    descriptor_digest: Digest | None = None


class CapabilityNegotiationResult(StrictModel):
    schema_version: Literal["krail.capability-negotiation.v1"] = CAPABILITY_NEGOTIATION_SCHEMA
    compatible: bool
    descriptor: CapabilityDescriptor | None = None
    diagnostic: NonEmpty


def negotiate(
    descriptor: CapabilityDescriptor,
    request: CapabilityNegotiationRequest,
) -> CapabilityNegotiationResult:
    if request.capability_id != descriptor.capability_id:
        return CapabilityNegotiationResult(compatible=False, diagnostic="capability id is not published by this provider")
    if _major(request.consumer_version) != _major(descriptor.semantic_version):
        return CapabilityNegotiationResult(compatible=False, descriptor=descriptor, diagnostic="capability semantic-version major is incompatible")
    if request.descriptor_digest and request.descriptor_digest != descriptor.descriptor_digest:
        return CapabilityNegotiationResult(compatible=False, descriptor=descriptor, diagnostic="capability descriptor digest pin does not match")
    return CapabilityNegotiationResult(compatible=True, descriptor=descriptor, diagnostic="capability version and descriptor digest are compatible")


__all__ = [
    "CAPABILITY_DESCRIPTOR_SCHEMA",
    "CAPABILITY_NEGOTIATION_SCHEMA",
    "AuthorizationDeclaration",
    "CapabilityDescriptor",
    "CapabilityLimit",
    "CapabilityNegotiationRequest",
    "CapabilityNegotiationResult",
    "CapabilityOperation",
    "CompatibilityDeclaration",
    "EffectDeclaration",
    "SemanticProcessingVersion",
    "negotiate",
]
