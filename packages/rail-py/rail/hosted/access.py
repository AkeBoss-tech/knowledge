"""Fail-closed authorization and reliability boundary for hosted repositories.

KRAIL does not become an identity provider here.  A customer control plane
issues a bounded, signed capability/delegation context; this module verifies
that context on every operation and enforces its tenant, project, source, data
classification, action, expiry, and revocation constraints before object bytes
are read or durable metadata is changed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from threading import RLock
from typing import Annotated, Any, Literal, Protocol

import rfc8785
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from rail.hosted.models import (
    BackupBundle,
    CaptureRecord,
    DataClassification,
    ProjectionRecord,
)
from rail.hosted.repository import ConcurrencyConflict, HostedRepository

NonEmpty = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
Digest = Annotated[
    str, StringConstraints(to_lower=True, pattern=r"^sha256:[0-9a-f]{64}$")
]
Action = Literal[
    "context.read",
    "procedure.review",
    "procedure.invalidate",
    "projection.write",
    "capture.write",
    "capture.read",
    "capture.list",
    "capture.erase",
    "retention.enforce",
    "projection.rebuild",
    "backup.create",
    "backup.restore",
]
ALL_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"public", "internal", "confidential", "restricted"}
)
MAX_PAGE_SIZE = 100
MAX_AUTHORIZATION_SCAN_RECORDS = 10_000
MAX_DECISION_CACHE_ENTRIES = 1_024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical(value: object) -> bytes:
    return rfc8785.dumps(value)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _model_bytes(value: BaseModel) -> bytes:
    return _canonical(value.model_dump(mode="json"))


def _utc_timestamp(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authorization timestamps must include a timezone")


class AccessDenied(PermissionError):
    """Opaque denial that never reveals whether a target exists."""

    def __init__(self) -> None:
        super().__init__("access denied")


class InvalidAccessContext(AccessDenied):
    pass


class CursorInvalid(ValueError):
    pass


class AuditUnavailable(RuntimeError):
    pass


class AccessClaims(StrictModel):
    schema_version: Literal["krail.external-access-claims.v1"] = (
        "krail.external-access-claims.v1"
    )
    issuer: NonEmpty
    tenant_id: NonEmpty
    project_id: NonEmpty
    subject: NonEmpty
    delegator: NonEmpty
    delegation_id: NonEmpty
    parent_delegation_digest: Digest | None = None
    capability_id: NonEmpty
    capability_version: NonEmpty
    capability_digest: Digest
    actions: tuple[Action, ...] = Field(min_length=1, max_length=16)
    source_ids: tuple[NonEmpty, ...] = Field(min_length=1, max_length=256)
    classifications: tuple[DataClassification, ...] = Field(min_length=1, max_length=4)
    policy_digest: Digest
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    nonce: NonEmpty

    @field_validator("issued_at", "not_before", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        _utc_timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> AccessClaims:
        if not self.issued_at <= self.not_before < self.expires_at:
            raise ValueError("authorization validity interval is invalid")
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("authorization actions must be unique")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("authorization source IDs must be unique")
        if "*" in self.source_ids and self.source_ids != ("*",):
            raise ValueError("wildcard source scope cannot be combined with source IDs")
        if len(set(self.classifications)) != len(self.classifications):
            raise ValueError("authorization classifications must be unique")
        return self


class SignedAccessContext(StrictModel):
    schema_version: Literal["krail.signed-access-context.v1"] = (
        "krail.signed-access-context.v1"
    )
    claims: AccessClaims
    key_id: NonEmpty
    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    signature: Digest

    @property
    def context_digest(self) -> str:
        return _digest(_model_bytes(self))


class PacketRequestBinding(StrictModel):
    """Caller-authority signature scope for one immutable packet request."""

    schema_version: Literal["krail.packet-request-binding.v1"] = (
        "krail.packet-request-binding.v1"
    )
    access_context_digest: Digest
    tenant_id: NonEmpty
    project_id: NonEmpty
    capability_id: NonEmpty
    capability_version: NonEmpty
    capability_digest: Digest
    request_digest: Digest
    purpose: NonEmpty
    scope: NonEmpty
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    nonce: NonEmpty

    @field_validator("issued_at", "not_before", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        _utc_timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> "PacketRequestBinding":
        if not self.issued_at <= self.not_before < self.expires_at:
            raise ValueError("packet request binding validity interval is invalid")
        return self


class SignedPacketRequestBinding(StrictModel):
    schema_version: Literal["krail.signed-packet-request-binding.v1"] = (
        "krail.signed-packet-request-binding.v1"
    )
    binding: PacketRequestBinding
    key_id: NonEmpty
    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    signature: Digest

    @property
    def binding_digest(self) -> str:
        return _digest(_model_bytes(self))


class RevocationRegistry(Protocol):
    def is_revoked(
        self, context_digest: str, delegation_id: str, *, as_of: datetime
    ) -> bool: ...


class MemoryRevocationRegistry:
    """Deterministic fake; production composes an external revocation adapter."""

    def __init__(self) -> None:
        self._contexts: dict[str, datetime] = {}
        self._delegations: dict[str, datetime] = {}

    def revoke_context(self, context_digest: str, *, revoked_at: datetime) -> None:
        _utc_timestamp(revoked_at)
        self._contexts[context_digest] = revoked_at

    def revoke_delegation(self, delegation_id: str, *, revoked_at: datetime) -> None:
        _utc_timestamp(revoked_at)
        self._delegations[delegation_id] = revoked_at

    def is_revoked(
        self, context_digest: str, delegation_id: str, *, as_of: datetime
    ) -> bool:
        return any(
            revoked_at <= as_of
            for revoked_at in (
                self._contexts.get(context_digest),
                self._delegations.get(delegation_id),
            )
            if revoked_at is not None
        )


class AccessContextAuthority:
    """HMAC reference authority for local parity and deterministic tests.

    Customer-hosted deployments should inject keys resolved from their control
    plane or replace this verifier at the composition root.  Keys are never
    loaded from KRAIL project files or serialized into a context.
    """

    def __init__(
        self,
        keys: Mapping[str, bytes],
        *,
        issuer: str,
        revocations: RevocationRegistry | None = None,
        required_capability_id: str | None = None,
        required_capability_digest: str | None = None,
    ) -> None:
        if not keys or any(not key for key in keys.values()):
            raise ValueError("at least one non-empty verification key is required")
        self._keys = {name: bytes(key) for name, key in keys.items()}
        self.issuer = issuer
        self.revocations = revocations or MemoryRevocationRegistry()
        self.required_capability_id = required_capability_id
        self.required_capability_digest = required_capability_digest

    def issue(self, claims: AccessClaims, *, key_id: str) -> SignedAccessContext:
        key = self._keys.get(key_id)
        if key is None or claims.issuer != self.issuer:
            raise InvalidAccessContext()
        signature = (
            "sha256:" + hmac.new(key, _model_bytes(claims), hashlib.sha256).hexdigest()
        )
        return SignedAccessContext(claims=claims, key_id=key_id, signature=signature)

    def issue_packet_request_binding(
        self, binding: PacketRequestBinding, *, key_id: str
    ) -> SignedPacketRequestBinding:
        key = self._keys.get(key_id)
        if key is None:
            raise InvalidAccessContext()
        signature = (
            "sha256:"
            + hmac.new(key, _model_bytes(binding), hashlib.sha256).hexdigest()
        )
        return SignedPacketRequestBinding(
            binding=binding, key_id=key_id, signature=signature
        )

    def verify_packet_request_binding(
        self,
        signed: SignedPacketRequestBinding,
        *,
        access_context: SignedAccessContext,
        as_of: datetime,
    ) -> PacketRequestBinding:
        _utc_timestamp(as_of)
        key = self._keys.get(signed.key_id)
        binding = signed.binding
        if key is None:
            raise InvalidAccessContext()
        observed = (
            "sha256:"
            + hmac.new(key, _model_bytes(binding), hashlib.sha256).hexdigest()
        )
        if (
            not hmac.compare_digest(observed, signed.signature)
            or binding.access_context_digest != access_context.context_digest
            or as_of < binding.not_before
            or as_of >= binding.expires_at
        ):
            raise InvalidAccessContext()
        return binding

    def verify(self, context: SignedAccessContext, *, as_of: datetime) -> AccessClaims:
        _utc_timestamp(as_of)
        key = self._keys.get(context.key_id)
        claims = context.claims
        if key is None or claims.issuer != self.issuer:
            raise InvalidAccessContext()
        observed = (
            "sha256:" + hmac.new(key, _model_bytes(claims), hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(observed, context.signature):
            raise InvalidAccessContext()
        if as_of < claims.not_before or as_of >= claims.expires_at:
            raise InvalidAccessContext()
        if (
            self.required_capability_id
            and claims.capability_id != self.required_capability_id
        ):
            raise InvalidAccessContext()
        if (
            self.required_capability_digest
            and claims.capability_digest != self.required_capability_digest
        ):
            raise InvalidAccessContext()
        if self.revocations.is_revoked(
            context.context_digest, claims.delegation_id, as_of=as_of
        ):
            raise InvalidAccessContext()
        return claims


class AuditEvent(StrictModel):
    schema_version: Literal["krail.access-audit.v1"] = "krail.access-audit.v1"
    sequence: int = Field(ge=1)
    occurred_at: datetime
    context_digest: Digest
    scope_digest: Digest
    subject_digest: Digest
    target_digest: Digest
    action: Action
    decision: Literal["allowed", "denied"]
    reason_code: Literal[
        "capability-allowed",
        "invalid-context",
        "scope-denied",
        "action-denied",
        "source-denied",
        "classification-denied",
    ]
    previous_event_digest: Digest | None = None
    event_digest: Digest

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        _utc_timestamp(value)
        return value


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> None: ...
    def tail(self) -> AuditEvent | None: ...


class MemoryAuditLedger:
    """Hash-chained, content-free audit fake with fail-closed injection."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.available = True
        self._lock = RLock()

    def tail(self) -> AuditEvent | None:
        return self.events[-1] if self.events else None

    def append(self, event: AuditEvent) -> None:
        if not self.available:
            raise AuditUnavailable("authorization audit sink is unavailable")
        with self._lock:
            prior = self.tail()
            if prior is not None:
                prior_body = prior.model_dump(mode="json", exclude={"event_digest"})
                if prior.event_digest != _digest(_canonical(prior_body)):
                    raise AuditUnavailable("authorization audit chain is inconsistent")
            expected_sequence = 1 if prior is None else prior.sequence + 1
            expected_previous = None if prior is None else prior.event_digest
            body = event.model_dump(mode="json", exclude={"event_digest"})
            if (
                event.sequence != expected_sequence
                or event.previous_event_digest != expected_previous
            ):
                raise AuditUnavailable("authorization audit chain is inconsistent")
            if event.event_digest != _digest(_canonical(body)):
                raise AuditUnavailable("authorization audit digest is invalid")
            self.events.append(event)


