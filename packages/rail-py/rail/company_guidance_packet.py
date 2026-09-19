"""Signed, bounded company-guidance packets over existing company/procedure reads."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Literal

import fcntl
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from krail.provider.v1 import ResourceRef
from rail.company_knowledge import (
    CompanyOperationalGuidance,
    CompanyOperationalGuidanceRequest,
    CompanyOperationalGuidanceService,
)
from rail.hosted.access import AccessContextAuthority, SignedAccessContext, SignedPacketRequestBinding
from rail.authorized_context import (
    AuthorizedContextPacketTombstone,
    MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONES,
)


CAPABILITY_ID = "krail.company-guidance-packet"
CAPABILITY_VERSION = "1.0.0"
SCHEMA_VERSION = "krail.company-guidance-packet.v1"
REQUEST_VERSION = "krail.company-guidance-packet-request.v1"
READ_VERSION = "krail.company-guidance-packet-read.v1"
MAX_PACKET_BYTES = 524_288


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompanyGuidancePacketCreateRequest(StrictModel):
    schema_version: Literal["krail.company-guidance-packet-request.v1"] = REQUEST_VERSION
    access_context: SignedAccessContext
    request_binding: SignedPacketRequestBinding
    exact_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=256)
    guidance_request: CompanyOperationalGuidanceRequest
    purpose: str = Field(min_length=1, max_length=512)
    scope: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _unique_refs(self) -> "CompanyGuidancePacketCreateRequest":
        if len({ref.exact_key for ref in self.exact_refs}) != len(self.exact_refs):
            raise ValueError("company guidance packet exact refs must be unique")
        return self


class CompanyGuidancePacketReadRequest(StrictModel):
    schema_version: Literal["krail.company-guidance-packet-read.v1"] = READ_VERSION
    packet_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    access_context: SignedAccessContext
    request_binding: SignedPacketRequestBinding
    exact_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=256)
    purpose: str = Field(min_length=1, max_length=512)
    scope: str = Field(min_length=1, max_length=512)


class CompanyGuidancePacket(StrictModel):
    schema_version: Literal["krail.company-guidance-packet.v1"] = SCHEMA_VERSION
    packet_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    packet_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authorization_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    capability_id: Literal["krail.company-guidance-packet"] = CAPABILITY_ID
    capability_version: Literal["1.0.0"] = CAPABILITY_VERSION
    capability_descriptor_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    purpose: str = Field(min_length=1, max_length=512)
    scope: str = Field(min_length=1, max_length=512)
    exact_lineage_refs: tuple[ResourceRef, ...] = Field(min_length=1, max_length=256)
    guidance: CompanyOperationalGuidance
    canonical_packet_utf8_bytes: int = Field(ge=1, le=MAX_PACKET_BYTES)

    @model_validator(mode="after")
    def _digest_matches(self) -> "CompanyGuidancePacket":
        body = self.model_dump(mode="json", exclude={"packet_id", "packet_digest"})
        expected = _digest(body)
        if self.packet_id != expected or self.packet_digest != expected:
            raise ValueError("company guidance packet digest does not match")
        if self.canonical_packet_utf8_bytes != len(_canonical(self.model_dump(mode="json"))):
            raise ValueError("company guidance packet byte count does not match")
        return self


class CompanyGuidancePacketReadResult(StrictModel):
    status: Literal["available", "packet_unavailable"]
    packet: CompanyGuidancePacket | None = None
    reauthorized_at: datetime | None = None
    current_authorization_digest: str | None = None


class CompanyGuidancePacketService:
    """Content-addressed derived cache; authority and company readers are injected."""

    def __init__(
        self,
        guidance_factory: Callable[[SignedAccessContext, tuple[ResourceRef, ...]], CompanyOperationalGuidanceService],
        *,
        project_path: str | Path,
        authority: AccessContextAuthority,
        tenant_id: str,
        project_id: str,
        capability_descriptor_digest: str,
        current_ref_resolver: Callable[[ResourceRef], ResourceRef],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.guidance_factory = guidance_factory
        self.project_path = Path(project_path).resolve()
        self.packet_path = self.project_path / ".krail" / "authorized-context-packets"
        self.tombstone_path = self.project_path / ".krail" / "authorized-context-packet-tombstones.json"
        self.authority = authority
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.capability_descriptor_digest = capability_descriptor_digest
        self.current_ref_resolver = current_ref_resolver
        self.clock = clock or (lambda: datetime.now(UTC))

    @contextmanager
    def _lock(self):
        self.tombstone_path.parent.mkdir(parents=True, exist_ok=True)
        with self.tombstone_path.with_suffix(".lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _tombstones(self) -> set[str]:
        try:
            raw = json.loads(self.tombstone_path.read_text())
        except FileNotFoundError:
            return set()
        if raw.get("schema_version") != "krail.authorized-context-packet-tombstones.v1":
            raise ValueError("invalid packet tombstone journal")
        return {item["packet_digest"] for item in raw.get("entries", [])}

    def _persist_tombstones(self, entries: dict[str, AuthorizedContextPacketTombstone]) -> None:
        if len(entries) > MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONES:
            raise PermissionError("company guidance packet retention is unavailable")
        payload = _canonical({
            "schema_version": "krail.authorized-context-packet-tombstones.v1",
            "entries": [entries[key].model_dump(mode="json") for key in sorted(entries)],
        })
        self.tombstone_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.tombstone_path.parent, prefix=".company-guidance-tombstones.")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.tombstone_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _binding(self, context: SignedAccessContext, binding: SignedPacketRequestBinding, *, request_digest: str, purpose: str, scope: str, refs: tuple[ResourceRef, ...]):
        claims = self.authority.verify(context, as_of=self.clock())
        signed = self.authority.verify_packet_request_binding(binding, access_context=context, as_of=self.clock())
        if ((claims.tenant_id, claims.project_id) != (self.tenant_id, self.project_id)
                or claims.capability_id != CAPABILITY_ID or claims.capability_version != CAPABILITY_VERSION
                or claims.capability_digest != self.capability_descriptor_digest or "context.read" not in claims.actions
                or signed.request_digest != request_digest or signed.purpose != purpose or signed.scope != scope
                or (signed.tenant_id, signed.project_id) != (self.tenant_id, self.project_id)
                or any(ref.resource_id not in claims.source_ids for ref in refs)):
            raise PermissionError("company guidance packet access denied")

    def _request_digest(self, request: CompanyGuidancePacketCreateRequest) -> str:
        return _digest({"exact_refs": [r.model_dump(mode="json") for r in request.exact_refs], "guidance_request": request.guidance_request.model_dump(mode="json"), "purpose": request.purpose, "scope": request.scope})

    def _file(self, digest: str) -> Path:
        return self.packet_path / f"{digest.removeprefix('sha256:')}.company-guidance.json"

    def _current(self, refs: tuple[ResourceRef, ...]) -> None:
        for ref in refs:
            if self.current_ref_resolver(ref).exact_key != ref.exact_key:
                raise ValueError("company guidance source revision changed")

    def invalidate_for_sources(
        self,
        access_context: SignedAccessContext,
        *,
        exact_refs: tuple[ResourceRef, ...],
        reason: Literal["source-deleted", "source-revoked", "retention-expired"],
    ) -> tuple[str, ...]:
        claims = self.authority.verify(access_context, as_of=self.clock())
        if ((claims.tenant_id, claims.project_id) != (self.tenant_id, self.project_id)
                or claims.capability_id != CAPABILITY_ID
                or claims.capability_version != CAPABILITY_VERSION
                or claims.capability_digest != self.capability_descriptor_digest
                or "retention.enforce" not in claims.actions
                or claims.source_ids == ("*",)
                or any(ref.resource_id not in claims.source_ids for ref in exact_refs)):
            raise PermissionError("company guidance packet retention is unavailable")
        target = {ref.exact_key for ref in exact_refs}
        with self._lock():
            entries: dict[str, AuthorizedContextPacketTombstone] = {}
            try:
                raw = json.loads(self.tombstone_path.read_text())
                entries = {item["packet_digest"]: AuthorizedContextPacketTombstone.model_validate(item)
                           for item in raw.get("entries", [])}
            except FileNotFoundError:
                pass
            matched: list[str] = []
            for path in self.packet_path.glob("*.company-guidance.json") if self.packet_path.is_dir() else ():
                try:
                    packet = CompanyGuidancePacket.model_validate_json(path.read_bytes())
                except (OSError, ValueError):
                    continue
                if any(ref.exact_key in target for ref in packet.exact_lineage_refs):
                    matched.append(packet.packet_digest)
                    entries[packet.packet_digest] = AuthorizedContextPacketTombstone(
                        packet_digest=packet.packet_digest,
                        reason=reason,
                        occurred_at=self.clock(),
                        decision_digest=_digest({"packet_digest": packet.packet_digest, "reason": reason, "authorization_digest": access_context.context_digest}),
                    )
            self._persist_tombstones(entries)
            for digest in matched:
                self._file(digest).unlink(missing_ok=True)
            return tuple(sorted(matched))

    def _persist(self, packet: CompanyGuidancePacket) -> None:
        with self._lock():
            if packet.packet_digest in self._tombstones():
                raise PermissionError("company guidance packet is unavailable")
            payload = _canonical(packet.model_dump(mode="json"))
            if len(payload) > MAX_PACKET_BYTES:
                raise ValueError("company guidance packet exceeds byte limit")
            self.packet_path.mkdir(parents=True, exist_ok=True)
            path = self._file(packet.packet_digest)
            if path.exists() and path.read_bytes() != payload:
                raise ValueError("conflicting company guidance packet")
            if not path.exists():
                fd, temporary = tempfile.mkstemp(dir=self.packet_path, prefix=".company-guidance.")
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)

    def create(self, request: CompanyGuidancePacketCreateRequest) -> CompanyGuidancePacket:
        request_digest = self._request_digest(request)
        self._binding(request.access_context, request.request_binding, request_digest=request_digest, purpose=request.purpose, scope=request.scope, refs=request.exact_refs)
        self._current(request.exact_refs)
        guidance = self.guidance_factory(request.access_context, request.exact_refs).read(request.guidance_request)
        if guidance.status != "guidance" or guidance.procedure is None:
            raise PermissionError("company guidance packet access denied")
        lineage = tuple(dict.fromkeys((*guidance.company_lineage, *guidance.procedure_lineage)))
        if not {ref.exact_key for ref in lineage} <= {ref.exact_key for ref in request.exact_refs}:
            raise PermissionError("company guidance packet access denied")
        self._current(lineage)
        body = dict(authorization_digest=request.access_context.context_digest, capability_id=CAPABILITY_ID, capability_version=CAPABILITY_VERSION, capability_descriptor_digest=self.capability_descriptor_digest, request_digest=request_digest, purpose=request.purpose, scope=request.scope, exact_lineage_refs=[r.model_dump(mode="json") for r in lineage], guidance=guidance.model_dump(mode="json"), canonical_packet_utf8_bytes=0)
        for _ in range(8):
            digest = _digest({"schema_version": SCHEMA_VERSION, **body})
            size = len(_canonical({"schema_version": SCHEMA_VERSION, "packet_id": digest, "packet_digest": digest, **body}))
            body["canonical_packet_utf8_bytes"] = size
        digest = _digest({"schema_version": SCHEMA_VERSION, **body})
        packet = CompanyGuidancePacket(packet_id=digest, packet_digest=digest, **body)
        self._binding(request.access_context, request.request_binding, request_digest=request_digest, purpose=request.purpose, scope=request.scope, refs=request.exact_refs)
        self._persist(packet)
        return packet

    def read(self, request: CompanyGuidancePacketReadRequest) -> CompanyGuidancePacketReadResult:
        try:
            if request.packet_digest in self._tombstones():
                return CompanyGuidancePacketReadResult(status="packet_unavailable")
            packet = CompanyGuidancePacket.model_validate_json(self._file(request.packet_digest).read_bytes())
            if packet.packet_digest != request.packet_digest or packet.purpose != request.purpose or packet.scope != request.scope:
                return CompanyGuidancePacketReadResult(status="packet_unavailable")
            self._binding(request.access_context, request.request_binding, request_digest=packet.request_digest, purpose=request.purpose, scope=request.scope, refs=request.exact_refs)
            if not set(ref.exact_key for ref in packet.exact_lineage_refs) <= set(ref.exact_key for ref in request.exact_refs):
                return CompanyGuidancePacketReadResult(status="packet_unavailable")
            self._current(packet.exact_lineage_refs)
            self._binding(request.access_context, request.request_binding, request_digest=packet.request_digest, purpose=request.purpose, scope=request.scope, refs=request.exact_refs)
            return CompanyGuidancePacketReadResult(status="available", packet=packet, reauthorized_at=self.clock(), current_authorization_digest=request.access_context.context_digest)
        except (OSError, ValueError, PermissionError):
            return CompanyGuidancePacketReadResult(status="packet_unavailable")
