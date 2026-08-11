"""Bounded, digest-addressed references to verification and outcome artifacts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from krail.provider.v1 import ResourceRef


ARTIFACT_REFERENCE_VERSION = "krail.artifact-reference.v1"
ArtifactRole = Literal[
    "diff",
    "patch",
    "changed-file",
    "test-result",
    "check-result",
    "tool-metadata",
    "environment-metadata",
    "provider-observation",
]
ArtifactState = Literal["complete", "partial", "corrected", "retained", "erased"]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
Digest = Annotated[str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")]


class ArtifactReference(BaseModel):
    """Reference-only artifact projection; raw patches and logs never live here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["krail.artifact-reference.v1"] = ARTIFACT_REFERENCE_VERSION
    role: ArtifactRole
    ref: ResourceRef
    state: ArtifactState = "complete"
    media_type: NonEmpty
    byte_length: int | None = Field(default=None, ge=0, le=1_000_000_000_000)
    bounded_summary: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4096)] | None = None
    partial_content_digest: Digest | None = None

    @model_validator(mode="after")
    def _partial_state_is_explicit(self) -> "ArtifactReference":
        if (self.state == "partial") != (self.partial_content_digest is not None):
            raise ValueError("partial artifacts require a partial_content_digest, and complete artifacts forbid it")
        if self.state == "erased" and (self.bounded_summary is not None or self.byte_length is not None):
            raise ValueError("erased artifact references retain only exact identity and digest")
        return self


__all__ = [
    "ARTIFACT_REFERENCE_VERSION",
    "ArtifactReference",
    "ArtifactRole",
    "ArtifactState",
]
