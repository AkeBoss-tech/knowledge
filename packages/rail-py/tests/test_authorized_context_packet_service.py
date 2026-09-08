from __future__ import annotations

import json
import multiprocessing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from krail.provider.v1 import GetResourceRequest, ResourceRef
from rail.authorized_context import (
    AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
    AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
    MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONES,
    MAX_AUTHORIZED_CONTEXT_PACKET_BYTES,
    AuthorizedContextPacketTombstone,
    AuthorizedContextPacketCreateRequest,
    AuthorizedContextPacketReadRequest,
    AuthorizedContextPacketReadResult,
    authorized_context_packet_request_digest,
)
from rail.bootstrap import bootstrap_future_project
from rail.capability_publication import authorized_context_packet_descriptor
from rail.context_brief import ContextBriefRequest
from rail.hosted.access import (
    AccessClaims,
    AccessContextAuthority,
    MemoryRevocationRegistry,
    PacketRequestBinding,
    SignedAccessContext,
)
from rail.local import LocalEngine
from rail.project import Project


NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
PURPOSE = "grounded coding run R"
SCOPE = "project-a"
POLICY_DIGEST = "sha256:" + "d" * 64


def _invalidate_packet_cache_process(root, access_context_json, refs_json, ready, start, result):
    """Subprocess adapter used only to exercise the real local lifecycle lock."""
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project = Project(slug="shared-packet", backend=LocalEngine(project_path=root))
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    ready.put(True)
    start.wait(10)
    try:
        service = project._backend.knowledge.application.authorized_context_packets
        result.put(("ok", service.invalidate_for_sources(
            SignedAccessContext.model_validate_json(access_context_json),
            exact_refs=tuple(ResourceRef.model_validate(item) for item in refs_json),
            reason="source-deleted",
        )))
    except Exception as exc:  # surfaced in parent assertion, never silently ignored
        result.put(("error", repr(exc)))


def _project(tmp_path: Path):
    root = bootstrap_future_project(
        tmp_path, name="Shared Packet", slug="shared-packet"
    )
    repository_path = root / "docs" / "repository.md"
    repository_path.parent.mkdir(parents=True, exist_ok=True)
    repository_path.write_text(
        "# Repository\n\nShared packet architecture and public evidence.\n",
        encoding="utf-8",
    )
    issue_path = root / "topics" / "issue.md"
    issue_path.parent.mkdir(parents=True, exist_ok=True)
    issue_path.write_text(
        "# Issue\n\nBuild the shared packet from cited evidence.\n",
        encoding="utf-8",
    )
    hidden_path = root / "sources" / "private.md"
    hidden_path.write_text(
        "# Private\n\nNeedleWide evidence contains Private-Zebra-42.\n",
        encoding="utf-8",
    )
    project = Project(slug="shared-packet", backend=LocalEngine(project_path=root))
    provider = project.provider
    refs = (
        provider._ref("docs/repository.md"),
        provider._ref("topics/issue.md"),
        provider._ref("sources/private.md"),
    )
    return root, project, refs


def _context_request(refs, *, max_total_bytes: int = 4096, query: str = "NeedleWide"):
    return ContextBriefRequest(
        repository=refs[0],
        issue=refs[1],
        evaluated_at=NOW,
        query=query,
        max_items=8,
        max_total_bytes=max_total_bytes,
        authorization_omission=True,
    )


