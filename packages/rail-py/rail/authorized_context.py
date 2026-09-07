"""Authorized context packet composition over the existing KRAIL authority.

This module is intentionally a thin read boundary.  OpenSaddle (or another
caller) owns identity, grants, revocation, and operational records; KRAIL only
accepts a live exact-resource authorizer and returns its existing ContextBrief.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from krail.provider.v1 import ResourceRef
from rail.context_brief import ContextBrief, ContextBriefRequest, ContextBriefService
from rail.hosted.access import AccessContextAuthority, SignedAccessContext
from rail.temporal_records import TemporalRecord


Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
PROCEDURE_REVIEW_CAPABILITY_ID = "krail.procedure-review"
PROCEDURE_REVIEW_CAPABILITY_VERSION = "1.0.0"
PROCEDURE_INVALIDATION_CAPABILITY_ID = "krail.procedure-invalidation"
PROCEDURE_INVALIDATION_CAPABILITY_VERSION = "1.0.0"
PROCEDURE_PROJECTION_CAPABILITY_ID = "krail.procedure-projection"
PROCEDURE_PROJECTION_CAPABILITY_VERSION = "1.0.0"


def _digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


class AuthorizedContextAuthorizer(Protocol):
    """Caller-owned live decision for one exact versioned resource."""

    authorization_digest: str

    def authorize(self, ref: ResourceRef, *, at: datetime | None = None) -> None:
        """Raise PermissionError when access is denied or revoked."""


class AuthorizedContextPacket(BaseModel):
    """Portable bounded packet bound to both knowledge and authorization state."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["krail.authorized-context-packet.v1"] = (
        "krail.authorized-context-packet.v1"
    )
    packet_digest: Digest
    authorization_digest: Digest
    context: ContextBrief

    @model_validator(mode="after")
    def _packet_digest_matches(self) -> "AuthorizedContextPacket":
        expected = _digest(
            {
                "schema_version": self.schema_version,
                "authorization_digest": self.authorization_digest,
                "context": self.context.model_dump(mode="json"),
            }
        )
        if self.packet_digest != expected:
            raise ValueError("authorized context packet digest does not match")
        return self