class AuthorizationSummary(StrictModel):
    shape: Literal["authorized_projection"] = "authorized_projection"
    omitted_count: Literal[0] = 0
    count_precision: Literal["undisclosed"] = "undisclosed"
    reason_codes: tuple[Literal["policy_filtered"], ...] = ()


class CapturePage(StrictModel):
    schema_version: Literal["krail.authorized-capture-page.v1"] = (
        "krail.authorized-capture-page.v1"
    )
    items: tuple[CaptureRecord, ...]
    next_cursor: str | None = None
    authorization: AuthorizationSummary


class GovernedHostedRepository:
    """Authorization-enforcing facade shared by memory, JSON, and Postgres stores."""

    def __init__(
        self,
        repository: HostedRepository,
        authority: AccessContextAuthority,
        audit: AuditSink,
        *,
        cursor_key: bytes,
        clock: Callable[[], datetime],
    ) -> None:
        if not cursor_key:
            raise ValueError("cursor key is required")
        self.repository = repository
        self.authority = authority
        self.audit = audit
        self.cursor_key = bytes(cursor_key)
        self.clock = clock
        self._decision_cache: dict[tuple[str, str, str, str], bool] = {}

    @staticmethod
    def _opaque_digest(value: str) -> str:
        return _digest(value.encode("utf-8"))

    def _record_audit(
        self,
        *,
        context_digest: str,
        claims: AccessClaims | None,
        action: Action,
        target: str,
        decision: Literal["allowed", "denied"],
        reason_code: str,
        occurred_at: datetime,
    ) -> None:
        prior = self.audit.tail()
        body: dict[str, Any] = {
            "schema_version": "krail.access-audit.v1",
            "sequence": 1 if prior is None else prior.sequence + 1,
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
            "context_digest": context_digest,
            "scope_digest": self._opaque_digest(
                f"{self.repository.tenant_id}\0{self.repository.project_id}"
            ),
            "subject_digest": self._opaque_digest(
                claims.subject if claims else "invalid"
            ),
            "target_digest": self._opaque_digest(target),
            "action": action,
            "decision": decision,
            "reason_code": reason_code,
            "previous_event_digest": None if prior is None else prior.event_digest,
        }
        event = AuditEvent(**body, event_digest=_digest(_canonical(body)))
        self.audit.append(event)

    def _authorize(
        self,
        context: SignedAccessContext,
        action: Action,
        *,
        source_id: str,
        classification: str,
        target: str,
        require_unrestricted: bool = False,
    ) -> AccessClaims:
        now = self.clock()
        try:
            claims = self.authority.verify(context, as_of=now)
        except AccessDenied:
            try:
                self._record_audit(
                    context_digest=context.context_digest,
                    claims=None,
                    action=action,
                    target=target,
                    decision="denied",
                    reason_code="invalid-context",
                    occurred_at=now,
                )
            finally:
                raise AccessDenied()
        reason = "capability-allowed"
        allowed = True
        if (claims.tenant_id, claims.project_id) != (
            self.repository.tenant_id,
            self.repository.project_id,
        ):
            allowed, reason = False, "scope-denied"
        elif action not in claims.actions:
            allowed, reason = False, "action-denied"
        elif (
            source_id != "*"
            and "*" not in claims.source_ids
            and source_id not in claims.source_ids
        ):
            allowed, reason = False, "source-denied"
        elif classification != "*" and classification not in claims.classifications:
            allowed, reason = False, "classification-denied"
        elif require_unrestricted and (
            claims.source_ids != ("*",)
            or frozenset(claims.classifications) != ALL_CLASSIFICATIONS
        ):
            allowed, reason = False, "scope-denied"
        cache_key = (
            context.context_digest,
            action,
            self._opaque_digest(source_id),
            self._opaque_digest(classification),
        )
        if len(self._decision_cache) >= MAX_DECISION_CACHE_ENTRIES:
            self._decision_cache.clear()
        self._decision_cache[cache_key] = allowed
        if not allowed:
            try:
                self._record_audit(
                    context_digest=context.context_digest,
                    claims=claims,
                    action=action,
                    target=target,
                    decision="denied",
                    reason_code=reason,
                    occurred_at=now,
                )
            except AuditUnavailable:
                # Preserve one fail-closed surface for authorized, denied, and
                # absent targets while the required audit sink is unavailable.
                # Masking this only for denied targets creates an existence
                # oracle because an allowed existing target exposes the outage.
                raise
            raise AccessDenied()
        self._record_audit(
            context_digest=context.context_digest,
            claims=claims,
            action=action,
            target=target,
            decision="allowed",
            reason_code="capability-allowed",
            occurred_at=now,
        )
        return claims

    def _preauthorize_context_scope_action(
        self,
        context: SignedAccessContext,
        action: Action,
        *,
        target: str,
    ) -> None:
        """Reject invalid callers before target-dependent metadata access."""
        now = self.clock()
        try:
            claims = self.authority.verify(context, as_of=now)
        except AccessDenied:
            try:
                self._record_audit(
                    context_digest=context.context_digest,
                    claims=None,
                    action=action,
                    target=target,
                    decision="denied",
                    reason_code="invalid-context",
                    occurred_at=now,
                )
            finally:
                raise AccessDenied()
        reason = None
        if (claims.tenant_id, claims.project_id) != (
            self.repository.tenant_id,
            self.repository.project_id,
        ):
            reason = "scope-denied"
        elif action not in claims.actions:
            reason = "action-denied"
        if reason is not None:
            try:
                self._record_audit(
                    context_digest=context.context_digest,
                    claims=claims,
                    action=action,
                    target=target,
                    decision="denied",
                    reason_code=reason,
                    occurred_at=now,
                )
            finally:
                raise AccessDenied()

    def capture(
        self,
        context: SignedAccessContext,
        capture_id: str,
        content: bytes,
        *,
        source_id: str,
        classification: DataClassification,
        media_type: str,
        created_at: datetime,
        idempotency_key: str,
        retention_until: datetime | None = None,
        expected_revision: int = 0,
    ) -> CaptureRecord:
        self._preauthorize_context_scope_action(
            context, "capture.write", target=capture_id
        )
        self._authorize(
            context,
            "capture.write",
            source_id=source_id,
            classification=classification,
            target=capture_id,
        )
        try:
            current = self.repository.capture_metadata(capture_id)
        except KeyError:
            if expected_revision > 0:
                self._authorize(
                    context,
                    "capture.write",
                    source_id="*",
                    classification="*",
                    target=capture_id,
                    require_unrestricted=True,
                )
        else:
            self._authorize(
                context,
                "capture.write",
                source_id=current.source_id or "*",
                classification=current.classification or "*",
                target=capture_id,
            )
            if expected_revision != current.revision:
                raise ConcurrencyConflict(
                    f"expected revision {expected_revision}, "
                    f"found {current.revision}"
                )
        return self.repository.capture(
            capture_id,
            content,
            media_type=media_type,
            created_at=created_at,
            retention_until=retention_until,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            source_id=source_id,
            classification=classification,
        )

    def read_capture(
        self, context: SignedAccessContext, capture_id: str
    ) -> tuple[CaptureRecord, bytes]:
        self._preauthorize_context_scope_action(
            context, "capture.read", target=capture_id
        )
        try:
            capture = self.repository.capture_metadata(capture_id)
        except KeyError:
            self._authorize(
                context,
                "capture.read",
                source_id="*",
                classification="*",
                target=capture_id,
                require_unrestricted=True,
            )
            raise
        self._authorize(
            context,
            "capture.read",
            source_id=capture.source_id or "*",
            classification=capture.classification or "*",
            target=capture_id,
        )
        return self.repository.read_capture_record(capture)

    def erase(
        self,
        context: SignedAccessContext,
        capture_id: str,
        *,
        erased_at: datetime,
        reason: str,
        expected_revision: int,
    ) -> None:
        self._preauthorize_context_scope_action(
            context, "capture.erase", target=capture_id
        )
        try:
            capture = self.repository.capture_metadata(capture_id)
        except KeyError:
            self._authorize(
                context,
                "capture.erase",
                source_id="*",
                classification="*",
                target=capture_id,
                require_unrestricted=True,
            )
            return
        self._authorize(
            context,
            "capture.erase",
            source_id=capture.source_id or "*",
            classification=capture.classification or "*",
            target=capture_id,
        )
        if expected_revision != capture.revision:
            raise ConcurrencyConflict(
                f"expected revision {expected_revision}, found {capture.revision}"
            )
        self.repository.erase(
            capture_id,
            erased_at=erased_at,
            reason=reason,
            expected_revision=expected_revision,
        )

    def _snapshot_digest(self, rows: list[CaptureRecord]) -> str:
        return _digest(
            _canonical(
                [
                    {
                        "resource": row.resource_ref.model_dump(mode="json"),
                        "revision": row.revision,
                    }
                    for row in rows
                ]
            )
        )

    def _encode_cursor(self, body: dict[str, Any]) -> str:
        payload = _canonical(body)
        signature = hmac.new(self.cursor_key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")

    def _decode_cursor(self, cursor: str) -> dict[str, Any]:
        if len(cursor) > 2_048:
            raise CursorInvalid("cursor is invalid or stale")
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, signature = raw[:-32], raw[-32:]
            expected = hmac.new(self.cursor_key, payload, hashlib.sha256).digest()
            if len(signature) != 32 or not hmac.compare_digest(signature, expected):
                raise ValueError
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise TypeError
            return value
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CursorInvalid("cursor is invalid or stale") from exc

    def list_captures(
        self,
        context: SignedAccessContext,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> CapturePage:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        claims = self._authorize(
            context,
            "capture.list",
            source_id="*",
            classification="*",
            target="capture-page",
        )
        visible = self.repository.capture_records_for_scope(
            source_ids=claims.source_ids,
            classifications=claims.classifications,
            max_records=MAX_AUTHORIZATION_SCAN_RECORDS,
        )
        if len(visible) > MAX_AUTHORIZATION_SCAN_RECORDS:
            raise ValueError("authorization scan bound exceeded")
        snapshot = self._snapshot_digest(visible)
        offset = 0
        if cursor is not None:
            value = self._decode_cursor(cursor)
            expected = {
                "context_digest": context.context_digest,
                "scope_digest": self._opaque_digest(
                    f"{self.repository.tenant_id}\0{self.repository.project_id}"
                ),
                "snapshot_digest": snapshot,
            }
            if any(
                value.get(key) != expected_value
                for key, expected_value in expected.items()
            ):
                raise CursorInvalid("cursor is invalid or stale")
            offset = value.get("offset")
            if not isinstance(offset, int) or offset < 0 or offset > len(visible):
                raise CursorInvalid("cursor is invalid or stale")
        page = visible[offset : offset + limit]
        next_offset = offset + len(page)
        next_cursor = None
        if next_offset < len(visible):
            next_cursor = self._encode_cursor(
                {
                    "context_digest": context.context_digest,
                    "scope_digest": self._opaque_digest(
                        f"{self.repository.tenant_id}\0{self.repository.project_id}"
                    ),
                    "snapshot_digest": snapshot,
                    "offset": next_offset,
                }
            )
        return CapturePage(
            items=tuple(page),
            next_cursor=next_cursor,
            authorization=AuthorizationSummary(
                reason_codes=("policy_filtered",)
            ),
        )

    def backup(
        self, context: SignedAccessContext, *, created_at: datetime
    ) -> BackupBundle:
        self._authorize(
            context,
            "backup.create",
            source_id="*",
            classification="*",
            target="backup",
            require_unrestricted=True,
        )
        return self.repository.backup(created_at=created_at)

    def restore(self, context: SignedAccessContext, bundle: BackupBundle) -> None:
        self._authorize(
            context,
            "backup.restore",
            source_id="*",
            classification="*",
            target="restore",
            require_unrestricted=True,
        )
        self.repository.restore(bundle)

    def rebuild_projection(
        self,
        context: SignedAccessContext,
        projection_id: str,
        builder: Callable[[list[CaptureRecord]], dict[str, Any]],
        *,
        rebuilt_at: datetime,
        projection_kind: str = "search",
    ) -> ProjectionRecord:
        self._authorize(
            context,
            "projection.rebuild",
            source_id="*",
            classification="*",
            target=projection_id,
            require_unrestricted=True,
        )
        return self.repository.rebuild_projection(
            projection_id,
            builder,
            rebuilt_at=rebuilt_at,
            projection_kind=projection_kind,
        )

    def enforce_retention(
        self, context: SignedAccessContext, *, as_of: datetime
    ) -> list[str]:
        self._authorize(
            context,
            "retention.enforce",
            source_id="*",
            classification="*",
            target="retention",
            require_unrestricted=True,
        )
        return self.repository.enforce_retention(as_of=as_of)