def _signed_request(
    authority,
    descriptor,
    refs,
    context_request,
    *,
    now=NOW,
    expires_at=None,
    purpose=PURPOSE,
    scope=SCOPE,
    tenant_id="tenant-a",
    project_id="project-a",
    capability_version=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION,
    capability_digest=None,
    max_context_tokens=32_768,
    nonce="wide",
    actions=("context.read",),
    source_ids=None,
    subject="user/alice",
):
    capability_digest = capability_digest or descriptor.descriptor_digest
    expires_at = expires_at or now + timedelta(hours=1)
    claims = AccessClaims(
        issuer="https://control.example.test",
        tenant_id=tenant_id,
        project_id=project_id,
        subject=subject,
        delegator=subject,
        delegation_id=f"delegation/{nonce}",
        capability_id=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
        capability_version=capability_version,
        capability_digest=capability_digest,
        actions=actions,
        source_ids=source_ids or tuple(ref.resource_id for ref in refs),
        classifications=("internal",),
        policy_digest=POLICY_DIGEST,
        issued_at=now - timedelta(minutes=2),
        not_before=now - timedelta(minutes=1),
        expires_at=expires_at,
        nonce=f"context/{nonce}",
    )
    context = authority.issue(claims, key_id="core")
    request_digest = authorized_context_packet_request_digest(
        exact_refs=refs,
        context_request=context_request,
        purpose=purpose,
        scope=scope,
        max_context_tokens=max_context_tokens,
    )
    binding = authority.issue_packet_request_binding(
        PacketRequestBinding(
            access_context_digest=context.context_digest,
            tenant_id=tenant_id,
            project_id=project_id,
            capability_id=AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID,
            capability_version=capability_version,
            capability_digest=capability_digest,
            request_digest=request_digest,
            purpose=purpose,
            scope=scope,
            issued_at=now - timedelta(minutes=2),
            not_before=now - timedelta(minutes=1),
            expires_at=expires_at,
            nonce=f"binding/{nonce}",
        ),
        key_id="core",
    )
    return context, binding


def _create_request(refs, context_request, context, binding, **updates):
    values = {
        "access_context": context,
        "request_binding": binding,
        "exact_refs": refs,
        "context_request": context_request,
        "purpose": PURPOSE,
        "scope": SCOPE,
        "max_context_tokens": 32_768,
    }
    values.update(updates)
    return AuthorizedContextPacketCreateRequest(**values)


def _read_request(packet, refs, context, binding, **updates):
    values = {
        "packet_digest": packet.packet_digest,
        "access_context": context,
        "request_binding": binding,
        "exact_refs": refs,
        "query": packet.context.evidence.query,
        "purpose": PURPOSE,
        "scope": SCOPE,
    }
    values.update(updates)
    return AuthorizedContextPacketReadRequest(**values)


