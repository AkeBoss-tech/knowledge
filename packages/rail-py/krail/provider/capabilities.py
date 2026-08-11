"""Implementation-independent contracts for immutable capability publication."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


CAPABILITY_DESCRIPTOR_SCHEMA = "krail.capability-descriptor.v1"
CAPABILITY_NEGOTIATION_SCHEMA = "krail.capability-negotiation.v1"
SEMVER_PATTERN = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
SemVer = Annotated[
    str,
    StringConstraints(pattern=SEMVER_PATTERN),
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


def _semver_parts(version: str) -> tuple[tuple[int, int, int], tuple[str, ...] | None]:
    match = re.fullmatch(SEMVER_PATTERN, version)
    if match is None:  # Pydantic validates public inputs; keep this helper total.
        raise ValueError(f"invalid semantic version: {version}")
    core = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else None
    if prerelease and any(item.isdigit() and len(item) > 1 and item.startswith("0") for item in prerelease):
        raise ValueError("numeric semantic-version prerelease identifiers may not contain leading zeroes")
    return core, prerelease


def _compare_semver(left: str, right: str) -> int:
    left_core, left_pre = _semver_parts(left)
    right_core, right_pre = _semver_parts(right)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_item, right_item in zip(left_pre, right_pre):
        if left_item == right_item:
            continue
        left_numeric = left_item.isdigit()
        right_numeric = right_item.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_item) < int(right_item) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_item < right_item else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1


def _in_supported_range(version: str) -> bool:
    return _compare_semver(version, "1.0.0") >= 0 and _compare_semver(version, "2.0.0") < 0


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
    negotiation: Literal["advertised-semver-range-and-optional-digest-pin"] = "advertised-semver-range-and-optional-digest-pin"


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

    @field_validator("semantic_version")
    @classmethod
    def _valid_semantic_version(cls, value: str) -> str:
        _semver_parts(value)
        return value

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

    @field_validator("consumer_version")
    @classmethod
    def _valid_consumer_version(cls, value: str) -> str:
        _semver_parts(value)
        return value


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
    if not _in_supported_range(request.consumer_version):
        return CapabilityNegotiationResult(compatible=False, descriptor=descriptor, diagnostic="consumer version is outside the advertised compatibility range")
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
