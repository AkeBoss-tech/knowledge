from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

import rail.hosted.access as hosted_access
import rail.hosted.repository as hosted_repository
from rail.hosted import (
    AccessClaims,
    AccessContextAuthority,
    AccessDenied,
    AuditUnavailable,
    CaptureRecord,
    CursorInvalid,
    FileObjectStore,
    GovernedHostedRepository,
    HostedRepository,
    IntegrityFailure,
    JsonMetadataStore,
    MemoryAuditLedger,
    MemoryMetadataStore,
    MemoryObjectStore,
    MemoryRevocationRegistry,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
KEY = b"deterministic-external-control-plane-key"
CAPABILITY_DIGEST = "sha256:" + "a" * 64
POLICY_DIGEST = "sha256:" + "b" * 64
ALL_ACTIONS = (
    "capture.write",
    "capture.read",
    "capture.list",
    "capture.erase",
    "retention.enforce",
    "projection.rebuild",
    "backup.create",
    "backup.restore",
)


def claims(
    *,
    tenant: str = "tenant-a",
    project: str = "project-a",
    actions=ALL_ACTIONS,
    sources=("*",),
    classifications=("public", "internal", "confidential", "restricted"),
    issued_at: datetime = NOW - timedelta(minutes=2),
    not_before: datetime = NOW - timedelta(minutes=1),
    expires_at: datetime = NOW + timedelta(hours=1),
    capability_digest: str = CAPABILITY_DIGEST,
    nonce: str = "nonce-1",
) -> AccessClaims:
    return AccessClaims(
        issuer="https://control.example.test",
        tenant_id=tenant,
        project_id=project,
        subject="agent/worker-7",
        delegator="user/alice",
        delegation_id="delegation/42",
        parent_delegation_digest="sha256:" + "c" * 64,
        capability_id="krail.hosted-repository",
        capability_version="1.0.0",
        capability_digest=capability_digest,
        actions=actions,
        source_ids=sources,
        classifications=classifications,
        policy_digest=POLICY_DIGEST,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        nonce=nonce,
    )


def governed(
    *,
    metadata=None,
    objects=None,
    tenant="tenant-a",
    project="project-a",
    revocations=None,
    audit=None,
    clock=lambda: NOW,
):
    authority = AccessContextAuthority(
        {"key-1": KEY},
        issuer="https://control.example.test",
        revocations=revocations,
        required_capability_id="krail.hosted-repository",
        required_capability_digest=CAPABILITY_DIGEST,
    )
    repository = HostedRepository(
        metadata or MemoryMetadataStore(),
        objects or MemoryObjectStore(),
        tenant_id=tenant,
        project_id=project,
    )
    ledger = audit or MemoryAuditLedger()
    service = GovernedHostedRepository(
        repository,
        authority,
        ledger,
        cursor_key=b"deterministic-cursor-key",
        clock=clock,
    )
    return service, authority, ledger


def issue(authority: AccessContextAuthority, value: AccessClaims | None = None):
    return authority.issue(value or claims(), key_id="key-1")


def capture(
    service,
    context,
    capture_id="capture-1",
    *,
    source="github",
    classification="internal",
    content=b"body",
    retention_until=None,
):
    return service.capture(
        context,
        capture_id,
        content,
        source_id=source,
        classification=classification,
        media_type="text/plain",
        created_at=NOW,
        retention_until=retention_until,
        idempotency_key=f"idem-{capture_id}",
    )


def test_signed_context_enforces_write_read_and_digest_only_audit():
    service, authority, audit = governed()
    context = issue(authority)
    record = capture(service, context)
    assert service.read_capture(context, record.capture_id)[1] == b"body"
    assert [event.action for event in audit.events] == ["capture.write", "capture.read"]
    serialized = json.dumps([event.model_dump(mode="json") for event in audit.events])
    for secret in ("tenant-a", "project-a", "agent/worker-7", "capture-1", "github"):
        assert secret not in serialized
    assert audit.events[1].previous_event_digest == audit.events[0].event_digest


@pytest.mark.parametrize(
    "failure", ["signature", "expired", "future", "capability", "tenant"]
)
def test_invalid_external_context_fails_closed_without_object_publication(failure):
    objects = MemoryObjectStore()
    service, authority, audit = governed(objects=objects)
    value = claims()
    if failure == "expired":
        value = claims(
            issued_at=NOW - timedelta(hours=3),
            not_before=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(hours=1),
        )
    elif failure == "future":
        value = claims(
            not_before=NOW + timedelta(minutes=1), expires_at=NOW + timedelta(hours=1)
        )
    elif failure == "capability":
        value = claims(capability_digest="sha256:" + "d" * 64)
    elif failure == "tenant":
        value = claims(tenant="tenant-b")
    context = issue(authority, value)
    if failure == "signature":
        context = context.model_copy(update={"signature": "sha256:" + "0" * 64})
    with pytest.raises(AccessDenied, match="^access denied$"):
        capture(service, context)
    assert objects.objects == {}
    assert audit.events[-1].decision == "denied"


@pytest.mark.parametrize("operation", ["read", "erase"])
def test_invalid_context_is_rejected_before_target_metadata_lookup(
    operation, monkeypatch
):
    service, authority, _ = governed()
    context = issue(authority).model_copy(update={"signature": "sha256:" + "d" * 64})

    def forbidden_lookup(_capture_id):
        raise AssertionError("target metadata was consulted before preauthorization")

    monkeypatch.setattr(service.repository, "capture_metadata", forbidden_lookup)
    with pytest.raises(AccessDenied):
        if operation == "read":
            service.read_capture(context, "possibly-hidden")
        else:
            service.erase(
                context,
                "possibly-hidden",
                erased_at=NOW,
                reason="privacy request",
                expected_revision=1,
            )


def test_source_and_classification_isolation_shape_counts_without_leaks():
    service, authority, _ = governed()
    admin = issue(authority)
    capture(service, admin, "visible", source="github", classification="internal")
    capture(service, admin, "hidden-source", source="slack", classification="internal")
    capture(
        service, admin, "hidden-class", source="github", classification="restricted"
    )
    restricted = issue(
        authority,
        claims(
            actions=("capture.read", "capture.list"),
            sources=("github",),
            classifications=("internal",),
            nonce="restricted",
        ),
    )
    page = service.list_captures(restricted)
    assert [item.capture_id for item in page.items] == ["visible"]
    assert page.authorization.model_dump() == {
        "shape": "authorized_projection",
        "omitted_count": 0,
        "count_precision": "undisclosed",
        "reason_codes": ("policy_filtered",),
    }
    for hidden in ("hidden-source", "hidden-class", "missing"):
        with pytest.raises(AccessDenied, match="^access denied$"):
            service.read_capture(restricted, hidden)


def test_opaque_cursor_is_context_scope_and_snapshot_bound():
    service, authority, _ = governed()
    admin = issue(authority)
    for item in ("a", "b", "c"):
        capture(service, admin, item)
    first = service.list_captures(admin, limit=2)
    assert first.next_cursor
    decoded_cursor = base64.urlsafe_b64decode(
        first.next_cursor + "=" * (-len(first.next_cursor) % 4)
    )[:-32]
    assert all(item not in decoded_cursor for item in (b'"a"', b'"b"', b'"c"'))
    assert [
        row.capture_id
        for row in service.list_captures(admin, cursor=first.next_cursor).items
    ] == ["c"]
    other = issue(authority, claims(nonce="other-context"))
    with pytest.raises(CursorInvalid):
        service.list_captures(other, cursor=first.next_cursor)
    with pytest.raises(CursorInvalid):
        service.list_captures(admin, cursor=first.next_cursor[:-1] + "A")
    capture(service, admin, "d")
    with pytest.raises(CursorInvalid):
        service.list_captures(admin, cursor=first.next_cursor)


def test_revocation_is_rechecked_after_prior_allowed_decision():
    revocations = MemoryRevocationRegistry()
    service, authority, _ = governed(revocations=revocations)
    context = issue(authority)
    capture(service, context)
    revocations.revoke_delegation("delegation/42", revoked_at=NOW)
    with pytest.raises(AccessDenied):
        service.read_capture(context, "capture-1")


def test_audit_outage_fails_closed_before_mutation_or_object_read():
    objects = MemoryObjectStore()
    audit = MemoryAuditLedger()
    service, authority, _ = governed(objects=objects, audit=audit)
    context = issue(authority)
    audit.available = False
    with pytest.raises(AuditUnavailable):
        capture(service, context)
    assert objects.objects == {}


def test_erasure_removes_policy_and_every_revision_object():
    service, authority, audit = governed()
    context = issue(authority)
    first = capture(
        service,
        context,
        source="private-source",
        classification="restricted",
        content=b"v1",
    )
    second = service.capture(
        context,
        "capture-1",
        b"v2",
        source_id="private-source",
        classification="restricted",
        media_type="text/plain",
        created_at=NOW,
        idempotency_key="idem-v2",
        expected_revision=1,
    )
    service.erase(
        context,
        "capture-1",
        erased_at=NOW,
        reason="privacy-request",
        expected_revision=2,
    )
    rows = service.repository.metadata.list("tenant-a", "project-a")
    capture_rows = [
        row for row in rows if row.record_kind in {"capture", "capture_revision"}
    ]
    assert all(row.payload["source_id"] is None for row in capture_rows)
    assert all(row.payload["classification"] is None for row in capture_rows)
    assert not service.repository.objects.contains(first.object_key)
    assert not service.repository.objects.contains(second.object_key)
    serialized = json.dumps([event.model_dump(mode="json") for event in audit.events])
    assert "private-source" not in serialized and "privacy-request" not in serialized


def test_administrative_operations_require_unrestricted_scope_and_preserve_backup_erasure():
    service, authority, _ = governed()
    admin = issue(authority)
    record = capture(service, admin, retention_until=NOW + timedelta(minutes=1))
    restricted = issue(
        authority,
        claims(
            actions=("backup.create", "projection.rebuild", "retention.enforce"),
            sources=("github",),
            classifications=("internal",),
            nonce="restricted-admin",
        ),
    )
    for call in (
        lambda: service.backup(restricted, created_at=NOW),
        lambda: service.rebuild_projection(
            restricted, "counts", lambda rows: {"count": len(rows)}, rebuilt_at=NOW
        ),
        lambda: service.enforce_retention(restricted, as_of=NOW + timedelta(hours=1)),
    ):
        with pytest.raises(AccessDenied):
            call()
    bundle = service.backup(admin, created_at=NOW)
    target, target_authority, _ = governed()
    target_admin = issue(target_authority)
    target.restore(target_admin, bundle)
    assert target.read_capture(target_admin, record.capture_id)[1] == b"body"
    assert target.enforce_retention(target_admin, as_of=NOW + timedelta(hours=1)) == [
        record.capture_id
    ]
    rows = target.repository.metadata.list(
        "tenant-a", "project-a", kind="capture_revision"
    )
    assert all(row.payload["state"] == "erased" for row in rows)


@pytest.mark.parametrize("adapter", ["memory", "json"])
def test_local_and_hosted_metadata_adapters_have_identical_governance_semantics(
    adapter, tmp_path
):
    if adapter == "memory":
        metadata, objects = MemoryMetadataStore(), MemoryObjectStore()
    else:
        metadata, objects = (
            JsonMetadataStore(tmp_path / "metadata.json"),
            FileObjectStore(tmp_path / "objects"),
        )
    service, authority, _ = governed(metadata=metadata, objects=objects)
    context = issue(authority)
    record = capture(service, context)
    assert service.read_capture(context, record.capture_id)[1] == b"body"
    assert service.list_captures(context).items == (record,)


def test_authorization_scan_is_bounded(monkeypatch):
    service, authority, _ = governed()
    context = issue(authority)
    capture(service, context)
    monkeypatch.setattr(hosted_access, "MAX_AUTHORIZATION_SCAN_RECORDS", 0)
    with pytest.raises(ValueError, match="scan bound"):
        service.list_captures(context)


def test_capture_idempotency_binds_source_and_classification():
    service, authority, _ = governed()
    context = issue(authority)
    capture(service, context)
    with pytest.raises(Exception, match="idempotency key"):
        service.repository.capture(
            "capture-1",
            b"body",
            source_id="other",
            classification="internal",
            media_type="text/plain",
            created_at=NOW,
            idempotency_key="idem-capture-1",
        )


def test_signed_context_rejects_duplicate_or_wildcard_mixed_sources():
    with pytest.raises(ValueError):
        claims(sources=("github", "github"))
    with pytest.raises(ValueError):
        claims(sources=("*", "github"))


def test_legacy_capture_payloads_are_quarantined_without_invented_authority():
    service, authority, _ = governed()
    context = issue(authority)
    record = capture(service, context)
    active = record.model_dump(mode="json", exclude={"source_id", "classification"})
    migrated = CaptureRecord.model_validate(active)
    assert (migrated.source_id, migrated.classification) == (
        "__legacy_unmapped__",
        "restricted",
    )
    erased = {
        **active,
        "resource_ref": None,
        "content_digest": None,
        "object_key": None,
        "media_type": None,
        "byte_size": None,
        "retention_until": None,
        "state": "erased",
        "erased_at": NOW.isoformat(),
        "erasure_reason_digest": "sha256:" + "f" * 64,
    }
    tombstone = CaptureRecord.model_validate(erased)
    assert tombstone.source_id is None and tombstone.classification is None


def test_audit_chain_rejects_tampering():
    service, authority, audit = governed()
    context = issue(authority)
    capture(service, context)
    changed = audit.events[0].model_copy(update={"event_digest": "sha256:" + "0" * 64})
    audit.events[0] = changed
    with pytest.raises(AuditUnavailable):
        service.read_capture(context, "capture-1")


def _redigest_bundle(bundle, **changes):
    updated = bundle.model_copy(update=changes)
    body = updated.model_dump(mode="json", exclude={"bundle_digest"})
    return updated.model_copy(
        update={
            "bundle_digest": hosted_repository._digest(
                hosted_repository._canonical(body)
            )
        }
    )


@pytest.mark.parametrize("failure", ["cross-scope", "missing-object", "extra-object"])
def test_restore_rejects_scope_and_object_inventory_before_publication(failure):
    source, source_authority, _ = governed()
    source_context = issue(source_authority)
    capture(source, source_context)
    bundle = source.backup(source_context, created_at=NOW)
    if failure == "cross-scope":
        rows = list(bundle.records)
        target_index = next(
            index for index, row in enumerate(rows) if row.record_kind == "capture"
        )
        row = rows[target_index]
        payload = {**row.payload, "tenant_id": "tenant-b"}
        rows[target_index] = row.model_copy(
            update={"tenant_id": "tenant-b", "payload": payload}
        )
        bundle = _redigest_bundle(bundle, records=rows)
    elif failure == "missing-object":
        bundle = _redigest_bundle(bundle, objects={})
    else:
        extra = b"unreferenced"
        key = hosted_repository._object_key(
            "tenant-a", "project-a", hosted_repository._digest(extra)
        )
        bundle = _redigest_bundle(
            bundle,
            objects={**bundle.objects, key: base64.b64encode(extra).decode("ascii")},
        )
    objects = MemoryObjectStore()
    target, target_authority, _ = governed(objects=objects)
    with pytest.raises(IntegrityFailure):
        target.restore(issue(target_authority), bundle)
    assert objects.objects == {}


def test_backup_rejects_corrupt_source_and_cache_contains_no_raw_policy_values():
    objects = MemoryObjectStore()
    service, authority, _ = governed(objects=objects)
    context = issue(authority)
    record = capture(
        service, context, source="private-source", classification="restricted"
    )
    assert all(
        "private-source" not in part for key in service._decision_cache for part in key
    )
    objects.objects[record.object_key] = b"corrupt"
    with pytest.raises(IntegrityFailure):
        service.backup(context, created_at=NOW)


def test_metadata_restore_failure_rolls_back_rows_without_claiming_object_atomicity():
    source, source_authority, _ = governed()
    source_context = issue(source_authority)
    capture(source, source_context)
    bundle = source.backup(source_context, created_at=NOW)

    class FailingMetadata(MemoryMetadataStore):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def restore(self, record):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("injected metadata failure")
            super().restore(record)

    metadata, objects = FailingMetadata(), MemoryObjectStore()
    target, target_authority, _ = governed(metadata=metadata, objects=objects)
    with pytest.raises(RuntimeError, match="injected"):
        target.restore(issue(target_authority), bundle)
    assert metadata.list("tenant-a", "project-a") == []
    assert objects.objects  # content-addressed, unreferenced upload is safe to collect