def test_provider_creates_narrow_and_wide_packets_without_hidden_leak(tmp_path):
    _root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project._backend.knowledge.application.configure_authorized_context_packets(
        authority,
        tenant_id="tenant-a",
        project_id="project-a",
        clock=lambda: NOW,
    )
    request = _context_request(refs)
    narrow_refs = refs[:2]
    narrow_context, narrow_binding = _signed_request(
        authority,
        descriptor,
        narrow_refs,
        request,
        nonce="narrow-bob",
        subject="user/bob",
    )
    wide_context, wide_binding = _signed_request(
        authority, descriptor, refs, request, nonce="wide"
    )

    narrow = project.provider.create_authorized_context_packet(
        _create_request(narrow_refs, request, narrow_context, narrow_binding)
    )
    wide = project.provider.create_authorized_context_packet(
        _create_request(refs, request, wide_context, wide_binding)
    )

    narrow_json = narrow.model_dump_json()
    assert narrow.packet_id == narrow.packet_digest
    assert len(narrow.context.omissions) == 1
    assert "Private-Zebra-42" not in narrow_json
    assert "sources/private.md" not in narrow_json
    assert refs[2].resource_id not in narrow_json
    assert "Private-Zebra-42" in wide.model_dump_json()
    assert refs[2] in wide.exact_evidence_refs
    assert narrow_context.claims.subject == "user/bob"
    assert wide_context.claims.subject == "user/alice"
    assert wide.authorization_digest == wide_context.context_digest
    assert wide.canonical_context_utf8_bytes == len(
        json.dumps(
            wide.context.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert wide.context_token_upper_bound == wide.canonical_context_utf8_bytes
    assert wide.canonical_packet_utf8_bytes == len(
        json.dumps(
            wide.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert wide.truncation_uncertainty


def test_expired_original_context_can_reread_byte_identical_packet_with_fresh_wide_grant(tmp_path):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"},
        issuer="https://control.example.test",
        revocations=revocations,
    )
    current = [NOW]
    project._backend.knowledge.application.configure_authorized_context_packets(
        authority,
        tenant_id="tenant-a",
        project_id="project-a",
        clock=lambda: current[0],
    )
    context_request = _context_request(refs)
    original_context, original_binding = _signed_request(
        authority,
        descriptor,
        refs,
        context_request,
        expires_at=NOW + timedelta(minutes=5),
        nonce="original",
    )
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, context_request, original_context, original_binding)
    )
    stored = (
        root
        / ".krail"
        / "authorized-context-packets"
        / f"{packet.packet_digest.removeprefix('sha256:')}.json"
    ).read_bytes()

    current[0] = NOW + timedelta(hours=2)
    fresh_context, fresh_binding = _signed_request(
        authority,
        descriptor,
        refs,
        context_request,
        now=current[0],
        nonce="fresh-wide-bob",
        subject="user/bob",
    )
    result = project.provider.read_authorized_context_packet(
        _read_request(packet, refs, fresh_context, fresh_binding)
    )

    assert result.status == "available"
    assert result.packet == packet
    assert result.packet.authorization_digest == original_context.context_digest
    assert original_context.claims.subject == "user/alice"
    assert fresh_context.claims.subject == "user/bob"
    assert result.reauthorization.current_authorization_digest == fresh_context.context_digest
    assert stored == json.dumps(
        packet.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    restarted = Project(slug="shared-packet", backend=LocalEngine(project_path=root))
    restarted._backend.knowledge.application.configure_authorized_context_packets(
        authority,
        tenant_id="tenant-a",
        project_id="project-a",
        clock=lambda: current[0],
    )
    replay = restarted.provider.read_authorized_context_packet(
        _read_request(packet, refs, fresh_context, fresh_binding)
    )
    assert replay.status == "available"
    assert replay.packet == packet


def test_fresh_narrow_grant_revocation_and_source_revision_return_coarse_unavailable(tmp_path):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"},
        issuer="https://control.example.test",
        revocations=revocations,
    )
    current = [NOW]
    current_heads = {ref.resource_id: ref for ref in refs}
    project._backend.knowledge.application.configure_authorized_context_packets(
        authority,
        tenant_id="tenant-a",
        project_id="project-a",
        clock=lambda: current[0],
        current_ref_resolver=lambda resource_id: current_heads[resource_id],
    )
    context_request = _context_request(refs)
    wide_context, wide_binding = _signed_request(
        authority, descriptor, refs, context_request, nonce="initial-wide"
    )
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, context_request, wide_context, wide_binding)
    )
    current[0] = NOW + timedelta(minutes=10)
    narrow_context, narrow_binding = _signed_request(
        authority,
        descriptor,
        refs[:2],
        context_request,
        now=current[0],
        nonce="fresh-narrow",
        subject="user/bob",
    )
    narrow = project.provider.read_authorized_context_packet(
        _read_request(packet, refs[:2], narrow_context, narrow_binding)
    )
    assert narrow.status == "context_packet_unavailable"
    assert narrow.model_dump_json() == (
        '{"schema_version":"krail.authorized-context-packet-read-result.v1",'
        '"status":"context_packet_unavailable","packet":null,"reauthorization":null}'
    )

    fresh_context, fresh_binding = _signed_request(
        authority, descriptor, refs, context_request, now=current[0], nonce="revoked-wide"
    )
    revocations.revoke_context(fresh_context.context_digest, revoked_at=current[0])
    revoked = project.provider.read_authorized_context_packet(
        _read_request(packet, refs, fresh_context, fresh_binding)
    )
    assert revoked == narrow

    current[0] += timedelta(minutes=1)
    replacement_context, replacement_binding = _signed_request(
        authority, descriptor, refs, context_request, now=current[0], nonce="changed-source"
    )
    del current_heads[refs[2].resource_id]
    missing_head = project.provider.read_authorized_context_packet(
        _read_request(packet, refs, replacement_context, replacement_binding)
    )
    assert missing_head == narrow
    current_heads[refs[2].resource_id] = refs[2]
    current_heads[refs[2].resource_id] = refs[2].model_copy(
        update={
            "version": "content:" + "1" * 64,
            "digest": "sha256:" + "1" * 64,
        }
    )
    retained = project.provider.get_resource(
        GetResourceRequest(ref=refs[2], max_bytes=4096)
    )
    assert "Private-Zebra-42" in retained.resource.content
    changed = project.provider.read_authorized_context_packet(
        _read_request(packet, refs, replacement_context, replacement_binding)
    )
    assert changed == narrow
    assert "Private-Zebra-42" not in changed.model_dump_json()


