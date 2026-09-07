from datetime import UTC, datetime
from hashlib import sha256
from threading import Event, Thread

import pytest

from krail.provider.v1 import ResourceRef
from rail.authorized_context import (
    HostedAccessContextAuthorizer,
    HostedProcedureInvalidationAuthorizer,
    PROCEDURE_INVALIDATION_CAPABILITY_ID,
    PROCEDURE_INVALIDATION_CAPABILITY_VERSION,
)
from rail.hosted.access import AccessClaims, AccessContextAuthority
from rail.procedure_projection import TemporalProjectionService, create_projection_tombstone
from rail.temporal_records import create_temporal_record

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class Allow:
    def authorize(self, record, *, at):
        return None


class AllowInvalidation:
    def __init__(self, expected_at=None):
        self.expected_at = expected_at

    def authorize_invalidation(self, event_id, changed_ref, event_digest, *, at):
        if self.expected_at is not None:
            assert at == self.expected_at


def ref(kind: str, ident: str) -> ResourceRef:
    return ResourceRef(
        authority="test://source", resource_type=kind, resource_id=ident,
        version="1", digest="sha256:" + sha256(ident.encode()).hexdigest(),
    )


def record(entity: str, revision: str, *, supersedes=None, recorded_at=NOW, source=None):
    return create_temporal_record(
        record_id=f"test:{entity}:{revision}", entity_id=entity, entity_authority="test://entity",
        payload_schema="test.procedure", payload_schema_version="1.0.0", kind="proposed_change",
        authority="test://authority", writer_family="test-writer", valid_from=NOW,
        recorded_at=recorded_at, source_refs=(source or ref("dependency", f"dep-{entity}"),),
        revision=revision, payload={"entity": entity, "revision": revision},
        supersedes_digest=supersedes,
    )


