"""Versioned, signed semantic packs and deterministic quality evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

from rail.semantic.models import (
    Digest,
    EvidenceProvenance,
    NonEmpty,
    ScopedModel,
    StrictModel,
    Token,
    canonical_digest,
)
from rail.semantic.repository import SemanticRepository


class PackMapping(StrictModel):
    mapping_id: NonEmpty
    source_kind: Literal["repository", "issue", "change", "pull-request", "ci-check"]
    source_path: NonEmpty
    target_type_id: Token
    target_field: Token
    transform: Literal["identity", "normalize-text", "parse-timestamp", "resource-ref"]


PackTrustStatus = Literal[
    "trusted", "untrusted", "revoked", "expired", "unsupported"
]


class PackSignatureEnvelope(StrictModel):
    """Unverified signature material supplied with a semantic pack."""

    issuer: NonEmpty
    key_id: NonEmpty
    algorithm: Token
    signed_digest: Digest
    signature: NonEmpty
    signed_at: datetime

    @field_validator("signed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("signature timestamp must include a timezone")
        return value


PackSignature = PackSignatureEnvelope


class PackSignatureVerificationRequest(ScopedModel):
    schema_version: Literal["krail.semantic-pack-signature-request.v1"] = (
        "krail.semantic-pack-signature-request.v1"
    )
    pack_id: Token
    pack_version: NonEmpty
    content_digest: Digest
    envelope: PackSignatureEnvelope
    admitted_at: datetime
    request_digest: Digest

    @field_validator("admitted_at")
    @classmethod
    def admission_time_requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("signature admission timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def request_digest_matches(self) -> PackSignatureVerificationRequest:
        calculated = canonical_digest(
            self.model_dump(mode="json", exclude={"request_digest"})
        )
        if self.request_digest != calculated:
            raise ValueError("signature verification request digest does not match")
        if self.envelope.signed_digest != self.content_digest:
            raise ValueError("signature envelope does not bind pack content")
        return self


class PackSignatureVerification(StrictModel):
    """Audit-safe result from an injected trust and cryptographic verifier."""

    status: PackTrustStatus
    request_digest: Digest
    issuer: NonEmpty
    key_id: NonEmpty
    algorithm: Token
    verifier_id: NonEmpty
    verifier_version: NonEmpty
    verifier_digest: Digest
    trust_policy_digest: Digest
    reason_code: Token
    verified_at: datetime
    valid_until: datetime | None = None

    @field_validator("verified_at", "valid_until")
    @classmethod
    def verification_time_requires_timezone(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("signature verification timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validity_is_ordered(self) -> PackSignatureVerification:
        if self.valid_until is not None and self.valid_until <= self.verified_at:
            raise ValueError("signature verification validity must end after verification")
        return self


class PackSignatureVerifier(Protocol):
    def verify(
        self, request: PackSignatureVerificationRequest
    ) -> PackSignatureVerification: ...


class PackSignatureAdmissionError(ValueError):
    """Typed, audit-safe denial before a semantic pack reaches persistence."""

    def __init__(self, status: PackTrustStatus, reason_code: str) -> None:
        self.status = status
        self.reason_code = reason_code
        super().__init__(f"semantic pack signature {status}: {reason_code}")


class SemanticPack(ScopedModel):
    schema_version: Literal["krail.semantic-pack.v1"] = "krail.semantic-pack.v1"
    pack_id: Token
    version: NonEmpty
    software_vertical: Literal[True] = True
    type_ids: tuple[Token, ...] = Field(min_length=1, max_length=256)
    mappings: tuple[PackMapping, ...] = Field(max_length=512)
    compatible_from: NonEmpty | None = None
    migration_proposal_ids: tuple[NonEmpty, ...] = ()
    provenance: EvidenceProvenance
    content_digest: Digest
    signature: PackSignatureEnvelope
    signature_verification: PackSignatureVerification | None = None
    revision: int = Field(ge=1)

    def calculated_digest(self) -> str:
        return canonical_digest(
            self.model_dump(
                mode="json",
                exclude={"content_digest", "signature", "signature_verification"},
            )
        )

    @model_validator(mode="after")
    def digest_and_signature_bind_content(self) -> SemanticPack:
        if self.content_digest != self.calculated_digest():
            raise ValueError("semantic pack digest does not match content")
        if self.signature.signed_digest != self.content_digest:
            raise ValueError("semantic pack signature must bind its content digest")
        if self.signature_verification is not None:
            verification = self.signature_verification
            if verification.status != "trusted":
                raise ValueError("persisted semantic packs require trusted verification")
            if (
                verification.issuer != self.signature.issuer
                or verification.key_id != self.signature.key_id
                or verification.algorithm != self.signature.algorithm
            ):
                raise ValueError("signature verification identity does not match envelope")
        if len(self.type_ids) != len(set(self.type_ids)):
            raise ValueError("semantic pack type IDs must be unique")
        if len({item.mapping_id for item in self.mappings}) != len(self.mappings):
            raise ValueError("semantic pack mapping IDs must be unique")
        return self


class QualityMetric(StrictModel):
    metric: Literal[
        "mapping-coverage",
        "evidence-coverage",
        "conflict-rate",
        "unresolved-alias-rate",
    ]
    value: float = Field(ge=0, le=1)
    sample_size: int = Field(ge=0)


class PackEvaluation(ScopedModel):
    schema_version: Literal["krail.semantic-pack-evaluation.v1"] = (
        "krail.semantic-pack-evaluation.v1"
    )
    evaluation_id: NonEmpty
    pack_id: Token
    pack_version: NonEmpty
    pack_digest: Digest
    metrics: tuple[QualityMetric, ...] = Field(min_length=1)
    baseline_evaluation_id: NonEmpty | None = None
    drift_codes: tuple[
        Literal["coverage-regression", "conflict-regression", "alias-regression"], ...
    ] = ()
    evaluated_at: datetime
    processing_version: NonEmpty
    processing_digest: Digest
    evaluation_digest: Digest
    revision: int = Field(ge=1)

    @field_validator("evaluated_at")
    @classmethod
    def evaluation_time_requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluation timestamp must include a timezone")
        return value

    def calculated_digest(self) -> str:
        return canonical_digest(
            self.model_dump(mode="json", exclude={"evaluation_digest"})
        )

    @model_validator(mode="after")
    def evaluation_digest_matches(self) -> PackEvaluation:
        if len({item.metric for item in self.metrics}) != len(self.metrics):
            raise ValueError("pack evaluation metrics must be unique")
        if self.evaluation_digest != self.calculated_digest():
            raise ValueError("pack evaluation digest does not match content")
        return self


class SemanticPackService:
    def __init__(
        self, repository: SemanticRepository, verifier: PackSignatureVerifier
    ) -> None:
        self.repository = repository
        self.verifier = verifier

    @staticmethod
    def verification_request(
        pack: SemanticPack, *, admitted_at: datetime
    ) -> PackSignatureVerificationRequest:
        values = {
            "tenant_id": pack.tenant_id,
            "project_id": pack.project_id,
            "pack_id": pack.pack_id,
            "pack_version": pack.version,
            "content_digest": pack.content_digest,
            "envelope": pack.signature,
            "admitted_at": admitted_at,
        }
        return PackSignatureVerificationRequest(
            **values, request_digest=canonical_digest(
                PackSignatureVerificationRequest.model_construct(
                    **values, request_digest="sha256:" + "0" * 64
                ).model_dump(mode="json", exclude={"request_digest"})
            )
        )

    def publish(
        self, pack: SemanticPack, *, expected_revision: int, published_at: datetime
    ) -> SemanticPack:
        if expected_revision != 0 or pack.revision != 1:
            raise ValueError("semantic pack versions are immutable")
        request = self.verification_request(pack, admitted_at=published_at)
        verification = self.verifier.verify(request)
        if verification.request_digest != request.request_digest:
            raise PackSignatureAdmissionError("untrusted", "verification-request-mismatch")
        if (
            verification.issuer != pack.signature.issuer
            or verification.key_id != pack.signature.key_id
            or verification.algorithm != pack.signature.algorithm
        ):
            raise PackSignatureAdmissionError("untrusted", "verification-identity-mismatch")
        if verification.status != "trusted":
            raise PackSignatureAdmissionError(
                verification.status, verification.reason_code
            )
        if verification.verified_at > published_at:
            raise PackSignatureAdmissionError("untrusted", "verification-from-future")
        if (
            verification.valid_until is not None
            and published_at >= verification.valid_until
        ):
            raise PackSignatureAdmissionError("expired", "verification-expired")
        admitted_pack = SemanticPack.model_validate(
            {
                **pack.model_dump(mode="python"),
                "signature_verification": verification,
            }
        )
        return self.repository._save(
            "semantic_pack",
            f"{pack.pack_id}@{pack.version}",
            admitted_pack,
            expected_revision=expected_revision,
            at=published_at,
        )

    @staticmethod
    def build_evaluation(
        *,
        evaluation_id: str,
        pack: SemanticPack,
        metrics: tuple[QualityMetric, ...],
        evaluated_at: datetime,
        processing_version: str,
        processing_digest: str,
        baseline: PackEvaluation | None = None,
        regression_threshold: float = 0.05,
    ) -> PackEvaluation:
        current = {metric.metric: metric.value for metric in metrics}
        prior = (
            {metric.metric: metric.value for metric in baseline.metrics}
            if baseline
            else {}
        )
        drift: list[str] = []
        if baseline:
            if current.get("mapping-coverage", 0) + regression_threshold < prior.get(
                "mapping-coverage", 0
            ):
                drift.append("coverage-regression")
            if (
                current.get("conflict-rate", 0)
                > prior.get("conflict-rate", 0) + regression_threshold
            ):
                drift.append("conflict-regression")
            if (
                current.get("unresolved-alias-rate", 0)
                > prior.get("unresolved-alias-rate", 0) + regression_threshold
            ):
                drift.append("alias-regression")
        values = {
            "tenant_id": pack.tenant_id,
            "project_id": pack.project_id,
            "evaluation_id": evaluation_id,
            "pack_id": pack.pack_id,
            "pack_version": pack.version,
            "pack_digest": pack.content_digest,
            "metrics": metrics,
            "baseline_evaluation_id": baseline.evaluation_id if baseline else None,
            "drift_codes": tuple(drift),
            "evaluated_at": evaluated_at,
            "processing_version": processing_version,
            "processing_digest": processing_digest,
            "revision": 1,
        }
        digest = canonical_digest(
            PackEvaluation.model_construct(
                **values, evaluation_digest="sha256:" + "0" * 64
            ).model_dump(mode="json", exclude={"evaluation_digest"})
        )
        return PackEvaluation(**values, evaluation_digest=digest)

    def record_evaluation(
        self,
        evaluation: PackEvaluation,
        *,
        expected_revision: int,
    ) -> PackEvaluation:
        return self.repository._save(
            "pack_evaluation",
            evaluation.evaluation_id,
            evaluation,
            expected_revision=expected_revision,
            at=evaluation.evaluated_at,
        )