@pytest.mark.parametrize(
    "claims_update",
    [
        {"tenant_id": "tenant-b"},
        {"project_id": "project-b"},
        {"capability_version": "9.0.0"},
        {"capability_digest": "sha256:" + "0" * 64},
        {"actions": ("capture.read",)},
        {"source_ids": ("*",)},
    ],
)
def test_scope_and_capability_substitution_deny_create_and_read(tmp_path, claims_update):
    _root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project._backend.knowledge.application.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    context_request = _context_request(refs)
    valid_context, valid_binding = _signed_request(
        authority, descriptor, refs, context_request, nonce="valid"
    )
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, context_request, valid_context, valid_binding)
    )
    bad_context, bad_binding = _signed_request(
        authority,
        descriptor,
        refs,
        context_request,
        nonce="substitution",
        **claims_update,
    )
    with pytest.raises(PermissionError, match="authorized context packet access denied"):
        project.provider.create_authorized_context_packet(
            _create_request(refs, context_request, bad_context, bad_binding)
        )
    denied = project.provider.read_authorized_context_packet(
        _read_request(packet, refs, bad_context, bad_binding)
    )
    assert denied.status == "context_packet_unavailable"
    assert "sources/private.md" not in denied.model_dump_json()


@pytest.mark.parametrize("mutation", ["query", "purpose", "budget"])
def test_signed_request_binding_rejects_unsigned_request_substitution(tmp_path, mutation):
    _root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project._backend.knowledge.application.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    original = _context_request(refs, max_total_bytes=4096)
    context, binding = _signed_request(
        authority, descriptor, refs, original, nonce=f"binding-{mutation}"
    )
    updates = {}
    if mutation == "query":
        updates["context_request"] = _context_request(refs, query="altered query")
    elif mutation == "purpose":
        updates["purpose"] = "different task"
    else:
        updates["max_context_tokens"] = 24_000
    candidate_context_request = updates.pop("context_request", original)
    with pytest.raises(PermissionError, match="authorized context packet access denied"):
        project.provider.create_authorized_context_packet(
            _create_request(
                refs, candidate_context_request, context, binding, **updates
            )
        )


def test_tampered_stored_packet_and_unconfigured_provider_are_coarsely_unavailable(tmp_path):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    context_request = _context_request(refs)
    context, binding = _signed_request(
        authority, descriptor, refs, context_request, nonce="tamper"
    )
    unconfigured = project.provider.read_authorized_context_packet(
        AuthorizedContextPacketReadRequest(
            packet_digest="sha256:" + "0" * 64,
            access_context=context,
            request_binding=binding,
            exact_refs=refs,
            query="shared packet",
            purpose=PURPOSE,
            scope=SCOPE,
        )
    )
    assert unconfigured.status == "context_packet_unavailable"
    project._backend.knowledge.application.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, context_request, context, binding)
    )
    available = project.provider.read_authorized_context_packet(
        _read_request(packet, refs, context, binding)
    )
    assert available.status == "available"
    path = (
        root
        / ".krail"
        / "authorized-context-packets"
        / f"{packet.packet_digest.removeprefix('sha256:')}.json"
    )
    body = json.loads(path.read_text(encoding="utf-8"))
    body["purpose"] = "tampered task"
    path.write_text(json.dumps(body), encoding="utf-8")
    denied = project.provider.read_authorized_context_packet(
        _read_request(packet, refs, context, binding)
    )
    assert denied.status == "context_packet_unavailable"
    with pytest.raises(ValueError, match="availability fields"):
        AuthorizedContextPacketReadResult(
            status="context_packet_unavailable", packet=packet
        )
    with pytest.raises(ValueError, match="availability fields"):
        AuthorizedContextPacketReadResult(
            status="context_packet_unavailable",
            reauthorization=available.reauthorization,
        )
    with pytest.raises(ValueError, match="availability fields"):
        AuthorizedContextPacketReadResult(status="available", packet=packet)
    with pytest.raises(ValueError, match="availability fields"):
        AuthorizedContextPacketReadResult(
            status="available", reauthorization=available.reauthorization
        )
    path.write_bytes(b"x" * (MAX_AUTHORIZED_CONTEXT_PACKET_BYTES + 1))
    oversized = project.provider.read_authorized_context_packet(
        _read_request(packet, refs, context, binding)
    )
    assert oversized.status == "context_packet_unavailable"