def test_temporal_projection_converges_late_duplicate_restart_and_tombstone(tmp_path):
    service = TemporalProjectionService(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p", clock=lambda: NOW)
    first = record("a", "1")
    second = record("a", "2", supersedes=first.record_digest, recorded_at=NOW.replace(hour=13))
    removed = record("removed", "1")
    service.ingest(second, at=NOW, writer=Allow())
    service.ingest(first, at=NOW, writer=Allow())
    service.ingest(removed, at=NOW, writer=Allow())
    service.ingest(first, at=NOW, writer=Allow())
    late = service.rebuild(projection_id="current", valid_at=NOW.replace(hour=14), known_at=NOW.replace(hour=23), at=NOW)
    assert late.record_digests == tuple(sorted((second.record_digest, removed.record_digest)))
    restarted = TemporalProjectionService(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p", clock=lambda: NOW)
    again = restarted.rebuild(projection_id="current", valid_at=NOW.replace(hour=14), known_at=NOW.replace(hour=23), at=NOW)
    assert again.record_digests == late.record_digests
    assert again.revision == late.revision
    # Tombstones are effective-time facts, known later, and must be signed writes.
    target = restarted.record_ref(removed)
    tombstone = restarted.tombstone(
        target, event_id="removed:a1", reason="source removed",
        effective_at=NOW.replace(hour=14), recorded_at=NOW.replace(hour=16),
        authorizer=AllowInvalidation(expected_at=NOW),
    )
    assert restarted.tombstone(
        target, event_id="removed:a1", reason="source removed",
        effective_at=NOW.replace(hour=14), recorded_at=NOW.replace(hour=16),
        authorizer=AllowInvalidation(expected_at=NOW),
    ) == tombstone
    before_known = restarted.rebuild(
        projection_id="before-known", valid_at=NOW.replace(hour=15),
        known_at=NOW.replace(hour=15), at=NOW,
    )
    after_known = restarted.rebuild(
        projection_id="after-known", valid_at=NOW.replace(hour=15),
        known_at=NOW.replace(hour=17), at=NOW,
    )
    assert before_known.record_digests == tuple(sorted((second.record_digest, removed.record_digest)))
    assert after_known.record_digests == (second.record_digest,)


def test_temporal_projection_keeps_three_level_dependency_edges(tmp_path):
    service = TemporalProjectionService(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p")
    records = [record("a", "1"), record("b", "1"), record("c", "1")]
    for item in records:
        service.ingest(item, at=NOW, writer=Allow())
    checkpoint = service.rebuild(projection_id="chain", valid_at=NOW, known_at=NOW, at=NOW)
    assert len(checkpoint.edge_digests) == 3


def test_temporal_projection_reverse_edges_find_three_level_affected_region(tmp_path):
    service = TemporalProjectionService(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p")
    a = record("a", "1")
    a_ref = service.record_ref(a)
    b = record("b", "1", source=a_ref)
    b_ref = service.record_ref(b)
    c = record("c", "1", source=b_ref)
    for item in (a, b, c):
        service.ingest(item, at=NOW, writer=Allow())
    assert tuple(item.digest for item in service.affected_region(a_ref)) == (b.record_digest, c.record_digest)


def test_affected_region_is_exact_ref_scoped_not_digest_scoped(tmp_path):
    service = TemporalProjectionService(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p")
    same_digest = "sha256:" + "a" * 64
    left = ResourceRef(authority="test://left", resource_type="dependency", resource_id="same", version="1", digest=same_digest)
    right = ResourceRef(authority="test://right", resource_type="dependency", resource_id="same", version="1", digest=same_digest)
    left_dependent = record("left", "1", source=left)
    right_dependent = record("right", "1", source=right)
    for item in (left_dependent, right_dependent):
        service.ingest(item, at=NOW, writer=Allow())
    assert tuple(item.digest for item in service.affected_region(left)) == (left_dependent.record_digest,)


def test_incremental_recompute_only_rewrites_changed_entity_rows_and_recovers_dirty_queue(tmp_path):
    path = tmp_path / "semantic.json"
    service = TemporalProjectionService(str(path), tenant_id="t", project_id="p")
    a = record("a", "1")
    a_ref = service.record_ref(a)
    b = record("b", "1", source=a_ref)
    b_ref = service.record_ref(b)
    c = record("c", "1", source=b_ref)
    unrelated = record("unrelated", "1")
    for item in (a, b, c, unrelated):
        service.ingest(item, at=NOW, writer=Allow())
    service.rebuild(projection_id="current", valid_at=NOW, known_at=NOW.replace(hour=14), at=NOW)
    original = {row.entity_id: row.revision for row in service.current_state("current")}
    replacement = record("a", "2", supersedes=a.record_digest, recorded_at=NOW.replace(hour=13))
    service.ingest(replacement, at=NOW, writer=Allow())
    # Simulate a crash after durable ingest/dirty marking and before recompute.
    restarted = TemporalProjectionService(str(path), tenant_id="t", project_id="p")
    run = restarted.recompute(projection_id="current", valid_at=NOW, known_at=NOW.replace(hour=14), at=NOW)
    assert {item.digest for item in run.affected_outputs} == {b.record_digest, c.record_digest, replacement.record_digest}
    current = {row.entity_id: row for row in restarted.current_state("current")}
    assert current["a"].record_digests == (replacement.record_digest,)
    assert current["b"].revision == original["b"] + 1
    assert current["c"].revision == original["c"] + 1
    assert current["unrelated"].revision == original["unrelated"]
    assert restarted.dirty_outputs("current") == ()


def test_incremental_recompute_carries_exact_dependency_lineage_and_does_not_evaluate_unrelated_entities(tmp_path, monkeypatch):
    import rail.procedure_projection as projection

    path = tmp_path / "semantic.json"
    service = TemporalProjectionService(str(path), tenant_id="t", project_id="p")
    a = record("a", "1")
    b = record("b", "1", source=service.record_ref(a))
    c = record("c", "1", source=service.record_ref(b))
    unrelated = record("unrelated", "1")
    for item in (a, b, c, unrelated):
        service.ingest(item, at=NOW, writer=Allow())
    service.rebuild(projection_id="current", valid_at=NOW, known_at=NOW.replace(hour=14), at=NOW)
    replacement = record("a", "2", supersedes=a.record_digest, recorded_at=NOW.replace(hour=13))
    service.ingest(replacement, at=NOW, writer=Allow())
    queried_entities = []
    original_query = projection.query_temporal_records

    def observe(records, **kwargs):
        queried_entities.append({item.entity_id for item in records})
        return original_query(records, **kwargs)

    monkeypatch.setattr(projection, "query_temporal_records", observe)
    service.recompute(projection_id="current", valid_at=NOW, known_at=NOW.replace(hour=14), at=NOW)
    current = {row.entity_id: row for row in service.current_state("current")}
    assert all("unrelated" not in entities for entities in queried_entities)
    assert a.record_digest in current["b"].input_record_digests
    assert current["b"].dependency_states[0].status == "stale"
    assert current["c"].dependency_states[0].status == "dirty"


def test_recompute_evaluates_unchanged_parent_without_materializing_it_as_dirty(tmp_path):
    service = TemporalProjectionService(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p")
    a = record("a", "1")
    b = record("b", "1", source=service.record_ref(a))
    for item in (a, b):
        service.ingest(item, at=NOW, writer=Allow())
    service.rebuild(projection_id="current", valid_at=NOW, known_at=NOW, at=NOW)
    service.mark_dirty(service.record_ref(a), projection_id="current", reason="external source changed", at=NOW)
    run = service.recompute(projection_id="current", valid_at=NOW, known_at=NOW, at=NOW)
    assert tuple(item.digest for item in run.affected_outputs) == (b.record_digest,)
    assert {row.entity_id for row in service.current_state("current")} == {"a", "b"}


def test_identical_empty_recompute_is_idempotent_and_concurrent_enqueue_survives(tmp_path, monkeypatch):
    path = tmp_path / "semantic.json"
    service = TemporalProjectionService(str(path), tenant_id="t", project_id="p")
    a = record("a", "1")
    b = record("b", "1", source=service.record_ref(a))
    for item in (a, b):
        service.ingest(item, at=NOW, writer=Allow())
    service.rebuild(projection_id="current", valid_at=NOW, known_at=NOW, at=NOW)
    replacement = record("a", "2", supersedes=a.record_digest, recorded_at=NOW)
    service.ingest(replacement, at=NOW, writer=Allow())
    entered = Event()
    queued = Event()
    peer = TemporalProjectionService(str(path), tenant_id="t", project_id="p")
    original_write = service._write_current_locked

    def interleave(*args, **kwargs):
        entered.set()
        return original_write(*args, **kwargs)

    monkeypatch.setattr(service, "_write_current_locked", interleave)

    def enqueue_new_event():
        entered.wait(timeout=2)
        peer.mark_dirty(service.record_ref(a), projection_id="current", reason="arrived while recomputing", at=NOW)
        queued.set()

    worker = Thread(target=enqueue_new_event)
    worker.start()
    service.recompute(projection_id="current", valid_at=NOW, known_at=NOW, at=NOW)
    worker.join(timeout=2)
    assert queued.is_set()
    assert peer.record_ref(b) in peer.dirty_outputs("current")
    first = peer.recompute(projection_id="empty", valid_at=NOW, known_at=NOW, at=NOW)
    assert peer.recompute(projection_id="empty", valid_at=NOW, known_at=NOW, at=NOW) == first


def test_tombstone_requires_invalidation_action_not_read_authority(tmp_path):
    service = TemporalProjectionService(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p")
    item = record("a", "1")
    service.ingest(item, at=NOW, writer=Allow())

    class Reader:
        def authorize(self, record, *, at):
            return None

    with pytest.raises(PermissionError, match="invalidation action denied"):
        service.tombstone(
            service.record_ref(item), event_id="reader", reason="no write",
            effective_at=NOW, recorded_at=NOW, authorizer=Reader(),
        )


def test_signed_exact_invalidation_action_authorizes_tombstone_at_live_clock(tmp_path):
    service = TemporalProjectionService(str(tmp_path / "semantic.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW)
    item = record("signed", "1")
    service.ingest(item, at=NOW, writer=Allow())
    target = service.record_ref(item)
    event = create_projection_tombstone(
        event_id="signed-tombstone", target_ref=target, reason="withdrawn",
        effective_at=NOW, recorded_at=NOW.replace(year=2020),
    )
    capability_digest = "sha256:" + "d" * 64
    authority = AccessContextAuthority({"key": b"projection-key"}, issuer="https://control.example.test", required_capability_id=PROCEDURE_INVALIDATION_CAPABILITY_ID)
    claims = AccessClaims(
        issuer="https://control.example.test", tenant_id="tenant", project_id="project",
        subject="agent/maintainer", delegator="user/alice", delegation_id="delegation/projection",
        capability_id=PROCEDURE_INVALIDATION_CAPABILITY_ID,
        capability_version=PROCEDURE_INVALIDATION_CAPABILITY_VERSION, capability_digest=capability_digest,
        actions=("procedure.invalidate",), source_ids=(target.resource_id,), classifications=("internal",),
        policy_digest="sha256:" + "e" * 64, issued_at=NOW, not_before=NOW, expires_at=NOW.replace(hour=23), nonce="projection",
    )
    action = HostedProcedureInvalidationAuthorizer(
        authority, authority.issue(claims, key_id="key"), tenant_id="tenant", project_id="project",
        exact_refs=(target,), allowed_event_digests=(event.tombstone_digest,), capability_digest=capability_digest, clock=lambda: NOW,
    )
    assert service.tombstone(
        target, event_id=event.event_id, reason=event.reason, effective_at=event.effective_at,
        recorded_at=event.recorded_at, authorizer=action,
    ) == event
    reader_claims = claims.model_copy(update={"actions": ("context.read",), "nonce": "reader"})
    reader = HostedAccessContextAuthorizer(
        authority, authority.issue(reader_claims, key_id="key"), exact_refs=(target,), clock=lambda: NOW,
    )
    with pytest.raises(PermissionError, match="invalidation action denied"):
        service.tombstone(
            target, event_id="reader-tombstone", reason="read is not write", effective_at=NOW,
            recorded_at=NOW, authorizer=reader,
        )
