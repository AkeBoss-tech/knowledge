"""Deterministic verification evidence assembly over supplied, bounded artifacts.

This module interprets observations supplied by a caller.  It never runs a
command, reads a repository, or mutates an external system.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from krail.epistemic_history import DomainEventRef, OperationContext
from krail.provider.v1 import MAX_EVIDENCE_CONTENT_BYTES, EvidenceItem, EvidencePacket, ResourceRef
from rail.artifact_references import ArtifactReference
from rail.context_brief import ContextBrief, ProcessingVersion


VERIFICATION_EVIDENCE_VERSION = "krail.verification-evidence.v1"
VERIFICATION_PROCESSING_VERSION = "krail.verification-assembly.v1"
MAX_CHANGED_FILES = 256
MAX_CHECKS = 64
MAX_CLAIMS = 32
MAX_GAPS = 64
MAX_CONFLICTS = 64

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]

UNAVAILABLE_CHECK_DISCLOSURES = {
    "missing": "Check evidence was not supplied.",
    "inaccessible": "Check evidence is inaccessible in the supplied authorization context.",
    "redacted": "Check evidence is redacted in the supplied authorization context.",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextBriefLink(StrictModel):
    brief_digest: Digest
    evidence_packet_id: NonEmpty
    repository: ResourceRef
    issue: ResourceRef
    source_refs: tuple[ResourceRef, ...] = Field(min_length=2, max_length=32)
    processing_versions: tuple[ProcessingVersion, ...] = Field(min_length=1, max_length=16)

    @classmethod
    def from_brief(cls, brief: ContextBrief) -> "ContextBriefLink":
        refs: list[ResourceRef] = []
        for ref in (brief.repository, brief.issue, *(item.source for item in brief.evidence.items)):
            if ref.exact_key not in {item.exact_key for item in refs}:
                refs.append(ref)
        return cls(
            brief_digest=brief.brief_digest,
            evidence_packet_id=brief.evidence.packet_id,
            repository=brief.repository,
            issue=brief.issue,
            source_refs=tuple(refs),
            processing_versions=brief.processing_versions,
        )

    @model_validator(mode="after")
    def _contains_exact_origins(self) -> "ContextBriefLink":
        keys = {item.exact_key for item in self.source_refs}
        if len(keys) != len(self.source_refs):
            raise ValueError("Context Brief source references must be unique exact versions")
        if self.repository.exact_key not in keys or self.issue.exact_key not in keys:
            raise ValueError("Context Brief link must retain its exact repository and issue source versions")
        return self


class VersionedMetadata(StrictModel):
    component: NonEmpty
    version: NonEmpty
    digest: Digest


class CommandDescriptor(StrictModel):
    """A command description, not an instruction for KRAIL to execute it."""

    program: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    arguments: tuple[Annotated[str, StringConstraints(max_length=2048)], ...] = Field(max_length=128)
    working_directory_ref: ResourceRef | None = None
    descriptor_digest: Digest

    @model_validator(mode="after")
    def _digest_matches(self) -> "CommandDescriptor":
        body = {
            "program": self.program,
            "arguments": list(self.arguments),
            "working_directory_ref": (
                self.working_directory_ref.model_dump(mode="json") if self.working_directory_ref else None
            ),
        }
        if self.descriptor_digest != _digest(body):
            raise ValueError("command descriptor digest does not match its canonical fields")
        return self

    @classmethod
    def describe(
        cls,
        program: str,
        arguments: tuple[str, ...] = (),
        *,
        working_directory_ref: ResourceRef | None = None,
    ) -> "CommandDescriptor":
        body = {
            "program": program.strip(),
            "arguments": list(arguments),
            "working_directory_ref": working_directory_ref.model_dump(mode="json") if working_directory_ref else None,
        }
        return cls(
            program=program,
            arguments=arguments,
            working_directory_ref=working_directory_ref,
            descriptor_digest=_digest(body),
        )


class CheckResult(StrictModel):
    check_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]*$", max_length=128)]
    kind: Literal["test", "lint", "typecheck", "build", "security", "other"]
    command: CommandDescriptor
    status: Literal["passed", "failed", "partial", "missing", "inaccessible", "redacted"]
    summary: Summary
    artifact_refs: tuple[ArtifactReference, ...] = Field(max_length=16)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def _timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("check timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _bounded_availability(self) -> "CheckResult":
        if self.completed_at and self.started_at and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.status in {"missing", "inaccessible", "redacted"} and self.artifact_refs:
            raise ValueError("unavailable checks must not leak artifact identities or counts")
        if self.status in UNAVAILABLE_CHECK_DISCLOSURES and self.summary != UNAVAILABLE_CHECK_DISCLOSURES[self.status]:
            raise ValueError("unavailable checks require their constant non-leaking disclosure")
        if self.status in {"passed", "failed", "partial"} and not self.artifact_refs:
            raise ValueError("observed checks require at least one exact artifact reference")
        if any(item.role not in {"test-result", "check-result"} for item in self.artifact_refs):
            raise ValueError("check results may cite only test-result or check-result artifacts")
        return self


class VerificationClaimInput(StrictModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16_384)]
    source_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=32)
    file_refs: tuple[ResourceRef, ...] = Field(max_length=MAX_CHANGED_FILES)
    check_ids: tuple[NonEmpty, ...] = Field(min_length=1, max_length=MAX_CHECKS)


class VerificationClaim(VerificationClaimInput):
    claim_id: Digest
    context_brief_digest: Digest
    diff_ref: ResourceRef
    check_artifact_refs: tuple[ResourceRef, ...] = Field(max_length=256)
    tool_versions: tuple[VersionedMetadata, ...] = Field(min_length=1, max_length=32)
    environment_versions: tuple[VersionedMetadata, ...] = Field(min_length=1, max_length=32)
    processing_versions: tuple[ProcessingVersion, ...] = Field(min_length=1, max_length=16)


class VerificationGap(StrictModel):
    state: Literal["missing", "inaccessible", "redacted", "partial"]
    code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$", max_length=128)]
    disclosure: Summary


class VerificationConflict(StrictModel):
    left: ResourceRef
    right: ResourceRef
    basis: Summary


class VerificationEvidenceRequest(StrictModel):
    context_brief: ContextBriefLink
    assembled_at: datetime
    diff: ArtifactReference
    changed_files: tuple[ResourceRef, ...] = Field(max_length=MAX_CHANGED_FILES)
    checks: tuple[CheckResult, ...] = Field(min_length=1, max_length=MAX_CHECKS)
    tool_versions: tuple[VersionedMetadata, ...] = Field(min_length=1, max_length=32)
    environment_versions: tuple[VersionedMetadata, ...] = Field(min_length=1, max_length=32)
    processing_versions: tuple[ProcessingVersion, ...] = Field(min_length=1, max_length=16)
    claims: tuple[VerificationClaimInput, ...] = Field(min_length=1, max_length=MAX_CLAIMS)
    gaps: tuple[VerificationGap, ...] = Field(max_length=MAX_GAPS)
    conflicts: tuple[VerificationConflict, ...] = Field(max_length=MAX_CONFLICTS)
    operation_context: OperationContext | None = None

    @field_validator("assembled_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("assembled_at must include a timezone")
        return value

    @model_validator(mode="after")
    def _exact_references_are_consistent(self) -> "VerificationEvidenceRequest":
        if self.diff.role not in {"diff", "patch"}:
            raise ValueError("verification diff must use the diff or patch artifact role")
        if len({item.exact_key for item in self.changed_files}) != len(self.changed_files):
            raise ValueError("changed file references must be unique exact versions")
        if len({item.check_id for item in self.checks}) != len(self.checks):
            raise ValueError("check identifiers must be unique")
        available_checks = {item.check_id for item in self.checks}
        exact_sources = {item.exact_key for item in self.context_brief.source_refs}
        exact_files = {item.exact_key for item in self.changed_files}
        for claim in self.claims:
            if not set(claim.check_ids).issubset(available_checks):
                raise ValueError("verification claims may cite only declared checks")
            if not {item.exact_key for item in claim.source_refs}.issubset(exact_sources):
                raise ValueError("verification claims may cite only exact Context Brief sources")
            if not {item.exact_key for item in claim.file_refs}.issubset(exact_files):
                raise ValueError("verification claims may cite only declared changed-file versions")
        if sum(len(item.text.encode("utf-8")) for item in self.claims) > MAX_EVIDENCE_CONTENT_BYTES:
            raise ValueError("combined verification claim text exceeds the bounded EvidencePacket content limit")
        return self


class VerificationEvidence(StrictModel):
    schema_version: Literal["krail.verification-evidence.v1"] = VERIFICATION_EVIDENCE_VERSION
    evidence_digest: Digest
    context_brief: ContextBriefLink
    assembled_at: datetime
    diff: ArtifactReference
    changed_files: tuple[ResourceRef, ...]
    checks: tuple[CheckResult, ...]
    tool_versions: tuple[VersionedMetadata, ...]
    environment_versions: tuple[VersionedMetadata, ...]
    processing_versions: tuple[ProcessingVersion, ...]
    claims: tuple[VerificationClaim, ...]
    gaps: tuple[VerificationGap, ...]
    conflicts: tuple[VerificationConflict, ...]
    evidence_packet: EvidencePacket
    domain_event_ref: DomainEventRef


class VerificationEvidenceService:
    """Pure assembly of supplied observations; it intentionally has no executor."""

    def assemble(self, request: VerificationEvidenceRequest) -> VerificationEvidence:
        checks_by_id = {item.check_id: item for item in request.checks}
        claims: list[VerificationClaim] = []
        for value in request.claims:
            check_artifact_refs = tuple(
                artifact.ref
                for check_id in value.check_ids
                for artifact in checks_by_id[check_id].artifact_refs
            )
            identity = {
                **value.model_dump(mode="json"),
                "context_brief_digest": request.context_brief.brief_digest,
                "diff_ref": request.diff.ref.model_dump(mode="json"),
                "check_artifacts": [item.model_dump(mode="json") for item in check_artifact_refs],
                "tool_versions": [item.model_dump(mode="json") for item in request.tool_versions],
                "environment_versions": [item.model_dump(mode="json") for item in request.environment_versions],
                "processing_versions": [item.model_dump(mode="json") for item in request.processing_versions],
            }
            claims.append(
                VerificationClaim(
                    **value.model_dump(mode="python"),
                    claim_id=_digest(identity),
                    context_brief_digest=request.context_brief.brief_digest,
                    diff_ref=request.diff.ref,
                    check_artifact_refs=check_artifact_refs,
                    tool_versions=request.tool_versions,
                    environment_versions=request.environment_versions,
                    processing_versions=request.processing_versions,
                )
            )

        packet_items = [
            EvidenceItem(
                source=claim.source_refs[0],
                locator=f"verification-claim:{claim.claim_id}",
                excerpt=claim.text,
                media_type="text/plain",
            )
            for claim in claims
        ]
        packet_body = {
            "context_brief_digest": request.context_brief.brief_digest,
            "diff_digest": request.diff.ref.digest,
            "claims": [item.model_dump(mode="json") for item in claims],
            "checks": [item.model_dump(mode="json") for item in request.checks],
        }
        packet = EvidencePacket(
            packet_id="packet:verification:" + _digest(packet_body).removeprefix("sha256:")[:24],
            query=f"verification for {request.context_brief.brief_digest}",
            generated_at=request.assembled_at,
            items=packet_items,
            truncated=bool(request.gaps),
        )
        body = {
            "schema_version": VERIFICATION_EVIDENCE_VERSION,
            **request.model_dump(mode="json"),
            "claims": [item.model_dump(mode="json") for item in claims],
            "evidence_packet": packet.model_dump(mode="json"),
        }
        evidence_digest = _digest(body)
        event_ref = DomainEventRef.for_digest(
            event_type="krail.verification-evidence-assembled.v1",
            digest=evidence_digest,
            context=request.operation_context,
        )
        request_values = request.model_dump(mode="python")
        request_values.pop("operation_context")
        request_values.pop("claims")
        return VerificationEvidence(
            **request_values,
            evidence_digest=evidence_digest,
            claims=tuple(claims),
            evidence_packet=packet,
            domain_event_ref=event_ref,
        )


__all__ = [
    "VERIFICATION_EVIDENCE_VERSION",
    "VERIFICATION_PROCESSING_VERSION",
    "CheckResult",
    "CommandDescriptor",
    "ContextBriefLink",
    "VerificationClaim",
    "VerificationClaimInput",
    "VerificationConflict",
    "VerificationEvidence",
    "VerificationEvidenceRequest",
    "VerificationEvidenceService",
    "VerificationGap",
    "VersionedMetadata",
]