def test_full_canonical_context_overhead_is_enforced_by_token_upper_bound(tmp_path):
    root, project, base_refs = _project(tmp_path)
    for index in range(16):
        path = root / "sources" / f"citation-{index:02d}.md"
        path.write_text(
            f"# Citation {index}\n\nNeedleHeavy evidence {index}.\n", encoding="utf-8"
        )
    refs = (*base_refs[:2], *(project.provider._ref(f"sources/citation-{index:02d}.md") for index in range(16)))
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project._backend.knowledge.application.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    context_request = ContextBriefRequest(
        repository=refs[0],
        issue=refs[1],
        evaluated_at=NOW,
        query="NeedleHeavy",
        max_items=18,
        max_total_bytes=1000,
        authorization_omission=True,
    )
    context, binding = _signed_request(
        authority,
        descriptor,
        refs,
        context_request,
        max_context_tokens=5000,
        nonce="citation-heavy",
    )
    with pytest.raises(ValueError, match="token upper bound"):
        project.provider.create_authorized_context_packet(
            _create_request(
                refs,
                context_request,
                context,
                binding,
                max_context_tokens=5000,
            )
        )


def test_create_denies_current_head_loss_at_final_release_boundary(tmp_path):
    _root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    private_calls = [0]

    def current_head(resource_id):
        if resource_id == refs[2].resource_id:
            private_calls[0] += 1
            if private_calls[0] > 1:
                raise KeyError(resource_id)
        return next(ref for ref in refs if ref.resource_id == resource_id)

    project._backend.knowledge.application.configure_authorized_context_packets(
        authority,
        tenant_id="tenant-a",
        project_id="project-a",
        clock=lambda: NOW,
        current_ref_resolver=current_head,
    )
    context_request = _context_request(refs)
    context, binding = _signed_request(
        authority, descriptor, refs, context_request, nonce="final-head-loss"
    )
    with pytest.raises(PermissionError, match="authorized context packet access denied"):
        project.provider.create_authorized_context_packet(
            _create_request(refs, context_request, context, binding)
        )


def test_source_lifecycle_invalidation_is_authorized_persistent_and_non_resurrecting(tmp_path):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    current = [NOW]
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: current[0]
    )
    request = _context_request(refs)
    reader, binding = _signed_request(authority, descriptor, refs, request, nonce="reader")
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, request, reader, binding)
    )
    service = project._backend.knowledge.application.authorized_context_packets
    with pytest.raises(PermissionError, match="retention is unavailable"):
        service.invalidate_for_sources(reader, exact_refs=(refs[2],), reason="source-deleted")
    maintainer, _ = _signed_request(
        authority, descriptor, (refs[2],), request, nonce="maintainer", actions=("retention.enforce",)
    )
    assert service.invalidate_for_sources(
        maintainer, exact_refs=(refs[2],), reason="source-deleted"
    ) == (packet.packet_digest,)
    assert not (
        root / ".krail" / "authorized-context-packets" / f"{packet.packet_digest.removeprefix('sha256:')}.json"
    ).exists()
    assert project.provider.read_authorized_context_packet(
        _read_request(packet, refs, reader, binding)
    ).status == "context_packet_unavailable"
    with pytest.raises(PermissionError, match="access denied"):
        project.provider.create_authorized_context_packet(
            _create_request(refs, request, reader, binding)
        )
    restarted = Project(slug="shared-packet", backend=LocalEngine(project_path=root))
    restarted.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: current[0]
    )
    assert restarted.provider.read_authorized_context_packet(
        _read_request(packet, refs, reader, binding)
    ).status == "context_packet_unavailable"


def test_explicit_packet_ttl_uses_service_clock_and_needs_retention_authority(tmp_path):
    _root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    current = [NOW]
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: current[0],
        packet_retention_until=lambda _packet: NOW + timedelta(minutes=1),
    )
    request = _context_request(refs)
    reader, binding = _signed_request(authority, descriptor, refs, request, nonce="ttl-reader")
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, request, reader, binding)
    )
    maintainer, _ = _signed_request(
        authority, descriptor, refs, request, nonce="ttl-maintainer", actions=("retention.enforce",)
    )
    service = project._backend.knowledge.application.authorized_context_packets
    assert service.enforce_retention(maintainer) == ()
    current[0] += timedelta(minutes=2)
    assert project.provider.read_authorized_context_packet(
        _read_request(packet, refs, reader, binding)
    ).status == "context_packet_unavailable"
    assert service.enforce_retention(maintainer) == (packet.packet_digest,)
    assert project.provider.read_authorized_context_packet(
        _read_request(packet, refs, reader, binding)
    ).status == "context_packet_unavailable"