class HostedAccessContextAuthorizer:
    """Exact-ref adapter over KRAIL's existing signed access authority."""

    def __init__(
        self,
        authority: AccessContextAuthority,
        context: SignedAccessContext,
        *,
        exact_refs: tuple[ResourceRef, ...],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not exact_refs:
            raise ValueError("at least one exact resource grant is required")
        self.authority = authority
        self.context = context
        self.exact_refs = frozenset(ref.exact_key for ref in exact_refs)
        self.clock = clock or (lambda: datetime.now(UTC))

    @property
    def authorization_digest(self) -> str:
        return self.context.context_digest

    def authorize(self, ref: ResourceRef, *, at: datetime | None = None) -> None:
        claims = self.authority.verify(self.context, as_of=at or self.clock())
        if "context.read" not in claims.actions:
            raise PermissionError("context access denied")
        if "*" not in claims.source_ids and ref.resource_id not in claims.source_ids:
            raise PermissionError("context access denied")
        if ref.exact_key not in self.exact_refs:
            raise PermissionError("context access denied")


class HostedProcedureReviewAuthorizer:
    """Signed-context adapter for the explicit procedure.review action."""

    def __init__(
        self,
        authority: AccessContextAuthority,
        context: SignedAccessContext,
        *,
        tenant_id: str,
        project_id: str,
        exact_refs: tuple[ResourceRef, ...],
        allowed_candidate_digests: tuple[str, ...],
        capability_digest: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not exact_refs:
            raise ValueError("at least one exact review resource grant is required")
        if not allowed_candidate_digests:
            raise ValueError("at least one exact review candidate is required")
        self.authority = authority
        self.context = context
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.exact_refs = frozenset(ref.exact_key for ref in exact_refs)
        self.allowed_candidate_digests = frozenset(allowed_candidate_digests)
        self.capability_digest = capability_digest
        self.clock = clock or (lambda: datetime.now(UTC))

    def authorize_review(
        self,
        candidate_digest: str,
        reviewer_ref: ResourceRef,
        evidence_refs: tuple[ResourceRef, ...],
        *,
        lineage_refs: tuple[ResourceRef, ...] = (),
        at: datetime | None = None,
    ) -> None:
        try:
            claims = self.authority.verify(self.context, as_of=at or self.clock())
        except PermissionError as exc:
            raise PermissionError("procedure review action denied") from exc
        if (
            (claims.tenant_id, claims.project_id) != (self.tenant_id, self.project_id)
            or claims.capability_id != PROCEDURE_REVIEW_CAPABILITY_ID
            or claims.capability_version != PROCEDURE_REVIEW_CAPABILITY_VERSION
            or claims.capability_digest != self.capability_digest
            or "procedure.review" not in claims.actions
            or claims.source_ids == ("*",)
            or candidate_digest not in self.allowed_candidate_digests
            or reviewer_ref.resource_id != claims.subject
            or reviewer_ref.authority != claims.issuer
        ):
            raise PermissionError("procedure review action denied")
        refs = (*lineage_refs, *evidence_refs, reviewer_ref)
        for ref in refs:
            if ref.exact_key not in self.exact_refs or ref.resource_id not in claims.source_ids:
                raise PermissionError("procedure review action denied")


class HostedProcedureInvalidationAuthorizer:
    """Signed action adapter for exact dependency invalidation writes."""

    def __init__(
        self,
        authority: AccessContextAuthority,
        context: SignedAccessContext,
        *,
        tenant_id: str,
        project_id: str,
        exact_refs: tuple[ResourceRef, ...],
        allowed_event_digests: tuple[str, ...],
        capability_digest: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not exact_refs or not allowed_event_digests:
            raise ValueError("exact invalidation refs and event digests are required")
        self.authority = authority
        self.context = context
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.exact_refs = frozenset(ref.exact_key for ref in exact_refs)
        self.allowed_event_digests = frozenset(allowed_event_digests)
        self.capability_digest = capability_digest
        self.clock = clock or (lambda: datetime.now(UTC))

    def authorize_invalidation(
        self, event_id: str, changed_ref: ResourceRef, event_digest: str, *, at: datetime
    ) -> None:
        del event_id
        try:
            # Evidence time is untrusted input; authorization uses the live clock.
            claims = self.authority.verify(self.context, as_of=self.clock())
        except PermissionError as exc:
            raise PermissionError("procedure invalidation action denied") from exc
        if (
            (claims.tenant_id, claims.project_id) != (self.tenant_id, self.project_id)
            or claims.capability_id != PROCEDURE_INVALIDATION_CAPABILITY_ID
            or claims.capability_version != PROCEDURE_INVALIDATION_CAPABILITY_VERSION
            or claims.capability_digest != self.capability_digest
            or "procedure.invalidate" not in claims.actions
            or claims.source_ids == ("*",)
            or changed_ref.exact_key not in self.exact_refs
            or changed_ref.resource_id not in claims.source_ids
            or event_digest not in self.allowed_event_digests
        ):
            raise PermissionError("procedure invalidation action denied")


class HostedTemporalProjectionWriter:
    """Signed exact-record writer; read contexts cannot publish projection inputs."""

    def __init__(self, authority: AccessContextAuthority, context: SignedAccessContext, *, tenant_id: str, project_id: str, exact_refs: tuple[ResourceRef, ...], allowed_record_digests: tuple[str, ...], capability_digest: str, clock: Callable[[], datetime] | None = None) -> None:
        if not exact_refs or not allowed_record_digests:
            raise ValueError("exact projection refs and record digests are required")
        self.authority, self.context = authority, context
        self.tenant_id, self.project_id = tenant_id, project_id
        self.exact_refs = frozenset(ref.exact_key for ref in exact_refs)
        self.allowed_record_digests = frozenset(allowed_record_digests)
        self.capability_digest, self.clock = capability_digest, clock or (lambda: datetime.now(UTC))

    def authorize(self, record: TemporalRecord, *, at: datetime) -> None:
        del at
        try:
            claims = self.authority.verify(self.context, as_of=self.clock())
        except PermissionError as exc:
            raise PermissionError("procedure projection write denied") from exc
        if ((claims.tenant_id, claims.project_id) != (self.tenant_id, self.project_id)
            or claims.capability_id != PROCEDURE_PROJECTION_CAPABILITY_ID
            or claims.capability_version != PROCEDURE_PROJECTION_CAPABILITY_VERSION
            or claims.capability_digest != self.capability_digest
            or "projection.write" not in claims.actions
            or claims.source_ids == ("*",)
            or record.record_digest not in self.allowed_record_digests):
            raise PermissionError("procedure projection write denied")
        for ref in record.source_refs + record.provenance_refs:
            if ref.exact_key not in self.exact_refs or ref.resource_id not in claims.source_ids:
                raise PermissionError("procedure projection write denied")
def assemble_authorized_context(
    service: ContextBriefService,
    request: ContextBriefRequest,
    authorizer: AuthorizedContextAuthorizer,
) -> AuthorizedContextPacket:
    """Assemble and bind a packet using the current KRAIL read authority.

    ``ContextBriefService`` performs pre-filtering and per-read checks.  The
    final check here closes the revocation race between assembly and handing
    the packet to a caller.
    """

    context = service.assemble(request, authorizer=authorizer)
    for item in context.evidence.items:
        authorizer.authorize(item.source)
    authorization_digest = authorizer.authorization_digest
    packet_digest = _digest(
        {
            "schema_version": "krail.authorized-context-packet.v1",
            "authorization_digest": authorization_digest,
            "context": context.model_dump(mode="json"),
        }
    )
    return AuthorizedContextPacket(
        packet_digest=packet_digest,
        authorization_digest=authorization_digest,
        context=context,
    )


__all__ = [
    "AuthorizedContextAuthorizer",
    "AuthorizedContextPacket",
    "HostedAccessContextAuthorizer",
    "HostedProcedureReviewAuthorizer",
    "HostedProcedureInvalidationAuthorizer",
    "HostedTemporalProjectionWriter",
    "PROCEDURE_REVIEW_CAPABILITY_ID",
    "PROCEDURE_REVIEW_CAPABILITY_VERSION",
    "PROCEDURE_INVALIDATION_CAPABILITY_ID",
    "PROCEDURE_INVALIDATION_CAPABILITY_VERSION",
    "PROCEDURE_PROJECTION_CAPABILITY_ID",
    "PROCEDURE_PROJECTION_CAPABILITY_VERSION",
    "assemble_authorized_context",
]
