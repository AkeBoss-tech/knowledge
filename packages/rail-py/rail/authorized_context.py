"""Authorized context packet composition over the existing KRAIL authority.

This module is intentionally a thin read boundary.  OpenSaddle (or another
caller) owns identity, grants, revocation, and operational records; KRAIL only
accepts a live exact-resource authorizer and returns its existing ContextBrief.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import fcntl
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from krail.provider.v1 import GetResourceRequest, MAX_EVIDENCE_ITEM_BYTES, ResourceRef
from rail.context_brief import ContextBrief, ContextBriefRequest, ContextBriefService
from rail.hosted.access import (
    AccessContextAuthority,
    SignedAccessContext,
    SignedPacketRequestBinding,
)
from rail.temporal_records import TemporalRecord


Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
PROCEDURE_REVIEW_CAPABILITY_ID = "krail.procedure-review"
PROCEDURE_REVIEW_CAPABILITY_VERSION = "1.0.0"
PROCEDURE_INVALIDATION_CAPABILITY_ID = "krail.procedure-invalidation"
PROCEDURE_INVALIDATION_CAPABILITY_VERSION = "1.0.0"
PROCEDURE_PROJECTION_CAPABILITY_ID = "krail.procedure-projection"
PROCEDURE_PROJECTION_CAPABILITY_VERSION = "1.0.0"
ROBOTICS_WORLD_MEMORY_CAPABILITY_ID = "krail.robotics-world-memory"
ROBOTICS_WORLD_MEMORY_CAPABILITY_VERSION = "1.0.0"
AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID = "krail.authorized-context-packet"
AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION = "1.0.0"
AUTHORIZED_CONTEXT_PACKET_SCHEMA_VERSION = "krail.authorized-context-packet.v2"
AUTHORIZED_CONTEXT_PACKET_REQUEST_VERSION = "krail.authorized-context-packet-request.v1"
AUTHORIZED_CONTEXT_PACKET_READ_VERSION = "krail.authorized-context-packet-read.v1"
MAX_AUTHORIZED_CONTEXT_PACKET_BYTES = 524_288
MAX_AUTHORIZED_CONTEXT_TOKENS = 32_768
MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONES = 1_024
MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONE_BYTES = 1_048_576


def _digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _context_refs(context: ContextBrief) -> tuple[ResourceRef, ...]:
    refs = (
        context.repository,
        context.issue,
        *(item.source for item in context.evidence.items),
        *(item.source for item in context.assertions),
        *(item.source for item in context.freshness),
        *(source for item in context.conflicts for source in item.sources),
        *(item.source for item in context.ranking_trace),
    )
    unique = {ref.exact_key: ref for ref in refs}
    return tuple(unique[key] for key in sorted(unique))


def robotics_world_memory_scope_digest(*, tenant_id: str, project_id: str, world_id: str, exact_refs: tuple[ResourceRef, ...]) -> str:
    """Capability binding for one signed hosted robotics world scope."""

    if not world_id or not exact_refs or len({ref.exact_key for ref in exact_refs}) != len(exact_refs):
        raise ValueError("world scope requires unique exact refs")
    return _digest({
        "capability_id": ROBOTICS_WORLD_MEMORY_CAPABILITY_ID,
        "capability_version": ROBOTICS_WORLD_MEMORY_CAPABILITY_VERSION,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "world_id": world_id,
        "exact_refs": [ref.model_dump(mode="json") for ref in sorted(exact_refs, key=lambda ref: ref.exact_key)],
    })


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


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class AuthorizedContextPacketCreateRequest(BaseModel):
    """Caller-owned signed scope and bounded query for one immutable packet."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["krail.authorized-context-packet-request.v1"] = (
        AUTHORIZED_CONTEXT_PACKET_REQUEST_VERSION
    )
    access_context: SignedAccessContext
    request_binding: SignedPacketRequestBinding
    exact_refs: tuple[ResourceRef, ...] = Field(min_length=2, max_length=256)
    context_request: ContextBriefRequest
    purpose: NonEmpty
    scope: NonEmpty
    max_context_tokens: int = Field(default=MAX_AUTHORIZED_CONTEXT_TOKENS, ge=1, le=MAX_AUTHORIZED_CONTEXT_TOKENS)

    @model_validator(mode="after")
    def _bounded_exact_request(self) -> "AuthorizedContextPacketCreateRequest":
        if self.context_request.query is None:
            raise ValueError("authorized packet query must be explicit")
        if len({ref.exact_key for ref in self.exact_refs}) != len(self.exact_refs):
            raise ValueError("authorized packet exact refs must be unique")
        mandatory = {
            self.context_request.repository.exact_key,
            self.context_request.issue.exact_key,
        }
        if not mandatory <= {ref.exact_key for ref in self.exact_refs}:
            raise ValueError("authorized packet exact refs must include repository and issue")
        if self.context_request.max_total_bytes > self.max_context_tokens * 4:
            raise ValueError("authorized packet byte budget exceeds token budget")
        return self