def test_tombstoned_packet_stays_unavailable_when_old_cache_bytes_are_restored(tmp_path):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    request = _context_request(refs)
    reader, binding = _signed_request(authority, descriptor, refs, request, nonce="restore-reader")
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, request, reader, binding)
    )
    path = root / ".krail" / "authorized-context-packets" / f"{packet.packet_digest.removeprefix('sha256:')}.json"
    saved = path.read_bytes()
    maintainer, _ = _signed_request(
        authority, descriptor, (refs[2],), request, nonce="restore-maintainer", actions=("retention.enforce",)
    )
    project._backend.knowledge.application.authorized_context_packets.invalidate_for_sources(
        maintainer, exact_refs=(refs[2],), reason="source-deleted"
    )
    path.write_bytes(saved)  # deterministic stale-backup fault injection
    assert project.provider.read_authorized_context_packet(
        _read_request(packet, refs, reader, binding)
    ).status == "context_packet_unavailable"


def test_full_tombstone_journal_fails_closed_without_claiming_cache_deletion(tmp_path):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    request = _context_request(refs)
    reader, binding = _signed_request(authority, descriptor, refs, request, nonce="full-reader")
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, request, reader, binding)
    )
    path = root / ".krail" / "authorized-context-packets" / f"{packet.packet_digest.removeprefix('sha256:')}.json"
    saved = path.read_bytes()
    service = project._backend.knowledge.application.authorized_context_packets
    service._persist_tombstones({  # deterministic durable-journal capacity fault injection
        f"sha256:{index:064x}": AuthorizedContextPacketTombstone(
            packet_digest=f"sha256:{index:064x}", reason="source-deleted", occurred_at=NOW,
            decision_digest="sha256:" + "e" * 64,
        )
        for index in range(MAX_AUTHORIZED_CONTEXT_PACKET_TOMBSTONES)
    })
    maintainer, _ = _signed_request(
        authority, descriptor, (refs[2],), request, nonce="full-maintainer", actions=("retention.enforce",)
    )
    with pytest.raises(PermissionError, match="retention is unavailable"):
        service.invalidate_for_sources(maintainer, exact_refs=(refs[2],), reason="source-deleted")
    assert path.read_bytes() == saved
    assert project.provider.read_authorized_context_packet(
        _read_request(packet, refs, reader, binding)
    ).status == "available"


def test_corrupt_durable_tombstone_journal_denies_packet_at_public_read_boundary(tmp_path):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    request = _context_request(refs)
    reader, binding = _signed_request(authority, descriptor, refs, request, nonce="corrupt-reader")
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, request, reader, binding)
    )
    (root / ".krail" / "authorized-context-packet-tombstones.json").write_text(
        '{"schema_version":"krail.authorized-context-packet-tombstones.v1","entries":null}',
        encoding="utf-8",
    )  # deterministic durable-state corruption fault injection
    assert project.provider.read_authorized_context_packet(
        _read_request(packet, refs, reader, binding)
    ).status == "context_packet_unavailable"


def test_two_process_source_invalidations_do_not_lose_durable_tombstones(tmp_path):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    request = _context_request(refs)
    reader, binding = _signed_request(authority, descriptor, refs, request, nonce="race-reader")
    wide = project.provider.create_authorized_context_packet(
        _create_request(refs, request, reader, binding)
    )
    narrow_context, narrow_binding = _signed_request(
        authority, descriptor, refs[:2], request, nonce="race-narrow", purpose="narrow task"
    )
    narrow = project.provider.create_authorized_context_packet(
        _create_request(refs[:2], request, narrow_context, narrow_binding, purpose="narrow task")
    )
    maintainer, _ = _signed_request(
        authority, descriptor, refs, request, nonce="race-maintainer", actions=("retention.enforce",)
    )
    context = multiprocessing.get_context("fork")
    ready, start, result = context.Queue(), context.Event(), context.Queue()
    processes = [
        context.Process(target=_invalidate_packet_cache_process, args=(str(root), maintainer.model_dump_json(), [ref.model_dump(mode="json") for ref in target], ready, start, result))
        for target in ((refs[2],), (refs[0],))
    ]
    for process in processes: process.start()
    assert ready.get(timeout=10) and ready.get(timeout=10)
    start.set()
    outcomes = [result.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert all(status == "ok" for status, _value in outcomes), outcomes
    restarted = Project(slug="shared-packet", backend=LocalEngine(project_path=root))
    restarted.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    assert restarted.provider.read_authorized_context_packet(
        _read_request(wide, refs, reader, binding)
    ).status == "context_packet_unavailable"
    assert restarted.provider.read_authorized_context_packet(
        _read_request(narrow, refs[:2], narrow_context, narrow_binding, purpose="narrow task")
    ).status == "context_packet_unavailable"


def test_inflight_read_cannot_release_packet_after_invalidation_commits(tmp_path, monkeypatch):
    _root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test"
    )
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    request = _context_request(refs)
    reader, binding = _signed_request(authority, descriptor, refs, request, nonce="inflight-reader")
    packet = project.provider.create_authorized_context_packet(
        _create_request(refs, request, reader, binding)
    )
    maintainer, _ = _signed_request(
        authority, descriptor, (refs[2],), request, nonce="inflight-maintainer", actions=("retention.enforce",)
    )
    service = project._backend.knowledge.application.authorized_context_packets
    original_verify = authority.verify
    calls, invalidated = [0], [False]

    def verify_then_invalidate(context, *, as_of):
        claims = original_verify(context, as_of=as_of)
        if context == reader:
            calls[0] += 1
            # Read's eighth verification is the final exact-resource check,
            # immediately before receipt construction and locked release.
            if calls[0] == 8 and not invalidated[0]:
                invalidated[0] = True
                service.invalidate_for_sources(
                    maintainer, exact_refs=(refs[2],), reason="source-deleted"
                )
        return claims

    monkeypatch.setattr(authority, "verify", verify_then_invalidate)
    assert project.provider.read_authorized_context_packet(
        _read_request(packet, refs, reader, binding)
    ).status == "context_packet_unavailable"
    assert invalidated == [True]


def test_inflight_create_does_not_publish_after_source_authority_revocation(tmp_path, monkeypatch):
    root, project, refs = _project(tmp_path)
    descriptor = authorized_context_packet_descriptor()
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority(
        {"core": b"core-owned-packet-key"}, issuer="https://control.example.test", revocations=revocations
    )
    project.configure_authorized_context_packets(
        authority, tenant_id="tenant-a", project_id="project-a", clock=lambda: NOW
    )
    request = _context_request(refs)
    reader, binding = _signed_request(authority, descriptor, refs, request, nonce="inflight-create")
    original_verify, calls = authority.verify, [0]

    def revoke_before_publish_verify(context, *, as_of):
        calls[0] += 1
        if context == reader and calls[0] == 9:
            revocations.revoke_context(reader.context_digest, revoked_at=NOW)
        return original_verify(context, as_of=as_of)

    monkeypatch.setattr(authority, "verify", revoke_before_publish_verify)
    with pytest.raises(PermissionError, match="authorized context packet access denied"):
        project.provider.create_authorized_context_packet(
            _create_request(refs, request, reader, binding)
        )
    cache = root / ".krail" / "authorized-context-packets"
    assert not cache.exists() or not tuple(cache.glob("*.json"))


def test_packet_capability_is_new_read_only_negotiated_surface(tmp_path):
    _root, project, _refs = _project(tmp_path)
    descriptor = project.provider.capability_descriptor(
        AUTHORIZED_CONTEXT_PACKET_CAPABILITY_ID
    )
    assert descriptor.semantic_version == AUTHORIZED_CONTEXT_PACKET_CAPABILITY_VERSION
    assert descriptor.effects.classification == "read-only"
    assert descriptor.effects.external_effects is False
    assert [operation.operation_id for operation in descriptor.operations] == [
        "create_authorized_context_packet",
        "read_authorized_context_packet",
    ]
    assert all(
        value not in descriptor.model_dump_json().lower()
        for value in ("signature key", "private key", "sql", "graph traversal")
    )