class AuthorizedContextPacketReadRequest(BaseModel):
    """Fresh caller delegation for an immutable stored packet handle."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["krail.authorized-context-packet-read.v1"] = (
        AUTHORIZED_CONTEXT_PACKET_READ_VERSION
    )
    packet_digest: Digest
    access_context: SignedAccessContext
    request_binding: SignedPacketRequestBinding
    exact_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=256)
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
    purpose: NonEmpty
    scope: NonEmpty

    @field_validator("exact_refs")
    @classmethod
    def _unique_exact_refs(cls, value: tuple[ResourceRef, ...]) -> tuple[ResourceRef, ...]:
        if len({ref.exact_key for ref in value}) != len(value):
            raise ValueError("authorized packet exact refs must be unique")
        return value


class SharedAuthorizedContextPacket(BaseModel):
    """Immutable bounded packet shared by a Run and its Inspector."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["krail.authorized-context-packet.v2"] = (
        AUTHORIZED_CONTEXT_PACKET_SCHEMA_VERSION
    )
    packet_id: Digest
    packet_digest: Digest
    authorization_digest: Digest
    capability_id: Literal["krail.authorized-context-packet"] = AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID
    capability_version: Literal["1.0.0"] = AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION
    capability_descriptor_digest: Digest
    request_digest: Digest
    purpose: NonEmpty
    scope: NonEmpty
    exact_evidence_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=256)
    context: ContextBrief
    canonical_context_utf8_bytes: int = Field(ge=1, le=MAX_AUTHORIZED_CONTEXT_PACKET_BYTES)
    canonical_packet_utf8_bytes: int = Field(ge=1, le=MAX_AUTHORIZED_CONTEXT_PACKET_BYTES)
    max_context_tokens: int = Field(ge=1, le=MAX_AUTHORIZED_CONTEXT_TOKENS)
    context_token_upper_bound: int = Field(ge=1, le=MAX_AUTHORIZED_CONTEXT_TOKENS)
    token_estimation: Literal["canonical-context-utf8-bytes-conservative-upper-bound"] = (
        "canonical-context-utf8-bytes-conservative-upper-bound"
    )
    truncation_uncertainty: Literal["bounded-search-or-source-truncation-may-omit-evidence"]
    truncated: bool

    @model_validator(mode="after")
    def _packet_digest_matches(self) -> "SharedAuthorizedContextPacket":
        body = self.model_dump(mode="json", exclude={"packet_id", "packet_digest"})
        expected = _digest(body)
        if self.packet_id != expected or self.packet_digest != expected:
            raise ValueError("authorized context packet digest does not match")
        if _context_refs(self.context) != self.exact_evidence_refs:
            raise ValueError("authorized context packet evidence refs do not match context")
        context_bytes = len(_canonical(self.context.model_dump(mode="json")))
        if (
            self.canonical_context_utf8_bytes != context_bytes
            or self.context_token_upper_bound != context_bytes
            or self.context_token_upper_bound > self.max_context_tokens
        ):
            raise ValueError("authorized context packet context budget does not match")
        if self.canonical_packet_utf8_bytes != len(
            _canonical(self.model_dump(mode="json"))
        ):
            raise ValueError("authorized context packet byte count does not match")
        return self


class AuthorizedContextReauthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["krail.authorized-context-reauthorization.v1"] = (
        "krail.authorized-context-reauthorization.v1"
    )
    reauthorized_at: datetime
    current_authorization_digest: Digest
    decision_digest: Digest

    @field_validator("reauthorized_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reauthorized_at must include a timezone")
        return value


class AuthorizedContextPacketReadResult(BaseModel):
    """Available packet or one metadata-free protected-resource replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["krail.authorized-context-packet-read-result.v1"] = (
        "krail.authorized-context-packet-read-result.v1"
    )
    status: Literal["available", "context_packet_unavailable"]
    packet: SharedAuthorizedContextPacket | None = None
    reauthorization: AuthorizedContextReauthorization | None = None

    @model_validator(mode="after")
    def _coarse_unavailable(self) -> "AuthorizedContextPacketReadResult":
        if self.status == "available" and (
            self.packet is None or self.reauthorization is None
        ):
            raise ValueError("packet read result availability fields do not match status")
        if self.status == "context_packet_unavailable" and (
            self.packet is not None or self.reauthorization is not None
        ):
            raise ValueError("packet read result availability fields do not match status")
        return self


class AuthorizedContextPacketTombstone(BaseModel):
    """Content-free durable denial for one removed local cache entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    packet_digest: Digest
    reason: Literal["source-deleted", "source-revoked", "retention-expired"]
    occurred_at: datetime
    decision_digest: Digest

    @field_validator("occurred_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value


def authorized_context_packet_request_digest(
    *,
    exact_refs: tuple[ResourceRef, ...],
    context_request: ContextBriefRequest,
    purpose: str,
    scope: str,
    max_context_tokens: int,
) -> str:
    """Digest every caller-authorized input that can shape packet content."""

    return _digest(
        {
            "schema_version": AUTHORIZED_CONTEXT_PACKET_REQUEST_VERSION,
            "exact_refs": [
                ref.model_dump(mode="json")
                for ref in sorted(exact_refs, key=lambda value: value.exact_key)
            ],
            "context_request": context_request.model_dump(mode="json"),
            "purpose": purpose,
            "scope": scope,
            "max_context_tokens": max_context_tokens,
        }
    )


class AuthorizedContextPacketService:
    """Published create/read service with caller-owned signed authorization."""

    def __init__(
        self,
        context_briefs: ContextBriefService,
        *,
        project_path: str | Path,
        authority: AccessContextAuthority,
        tenant_id: str,
        project_id: str,
        capability_descriptor_digest: str,
        current_ref_resolver: Callable[[str], ResourceRef],
        clock: Callable[[], datetime] | None = None,
        packet_retention_until: Callable[[SharedAuthorizedContextPacket], datetime | None] | None = None,
    ) -> None:
        self.context_briefs = context_briefs
        self.provider = context_briefs.provider
        self.project_path = Path(project_path).resolve()
        self.packet_path = self.project_path / ".krail" / "authorized-context-packets"
        self.tombstone_path = self.project_path / ".krail" / "authorized-context-packet-tombstones.json"
        self.authority = authority
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.capability_descriptor_digest = capability_descriptor_digest
        self.clock = clock or (lambda: datetime.now(UTC))
        self.current_ref_resolver = current_ref_resolver
        self.packet_retention_until = packet_retention_until

    def _tombstones(self) -> dict[str, AuthorizedContextPacketTombstone]:
        try:
            with self.tombstone_path.open("rb") as handle:
                raw = handle.read(MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONE_BYTES + 1)
            if len(raw) > MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONE_BYTES:
                raise ValueError("packet tombstone journal exceeds byte limit")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
                raise ValueError("invalid packet tombstone journal")
            if payload.get("schema_version") != "krail.authorized-context-packet-tombstones.v1":
                raise ValueError("unknown packet tombstone schema")
            entries = {
                item["packet_digest"]: AuthorizedContextPacketTombstone.model_validate(item)
                for item in payload["entries"]
            }
            if len(entries) != len(payload["entries"]) or len(entries) > MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONES:
                raise ValueError("invalid packet tombstone journal")
            return entries
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid packet tombstone journal") from exc

    @contextmanager
    def _lifecycle_lock(self):
        self.tombstone_path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.tombstone_path.with_suffix(".lock")
        with lock.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_packet(self, path: Path) -> SharedAuthorizedContextPacket:
        with path.open("rb") as handle:
            payload = handle.read(MAX_AUTHORIZED_CONTEXT_PACKET_BYTES + 1)
        if len(payload) > MAX_AUTHORIZED_CONTEXT_PACKET_BYTES:
            raise ValueError("authorized context packet exceeds byte limit")
        return SharedAuthorizedContextPacket.model_validate_json(payload)

    def _persist_tombstones(self, entries: dict[str, AuthorizedContextPacketTombstone]) -> None:
        if len(entries) > MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONES:
            raise PermissionError("authorized context packet retention is unavailable")
        self.tombstone_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _canonical({
            "schema_version": "krail.authorized-context-packet-tombstones.v1",
            "entries": [entries[key].model_dump(mode="json") for key in sorted(entries)],
        })
        descriptor, temporary = tempfile.mkstemp(dir=self.tombstone_path.parent, prefix=".authorized-context-packet-tombstones.")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.tombstone_path)
            directory = os.open(self.tombstone_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _maintenance_claims(self, access_context: SignedAccessContext, refs: tuple[ResourceRef, ...] = ()) -> None:
        claims = self.authority.verify(access_context, as_of=self.clock())
        if (
            (claims.tenant_id, claims.project_id) != (self.tenant_id, self.project_id)
            or "retention.enforce" not in claims.actions
            or claims.capability_id != AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID
            or claims.capability_version != AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION
            or claims.capability_digest != self.capability_descriptor_digest
            or claims.source_ids == ("*",)
            or len({ref.exact_key for ref in refs}) != len(refs)
            or any(ref.resource_id not in claims.source_ids for ref in refs)
        ):
            raise PermissionError("authorized context packet retention is unavailable")

    def invalidate_for_sources(
        self,
        access_context: SignedAccessContext,
        *,
        exact_refs: tuple[ResourceRef, ...],
        reason: Literal["source-deleted", "source-revoked", "retention-expired"],
    ) -> tuple[str, ...]:
        """Remove matching service-owned cache entries after a live caller decision.

        ``reason`` describes an already-authorized source lifecycle event; the
        signed context and injected clock, rather than that value, authorize it.
        """
        self._maintenance_claims(access_context, exact_refs)
        target_keys = {ref.exact_key for ref in exact_refs}
        with self._lifecycle_lock():
            self._maintenance_claims(access_context, exact_refs)
            candidates: list[SharedAuthorizedContextPacket] = []
            for path in self.packet_path.glob("*.json") if self.packet_path.is_dir() else ():
                try:
                    packet = self._load_packet(path)
                except (OSError, ValueError):
                    continue
                if any(ref.exact_key in target_keys for ref in packet.exact_evidence_refs):
                    candidates.append(packet)
            self._maintenance_claims(access_context, exact_refs)
            return self._remove_packets(access_context, candidates, reason=reason)

    def _remove_packets(
        self,
        access_context: SignedAccessContext,
        candidates: list[SharedAuthorizedContextPacket],
        *,
        reason: Literal["source-deleted", "source-revoked", "retention-expired"],
    ) -> tuple[str, ...]:
        """Journal first, then remove exactly the selected cache files."""
        entries = self._tombstones()
        new = [packet for packet in candidates if packet.packet_digest not in entries]
        if len(entries) + len(new) > MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONES:
            raise PermissionError("authorized context packet retention is unavailable")
        now = self.clock()
        for packet in new:
            entries[packet.packet_digest] = AuthorizedContextPacketTombstone(
                packet_digest=packet.packet_digest,
                reason=reason,
                occurred_at=now,
                decision_digest=_digest({"packet_digest": packet.packet_digest, "reason": reason, "occurred_at": now.isoformat(), "authorization_digest": access_context.context_digest}),
            )
        self._persist_tombstones(entries)
        for packet in candidates:
            try:
                self._packet_file(packet.packet_digest).unlink(missing_ok=True)
            except OSError as exc:
                raise PermissionError("authorized context packet retention is incomplete") from exc
        return tuple(sorted(packet.packet_digest for packet in candidates))

    def enforce_retention(self, access_context: SignedAccessContext) -> tuple[str, ...]:
        """Apply an explicitly injected policy using the service clock only."""
        self._maintenance_claims(access_context)
        if self.packet_retention_until is None:
            raise PermissionError("authorized context packet retention is unavailable")
        packets: list[SharedAuthorizedContextPacket] = []
        for path in self.packet_path.glob("*.json") if self.packet_path.is_dir() else ():
            try:
                packet = self._load_packet(path)
            except (OSError, ValueError):
                continue
            until = self.packet_retention_until(packet)
            if until is not None and until <= self.clock():
                packets.append(packet)
        refs = tuple(
            {ref.exact_key: ref for packet in packets for ref in packet.exact_evidence_refs}.values()
        )
        if not refs:
            return ()
        with self._lifecycle_lock():
            self._maintenance_claims(access_context, refs)
            return self._remove_packets(access_context, packets, reason="retention-expired")

    def _authorizer(
        self,
        *,
        access_context: SignedAccessContext,
        request_binding: SignedPacketRequestBinding,
        exact_refs: tuple[ResourceRef, ...],
        request_digest: str,
        purpose: str,
        scope: str,
    ) -> HostedAccessContextAuthorizer:
        now = self.clock()
        claims = self.authority.verify(access_context, as_of=now)
        binding = self.authority.verify_packet_request_binding(
            request_binding, access_context=access_context, as_of=now
        )
        exact_keys = {ref.exact_key for ref in exact_refs}
        if (
            (claims.tenant_id, claims.project_id) != (self.tenant_id, self.project_id)
            or claims.capability_id != AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID
            or claims.capability_version != AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION
            or claims.capability_digest != self.capability_descriptor_digest
            or "context.read" not in claims.actions
            or claims.source_ids == ("*",)
            or (binding.tenant_id, binding.project_id) != (self.tenant_id, self.project_id)
            or binding.capability_id != AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID
            or binding.capability_version != AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION
            or binding.capability_digest != self.capability_descriptor_digest
            or binding.request_digest != request_digest
            or binding.purpose != purpose
            or binding.scope != scope
            or scope != self.project_id
            or any(ref.resource_id not in claims.source_ids for ref in exact_refs)
            or len(exact_keys) != len(exact_refs)
        ):
            raise PermissionError("authorized context packet access denied")
        return HostedAccessContextAuthorizer(
            self.authority,
            access_context,
            exact_refs=exact_refs,
            clock=self.clock,
        )

    def _verify_exact_resources(
        self,
        refs: tuple[ResourceRef, ...],
        authorizer: HostedAccessContextAuthorizer,
    ) -> None:
        for ref in refs:
            authorizer.authorize(ref, at=self.clock())
            self.provider.get_resource(
                GetResourceRequest(ref=ref, max_bytes=MAX_EVIDENCE_ITEM_BYTES)
            )
            if self.current_ref_resolver(ref.resource_id).exact_key != ref.exact_key:
                raise ValueError("authorized context packet source revision changed")

    def _packet_file(self, digest: str) -> Path:
        return self.packet_path / f"{digest.removeprefix('sha256:')}.json"

    def _persist(self, packet: SharedAuthorizedContextPacket) -> None:
        with self._lifecycle_lock():
            self._persist_locked(packet)

    def _persist_locked(self, packet: SharedAuthorizedContextPacket) -> None:
        if packet.packet_digest in self._tombstones():
            raise PermissionError("authorized context packet access denied")
        if (
            self.packet_retention_until is not None
            and (until := self.packet_retention_until(packet)) is not None
            and until <= self.clock()
        ):
            raise PermissionError("authorized context packet access denied")
        payload = _canonical(packet.model_dump(mode="json"))
        if len(payload) > MAX_AUTHORIZED_CONTEXT_PACKET_BYTES:
            raise ValueError("authorized context packet exceeds byte limit")
        path = self._packet_file(packet.packet_digest)
        self.packet_path.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError("conflicting authorized context packet")
            return
        descriptor, temporary = tempfile.mkstemp(
            dir=self.packet_path, prefix=".authorized-context-packet."
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def create(
        self, request: AuthorizedContextPacketCreateRequest
    ) -> SharedAuthorizedContextPacket:
        request_digest = authorized_context_packet_request_digest(
            exact_refs=request.exact_refs,
            context_request=request.context_request,
            purpose=request.purpose,
            scope=request.scope,
            max_context_tokens=request.max_context_tokens,
        )
        try:
            authorizer = self._authorizer(
                access_context=request.access_context,
                request_binding=request.request_binding,
                exact_refs=request.exact_refs,
                request_digest=request_digest,
                purpose=request.purpose,
                scope=request.scope,
            )
            context = self.context_briefs.assemble(
                request.context_request, authorizer=authorizer
            )
            evidence_refs = _context_refs(context)
            self._verify_exact_resources(evidence_refs, authorizer)
            # Reverify the signed service scope immediately before persistence.
            self._authorizer(
                access_context=request.access_context,
                request_binding=request.request_binding,
                exact_refs=request.exact_refs,
                request_digest=request_digest,
                purpose=request.purpose,
                scope=request.scope,
            )
        except (FileNotFoundError, LookupError, PermissionError, ValueError) as exc:
            raise PermissionError("authorized context packet access denied") from exc

        context_bytes = len(_canonical(context.model_dump(mode="json")))
        if context_bytes > request.max_context_tokens:
            raise ValueError("authorized context packet exceeds token upper bound")
        body = {
            "schema_version": AUTHORIZED_CONTEXT_PACKET_SCHEMA_VERSION,
            "authorization_digest": request.access_context.context_digest,
            "capability_id": AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
            "capability_version": AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
            "capability_descriptor_digest": self.capability_descriptor_digest,
            "request_digest": request_digest,
            "purpose": request.purpose,
            "scope": request.scope,
            "exact_evidence_refs": [ref.model_dump(mode="json") for ref in evidence_refs],
            "context": context.model_dump(mode="json"),
            "canonical_context_utf8_bytes": context_bytes,
            "canonical_packet_utf8_bytes": 0,
            "max_context_tokens": request.max_context_tokens,
            "context_token_upper_bound": context_bytes,
            "token_estimation": "canonical-context-utf8-bytes-conservative-upper-bound",
            "truncation_uncertainty": "bounded-search-or-source-truncation-may-omit-evidence",
            "truncated": context.truncated,
        }
        for _ in range(8):
            packet_digest = _digest(body)
            packet_size = len(
                _canonical(
                    {
                        "packet_id": packet_digest,
                        "packet_digest": packet_digest,
                        **body,
                    }
                )
            )
            if packet_size == body["canonical_packet_utf8_bytes"]:
                break
            body["canonical_packet_utf8_bytes"] = packet_size
        else:
            raise ValueError("authorized context packet byte count did not converge")
        packet_digest = _digest(body)
        packet = SharedAuthorizedContextPacket(
            packet_id=packet_digest,
            packet_digest=packet_digest,
            **body,
        )
        try:
            with self._lifecycle_lock():
                self._verify_exact_resources(evidence_refs, authorizer)
                self._authorizer(
                    access_context=request.access_context,
                    request_binding=request.request_binding,
                    exact_refs=request.exact_refs,
                    request_digest=request_digest,
                    purpose=request.purpose,
                    scope=request.scope,
                )
                self._persist_locked(packet)
        except (LookupError, PermissionError, ValueError) as exc:
            raise PermissionError("authorized context packet access denied") from exc
        return packet

    def read(
        self, request: AuthorizedContextPacketReadRequest
    ) -> AuthorizedContextPacketReadResult:
        unavailable = AuthorizedContextPacketReadResult(
            status="context_packet_unavailable"
        )
        try:
            if request.packet_digest in self._tombstones(): return unavailable
            packet = self._load_packet(self._packet_file(request.packet_digest))
            if (
                packet.packet_digest != request.packet_digest
                or packet.context.evidence.query != request.query
                or packet.purpose != request.purpose
                or packet.scope != request.scope
            ):
                return unavailable
            if (
                self.packet_retention_until is not None
                and (until := self.packet_retention_until(packet)) is not None
                and until <= self.clock()
            ):
                return unavailable
            authorizer = self._authorizer(
                access_context=request.access_context,
                request_binding=request.request_binding,
                exact_refs=request.exact_refs,
                request_digest=packet.request_digest,
                purpose=request.purpose,
                scope=request.scope,
            )
            granted_keys = {ref.exact_key for ref in request.exact_refs}
            if any(ref.exact_key not in granted_keys for ref in packet.exact_evidence_refs):
                return unavailable
            self._verify_exact_resources(packet.exact_evidence_refs, authorizer)
            authorizer = self._authorizer(
                access_context=request.access_context,
                request_binding=request.request_binding,
                exact_refs=request.exact_refs,
                request_digest=packet.request_digest,
                purpose=request.purpose,
                scope=request.scope,
            )
            self._verify_exact_resources(packet.exact_evidence_refs, authorizer)
            now = self.clock()
            receipt_body = {
                "reauthorized_at": now.isoformat(),
                "current_authorization_digest": request.access_context.context_digest,
                "packet_digest": packet.packet_digest,
                "request_binding_digest": request.request_binding.binding_digest,
            }
            receipt = AuthorizedContextReauthorization(
                reauthorized_at=now,
                current_authorization_digest=request.access_context.context_digest,
                decision_digest=_digest(receipt_body),
            )
            result = AuthorizedContextPacketReadResult(
                status="available", packet=packet, reauthorization=receipt
            )
            if len(_canonical(result.model_dump(mode="json"))) > (
                MAX_AUTHORIZED_CONTEXT_PACKET_BYTES + 8192
            ):
                return unavailable
            with self._lifecycle_lock():
                if request.packet_digest in self._tombstones() or (
                    self.packet_retention_until is not None
                    and (until := self.packet_retention_until(packet)) is not None
                    and until <= self.clock()
                ):
                    return unavailable
            return result
        except (LookupError, OSError, PermissionError, ValueError):
            return unavailable


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


class _HostedRoboticsAdapter:
    """Shared signed-world binding; decisions always use the injected live clock."""

    def __init__(self, authority: AccessContextAuthority, context: SignedAccessContext, *, tenant_id: str, project_id: str, world_id: str, exact_refs: tuple[ResourceRef, ...], capability_digest: str, clock: Callable[[], datetime] | None = None) -> None:
        if not world_id or not exact_refs:
            raise ValueError("world scope and exact robotics refs are required")
        if capability_digest != robotics_world_memory_scope_digest(tenant_id=tenant_id, project_id=project_id, world_id=world_id, exact_refs=exact_refs):
            raise ValueError("robotics capability digest does not bind the exact world scope")
        self.authority, self.context = authority, context
        self.tenant_id, self.project_id, self.world_id = tenant_id, project_id, world_id
        self.exact_refs = frozenset(ref.exact_key for ref in exact_refs)
        self.capability_digest = capability_digest
        self.clock = clock or (lambda: datetime.now(UTC))

    def _claims(self, action: str):
        try:
            claims = self.authority.verify(self.context, as_of=self.clock())
        except PermissionError as exc:
            raise PermissionError("robotics world-memory access denied") from exc
        if (
            (claims.tenant_id, claims.project_id) != (self.tenant_id, self.project_id)
            or claims.capability_id != ROBOTICS_WORLD_MEMORY_CAPABILITY_ID
            or claims.capability_version != ROBOTICS_WORLD_MEMORY_CAPABILITY_VERSION
            or claims.capability_digest != self.capability_digest
            or action not in claims.actions
            or claims.source_ids == ("*",)
        ):
            raise PermissionError("robotics world-memory access denied")
        return claims

    def _authorize_ref(self, ref: ResourceRef, *, action: str) -> None:
        claims = self._claims(action)
        if ref.exact_key not in self.exact_refs or ref.resource_id not in claims.source_ids:
            raise PermissionError("robotics world-memory access denied")


class HostedRoboticsWorldReader(_HostedRoboticsAdapter):
    """Signed exact-ref reader bound to one hosted robotics world scope."""

    def authorize(self, ref: ResourceRef) -> None:
        self._authorize_ref(ref, action="context.read")


class HostedRoboticsProjectionWriter(_HostedRoboticsAdapter):
    """Signed world-scoped projection writer; a read context cannot ingest or publish."""

    def __init__(self, *args, allowed_record_digests: tuple[str, ...], allowed_publication_refs: tuple[ResourceRef, ...] = (), **kwargs) -> None:
        if not allowed_record_digests:
            raise ValueError("exact robotics record digests are required")
        super().__init__(*args, **kwargs)
        self.allowed_record_digests = frozenset(allowed_record_digests)
        self.allowed_publication_refs = frozenset(ref.exact_key for ref in allowed_publication_refs)

    def authorize(self, record: TemporalRecord, *, at: datetime) -> None:
        del at
        claims = self._claims("projection.write")
        if (
            record.entity_authority != f"robotics://world/{self.world_id}"
            or record.record_digest not in self.allowed_record_digests
        ):
            raise PermissionError("robotics world-memory write denied")
        for ref in record.source_refs + record.provenance_refs:
            if ref.exact_key not in self.exact_refs or ref.resource_id not in claims.source_ids:
                raise PermissionError("robotics world-memory write denied")

    def authorize_publication(self, ref: ResourceRef, *, content_digest: str, at: datetime) -> None:
        """Authorize one non-temporal canonical row whose exact ref commits its content."""

        del at
        claims = self._claims("projection.write")
        if (
            content_digest != ref.digest
            or ref.exact_key not in self.allowed_publication_refs
            or ref.exact_key not in self.exact_refs
            or ref.resource_id not in claims.source_ids
        ):
            raise PermissionError("robotics world-memory write denied")


class HostedRoboticsInvalidationAuthorizer(_HostedRoboticsAdapter):
    """Signed exact-event writer for one world's dependency invalidations."""

    def __init__(self, *args, allowed_event_digests: tuple[str, ...], **kwargs) -> None:
        if not allowed_event_digests:
            raise ValueError("exact robotics invalidation event digests are required")
        super().__init__(*args, **kwargs)
        self.allowed_event_digests = frozenset(allowed_event_digests)

    def authorize_invalidation(self, event_id: str, changed_ref: ResourceRef, event_digest: str, *, at: datetime) -> None:
        del event_id, at
        claims = self._claims("procedure.invalidate")
        if (
            changed_ref.exact_key not in self.exact_refs
            or changed_ref.resource_id not in claims.source_ids
            or event_digest not in self.allowed_event_digests
        ):
            raise PermissionError("robotics world-memory invalidation denied")
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
    "AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID",
    "AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION",
    "AUTHORIZED_CONTEXT_PACKET_SCHEMA_VERSION",
    "MAX_AUTHORIZED_CONTEXT_PACKET_BYTES",
    "MAX_AUTHORIZED_CONTEXT_TOKENS",
    "AuthorizedContextAuthorizer",
    "AuthorizedContextPacket",
    "AuthorizedContextPacketCreateRequest",
    "AuthorizedContextPacketReadRequest",
    "AuthorizedContextPacketReadResult",
    "AuthorizedContextPacketService",
    "AuthorizedContextReauthorization",
    "HostedAccessContextAuthorizer",
    "HostedProcedureReviewAuthorizer",
    "HostedProcedureInvalidationAuthorizer",
    "HostedTemporalProjectionWriter",
    "HostedRoboticsInvalidationAuthorizer",
    "HostedRoboticsProjectionWriter",
    "HostedRoboticsWorldReader",
    "PROCEDURE_REVIEW_CAPABILITY_ID",
    "PROCEDURE_REVIEW_CAPABILITY_VERSION",
    "PROCEDURE_INVALIDATION_CAPABILITY_ID",
    "PROCEDURE_INVALIDATION_CAPABILITY_VERSION",
    "PROCEDURE_PROJECTION_CAPABILITY_ID",
    "PROCEDURE_PROJECTION_CAPABILITY_VERSION",
    "ROBOTICS_WORLD_MEMORY_CAPABILITY_ID",
    "ROBOTICS_WORLD_MEMORY_CAPABILITY_VERSION",
    "SharedAuthorizedContextPacket",
    "authorized_context_packet_request_digest",
    "robotics_world_memory_scope_digest",
    "assemble_authorized_context",
]
