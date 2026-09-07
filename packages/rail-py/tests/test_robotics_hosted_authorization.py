from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from krail.provider.v1 import ResourceRef
from rail.authorized_context import (
    HostedRoboticsInvalidationAuthorizer,
    HostedRoboticsProjectionWriter,
    HostedRoboticsWorldReader,
    ROBOTICS_WORLD_MEMORY_CAPABILITY_ID,
    ROBOTICS_WORLD_MEMORY_CAPABILITY_VERSION,
    robotics_world_memory_scope_digest,
)
from rail.hosted.access import AccessClaims, AccessContextAuthority, MemoryRevocationRegistry
from rail.procedure_projection import TemporalProjectionService, create_projection_tombstone
from rail.robotics_world_memory import Pose, TabletopWorldMemory, WorldObject

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
WORLD = "table-a"


class Allow:
    def authorize(self, ref):
        return None


def evidence(name: str) -> ResourceRef:
    return ResourceRef(
        authority="fixture://camera", resource_type="image", resource_id=name,
        version="1", digest="sha256:" + sha256(name.encode()).hexdigest(),
    )


def pose(x: float, revision: str, at: datetime = NOW) -> Pose:
    return Pose(
        frame_id="table", metres=(x, 0.0, 0.0), quaternion_xyzw=(0, 0, 0, 1),
        observed_at=at, uncertainty_metres=0.01, revision=revision, map_revision="map-1",
    )


def drafts():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    obj = WorldObject(world_id=WORLD, object_id="cup", class_label="cup")
    observation = memory.record(obj, pose(0.1, "obs"), kind="observation", evidence=(evidence("sensor"),))
    estimate = memory.record(
        obj, pose(0.2, "estimate", NOW + timedelta(minutes=1)), kind="estimate",
        evidence=(evidence("estimate"),), estimate_expires_at=NOW + timedelta(hours=1),
    )
    refs = tuple(dict.fromkeys((
        *observation.source_refs, *estimate.source_refs,
        memory.record_ref(observation), memory.record_ref(estimate),
        TemporalProjectionService.record_ref(observation), TemporalProjectionService.record_ref(estimate),
    )))
    return observation, estimate, refs


def claims(*, actions, refs, tenant="tenant", project="project", world=WORLD, expires_at=NOW + timedelta(hours=1), nonce="n"):
    issued_at = NOW - timedelta(minutes=1) if expires_at > NOW else expires_at - timedelta(minutes=2)
    not_before = NOW - timedelta(seconds=1) if expires_at > NOW else expires_at - timedelta(minutes=1)
    return AccessClaims(
        issuer="https://control.example.test", tenant_id=tenant, project_id=project,
        subject="robot/operator", delegator="user/alice", delegation_id=f"delegation/{nonce}",
        capability_id=ROBOTICS_WORLD_MEMORY_CAPABILITY_ID,
        capability_version=ROBOTICS_WORLD_MEMORY_CAPABILITY_VERSION,
        capability_digest=robotics_world_memory_scope_digest(tenant_id=tenant, project_id=project, world_id=world, exact_refs=refs), actions=actions,
        source_ids=tuple(dict.fromkeys(ref.resource_id for ref in refs)), classifications=("internal",),
        policy_digest="sha256:" + "d" * 64, issued_at=issued_at,
        not_before=not_before, expires_at=expires_at, nonce=nonce,
    )


def adapters(authority, *, reader_context, writer_context, invalidation_context, refs, records, event_digest, publication_refs=()):
    common = dict(tenant_id="tenant", project_id="project", world_id=WORLD, exact_refs=refs, capability_digest=robotics_world_memory_scope_digest(tenant_id="tenant", project_id="project", world_id=WORLD, exact_refs=refs), clock=lambda: NOW)
    return (
        HostedRoboticsWorldReader(authority, reader_context, **common),
        HostedRoboticsProjectionWriter(authority, writer_context, **common, allowed_record_digests=tuple(item.record_digest for item in records), allowed_publication_refs=publication_refs),
        HostedRoboticsInvalidationAuthorizer(authority, invalidation_context, **common, allowed_event_digests=(event_digest,)),
    )


def test_hosted_world_memory_binds_signed_read_write_and_invalidation_at_live_clock(tmp_path):
    observation, estimate, refs = drafts()
    invalidated_at = NOW + timedelta(minutes=2)
    event = create_projection_tombstone(
        event_id="camera-revoked", target_ref=evidence("estimate"), reason="camera revoked",
        effective_at=invalidated_at, recorded_at=invalidated_at,
    )
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority({"key": b"robotics-key"}, issuer="https://control.example.test", revocations=revocations)
    reader_context = authority.issue(claims(actions=("context.read",), refs=refs, nonce="reader"), key_id="key")
    writer_context = authority.issue(claims(actions=("projection.write",), refs=refs, nonce="writer"), key_id="key")
    invalidation_context = authority.issue(claims(actions=("procedure.invalidate",), refs=refs, nonce="invalidate"), key_id="key")
    reader, writer, invalidator = adapters(authority, reader_context=reader_context, writer_context=writer_context, invalidation_context=invalidation_context, refs=refs, records=(observation, estimate), event_digest=event.tombstone_digest)
    memory = TabletopWorldMemory(
        str(tmp_path / "semantic.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW,
        hosted_world_id=WORLD, hosted_reader=reader, projection_writer=writer,
        projection_invalidation_authorizer=invalidator,
    )
    assert memory.record(WorldObject(world_id=WORLD, object_id="cup", class_label="cup"), pose(0.1, "obs"), kind="observation", evidence=(evidence("sensor"),)) == observation
    assert memory.record(WorldObject(world_id=WORLD, object_id="cup", class_label="cup"), pose(0.2, "estimate", NOW + timedelta(minutes=1)), kind="estimate", evidence=(evidence("estimate"),), estimate_expires_at=NOW + timedelta(hours=1)) == estimate
    estimate_at = NOW + timedelta(minutes=1)
    memory.rebuild_projection(valid_at=estimate_at, known_at=estimate_at, at=estimate_at)
    assert memory.location(world_id=WORLD, object_id="cup", at=estimate_at, known_at=estimate_at, estimated=True, reader=reader).status == "estimated"
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.location(world_id=WORLD, object_id="cup", at=estimate_at, known_at=estimate_at, estimated=True, reader=Allow())
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.location(world_id="other", object_id="cup", at=estimate_at, known_at=estimate_at, estimated=True, reader=reader)
    assert memory.invalidate_map_revision(evidence("estimate"), reason="camera revoked", at=invalidated_at, event_id=event.event_id, world_id=WORLD) == (TemporalProjectionService.record_ref(estimate),)
    assert memory.location(world_id=WORLD, object_id="cup", at=invalidated_at, known_at=invalidated_at, estimated=True, reader=reader).status == "stale"


def test_hosted_similarity_reader_revocation_denies_all_results(tmp_path):
    draft = TabletopWorldMemory(clock=lambda: NOW)
    obj = WorldObject(world_id=WORLD, object_id="cup", class_label="cup")
    observation = draft.record(obj, pose(.1, "obs"), kind="observation", evidence=(evidence("sensor"),))
    appearance = draft.record_appearance(obj, observation, asset_ref=evidence("asset"), viewpoint="top", context="table", descriptor_model="m", descriptor_version="1", quality=.9, occluded=False, revision="a", descriptor=(1., 0.))
    record = draft._appearance_records[0]
    refs = tuple(dict.fromkeys((*observation.source_refs, *record.source_refs, draft.record_ref(observation), draft.record_ref(record), appearance.appearance_ref, TemporalProjectionService.record_ref(observation), TemporalProjectionService.record_ref(record))))
    event = create_projection_tombstone(event_id="unused", target_ref=evidence("asset"), reason="unused", effective_at=NOW, recorded_at=NOW)
    revocations = MemoryRevocationRegistry(); authority = AccessContextAuthority({"key": b"robotics-key"}, issuer="https://control.example.test", revocations=revocations)
    reader_context = authority.issue(claims(actions=("context.read",), refs=refs, nonce="similarity-reader"), key_id="key")
    writer_context = authority.issue(claims(actions=("projection.write",), refs=refs, nonce="similarity-writer"), key_id="key")
    invalidation_context = authority.issue(claims(actions=("procedure.invalidate",), refs=refs, nonce="similarity-invalidate"), key_id="key")
    reader, writer, invalidator = adapters(authority, reader_context=reader_context, writer_context=writer_context, invalidation_context=invalidation_context, refs=refs, records=(observation, record), event_digest=event.tombstone_digest)
    memory = TabletopWorldMemory(str(tmp_path / "similarity.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=writer, projection_invalidation_authorizer=invalidator)
    hosted_observation = memory.record(obj, pose(.1, "obs"), kind="observation", evidence=(evidence("sensor"),))
    memory.record_appearance(obj, hosted_observation, asset_ref=evidence("asset"), viewpoint="top", context="table", descriptor_model="m", descriptor_version="1", quality=.9, occluded=False, revision="a", descriptor=(1., 0.))
    assert len(memory.similar_appearances(world_id=WORLD, descriptor=(1., 0.), descriptor_model="m", descriptor_version="1", at=NOW, known_at=NOW, reader=reader)) == 1
    revocations.revoke_context(reader_context.context_digest, revoked_at=NOW)
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.similar_appearances(world_id=WORLD, descriptor=(1., 0.), descriptor_model="m", descriptor_version="1", at=NOW, known_at=NOW, reader=reader)
def test_hosted_world_memory_rejects_wrong_scope_read_only_write_and_expired_invalidation(tmp_path):
    observation, estimate, refs = drafts()
    event = create_projection_tombstone(event_id="expired", target_ref=evidence("estimate"), reason="expired", effective_at=NOW, recorded_at=NOW)
    authority = AccessContextAuthority({"key": b"robotics-key"}, issuer="https://control.example.test")
    valid_reader_context = authority.issue(claims(actions=("context.read",), refs=refs, nonce="reader"), key_id="key")
    read_only_writer_context = authority.issue(claims(actions=("context.read",), refs=refs, nonce="writer-read"), key_id="key")
    expired_invalidation_context = authority.issue(claims(actions=("procedure.invalidate",), refs=refs, expires_at=NOW - timedelta(seconds=1), nonce="expired"), key_id="key")
    reader, read_only_writer, expired_invalidator = adapters(authority, reader_context=valid_reader_context, writer_context=read_only_writer_context, invalidation_context=expired_invalidation_context, refs=refs, records=(observation, estimate), event_digest=event.tombstone_digest)
    memory = TabletopWorldMemory(str(tmp_path / "semantic.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=read_only_writer, projection_invalidation_authorizer=expired_invalidator)
    with pytest.raises(PermissionError, match="robotics world-memory"):
        memory.record(WorldObject(world_id=WORLD, object_id="cup", class_label="cup"), pose(0.1, "obs"), kind="observation", evidence=(evidence("sensor"),))
    with pytest.raises(PermissionError, match="denied"):
        memory.invalidate_map_revision(evidence("estimate"), reason="expired", at=NOW, event_id=event.event_id, world_id=WORLD)
    with pytest.raises(ValueError, match="capability digest"):
        HostedRoboticsWorldReader(authority, valid_reader_context, tenant_id="tenant", project_id="other", world_id=WORLD, exact_refs=refs, capability_digest=robotics_world_memory_scope_digest(tenant_id="tenant", project_id="project", world_id=WORLD, exact_refs=refs), clock=lambda: NOW)
    with pytest.raises(ValueError, match="capability digest"):
        HostedRoboticsWorldReader(authority, valid_reader_context, tenant_id="tenant", project_id="project", world_id="other-world", exact_refs=refs, capability_digest=robotics_world_memory_scope_digest(tenant_id="tenant", project_id="project", world_id=WORLD, exact_refs=refs), clock=lambda: NOW)
    tampered = valid_reader_context.model_copy(update={"signature": "sha256:" + "0" * 64})
    with pytest.raises(PermissionError, match="robotics world-memory"):
        HostedRoboticsWorldReader(authority, tampered, tenant_id="tenant", project_id="project", world_id=WORLD, exact_refs=refs, capability_digest=robotics_world_memory_scope_digest(tenant_id="tenant", project_id="project", world_id=WORLD, exact_refs=refs), clock=lambda: NOW).authorize(refs[0])


def test_hosted_adapters_use_live_clock_and_reject_rebinding_or_local_fakes(tmp_path):
    observation, estimate, refs = drafts()
    event = create_projection_tombstone(event_id="revoked", target_ref=evidence("estimate"), reason="revoked", effective_at=NOW, recorded_at=NOW)
    revocations = MemoryRevocationRegistry()
    authority = AccessContextAuthority({"key": b"robotics-key"}, issuer="https://control.example.test", revocations=revocations)
    expired_reader_context = authority.issue(claims(actions=("context.read",), refs=refs, expires_at=NOW - timedelta(seconds=1), nonce="expired-reader"), key_id="key")
    writer_context = authority.issue(claims(actions=("projection.write",), refs=refs, nonce="revoked-writer"), key_id="key")
    invalidation_context = authority.issue(claims(actions=("procedure.invalidate",), refs=refs, nonce="revoked-invalidation"), key_id="key")
    expired_reader, writer, invalidator = adapters(authority, reader_context=expired_reader_context, writer_context=writer_context, invalidation_context=invalidation_context, refs=refs, records=(observation, estimate), event_digest=event.tombstone_digest)
    with pytest.raises(PermissionError, match="robotics world-memory"):
        expired_reader.authorize(refs[0])
    revocations.revoke_context(writer_context.context_digest, revoked_at=NOW)
    with pytest.raises(PermissionError, match="robotics world-memory"):
        writer.authorize(observation, at=NOW - timedelta(days=1))
    revocations.revoke_context(invalidation_context.context_digest, revoked_at=NOW)
    with pytest.raises(PermissionError, match="robotics world-memory"):
        invalidator.authorize_invalidation(event.event_id, event.target_ref, event.tombstone_digest, at=NOW - timedelta(days=1))

    other_claim = authority.issue(claims(actions=("context.read",), refs=refs, tenant="other-tenant", nonce="other-tenant"), key_id="key")
    other_reader = HostedRoboticsWorldReader(authority, other_claim, tenant_id="other-tenant", project_id="project", world_id=WORLD, exact_refs=refs, capability_digest=robotics_world_memory_scope_digest(tenant_id="other-tenant", project_id="project", world_id=WORLD, exact_refs=refs), clock=lambda: NOW)
    with pytest.raises(ValueError, match="same exact world"):
        TabletopWorldMemory(str(tmp_path / "other.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=other_reader, projection_writer=writer, projection_invalidation_authorizer=invalidator)

    class Fake:
        world_id = WORLD
        tenant_id = "tenant"
        project_id = "project"

        def authorize(self, ref):
            return None

        def authorize_invalidation(self, *args, **kwargs):
            return None

    with pytest.raises(ValueError, match="supported signed robotics adapters"):
        TabletopWorldMemory(str(tmp_path / "fake.json"), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=Fake(), projection_writer=Fake(), projection_invalidation_authorizer=Fake())


def test_hosted_scene_and_episode_publication_require_live_exact_writer_grants(tmp_path):
    observation, estimate, base_refs = drafts()
    draft = TabletopWorldMemory(clock=lambda: NOW)
    draft_observation = draft.record(WorldObject(world_id=WORLD, object_id="cup", class_label="cup"), pose(0.1, "obs"), kind="observation", evidence=(evidence("sensor"),))
    assert draft_observation == observation
    scene = draft.scene(world_id=WORLD, scene_id="signed-scene", records=(draft_observation,), reader=Allow(), valid_at=NOW, recorded_at=NOW)
    episode = draft.episode(world_id=WORLD, session_id="default", episode_id="signed-episode", scene_refs=(scene.scene_ref,), reader=Allow(), valid_from=NOW, recorded_at=NOW)
    refs = tuple(dict.fromkeys((*base_refs, scene.scene_ref, episode.episode_ref)))
    event = create_projection_tombstone(event_id="scene-write", target_ref=evidence("estimate"), reason="unused", effective_at=NOW, recorded_at=NOW)
    authority = AccessContextAuthority({"key": b"robotics-key"}, issuer="https://control.example.test")

    def signed_adapters(writer_context):
        reader_context = authority.issue(claims(actions=("context.read",), refs=refs, nonce=f"reader-{writer_context.context_digest}"), key_id="key")
        invalidation_context = authority.issue(claims(actions=("procedure.invalidate",), refs=refs, nonce=f"invalidate-{writer_context.context_digest}"), key_id="key")
        return adapters(authority, reader_context=reader_context, writer_context=writer_context, invalidation_context=invalidation_context, refs=refs, records=(observation, estimate), event_digest=event.tombstone_digest, publication_refs=(scene.scene_ref, episode.episode_ref))

    valid_writer_context = authority.issue(claims(actions=("projection.write",), refs=refs, nonce="valid-writer"), key_id="key")
    reader, valid_writer, invalidator = signed_adapters(valid_writer_context)
    revoked_path = tmp_path / "revoked.json"
    memory = TabletopWorldMemory(str(revoked_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=valid_writer, projection_invalidation_authorizer=invalidator)
    memory.record(WorldObject(world_id=WORLD, object_id="cup", class_label="cup"), pose(0.1, "obs"), kind="observation", evidence=(evidence("sensor"),))
    authority.revocations.revoke_context(valid_writer_context.context_digest, revoked_at=NOW)
    with pytest.raises(PermissionError, match="publication denied"):
        memory.scene(world_id=WORLD, scene_id="signed-scene", records=(observation,), reader=reader, valid_at=NOW, recorded_at=NOW)
    assert memory._projection.store.list("tenant", "project", kind="robotics_scene_snapshot") == []

    read_only_context = authority.issue(claims(actions=("context.read",), refs=refs, nonce="read-only-writer"), key_id="key")
    reader, valid_writer, invalidator = signed_adapters(authority.issue(claims(actions=("projection.write",), refs=refs, nonce="episode-bootstrap"), key_id="key"))
    read_only_path = tmp_path / "read-only.json"
    bootstrap = TabletopWorldMemory(str(read_only_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=valid_writer, projection_invalidation_authorizer=invalidator)
    bootstrap.record(WorldObject(world_id=WORLD, object_id="cup", class_label="cup"), pose(0.1, "obs"), kind="observation", evidence=(evidence("sensor"),))
    persisted_scene = bootstrap.scene(world_id=WORLD, scene_id="signed-scene", records=(observation,), reader=reader, valid_at=NOW, recorded_at=NOW)
    reader, read_only_writer, invalidator = signed_adapters(read_only_context)
    denied = TabletopWorldMemory(str(read_only_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=read_only_writer, projection_invalidation_authorizer=invalidator)
    with pytest.raises(PermissionError, match="publication denied"):
        denied.episode(world_id=WORLD, session_id="default", episode_id="signed-episode", scene_refs=(persisted_scene.scene_ref,), reader=reader, valid_from=NOW, recorded_at=NOW)
    assert denied._projection.store.list("tenant", "project", kind="robotics_episode") == []

    expired_context = authority.issue(claims(actions=("projection.write",), refs=refs, expires_at=NOW - timedelta(seconds=1), nonce="expired-writer"), key_id="key")
    reader, expired_writer, invalidator = signed_adapters(expired_context)
    expired = TabletopWorldMemory(str(read_only_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=expired_writer, projection_invalidation_authorizer=invalidator)
    with pytest.raises(PermissionError, match="publication denied"):
        expired.episode(world_id=WORLD, session_id="default", episode_id="signed-episode", scene_refs=(persisted_scene.scene_ref,), reader=reader, valid_from=NOW, recorded_at=NOW)


def test_hosted_appearance_and_identity_candidate_writes_use_projection_writer(tmp_path):
    draft = TabletopWorldMemory(clock=lambda: NOW)
    left_object = WorldObject(world_id=WORLD, object_id="left", class_label="cup")
    right_object = WorldObject(world_id=WORLD, object_id="right", class_label="cup")
    left = draft.record(left_object, pose(0.1, "left"), kind="observation", evidence=(evidence("left-sensor"),))
    right = draft.record(right_object, pose(0.2, "right"), kind="observation", evidence=(evidence("right-sensor"),))
    appearance = draft.record_appearance(left_object, left, asset_ref=evidence("left-asset"), viewpoint="overhead", context="table", descriptor_model="fixture", descriptor_version="1", quality=0.9, occluded=False, revision="appearance")
    candidate = draft.propose_identity_candidates(appearance, session_id="s", candidate_id="ambiguous", candidate_object_refs=(draft.record_ref(left), draft.record_ref(right)), evidence_refs=(evidence("association"),), association_basis="visual candidate", expires_at=NOW + timedelta(minutes=5))
    appearance_record = draft._appearance_records[0]
    candidate_record = draft._identity_candidate_records[0]
    refs = tuple(dict.fromkeys((
        *left.source_refs, *right.source_refs, *appearance_record.source_refs,
        *candidate_record.source_refs, draft.record_ref(left), draft.record_ref(right),
        appearance.appearance_ref, candidate.candidate_ref,
    )))
    event = create_projection_tombstone(event_id="appearance-write", target_ref=evidence("left-asset"), reason="unused", effective_at=NOW, recorded_at=NOW)
    authority = AccessContextAuthority({"key": b"robotics-key"}, issuer="https://control.example.test")

    def adapters_for(writer_context):
        reader_context = authority.issue(claims(actions=("context.read",), refs=refs, nonce=f"reader-{writer_context.context_digest}"), key_id="key")
        invalidation_context = authority.issue(claims(actions=("procedure.invalidate",), refs=refs, nonce=f"invalidate-{writer_context.context_digest}"), key_id="key")
        return adapters(authority, reader_context=reader_context, writer_context=writer_context, invalidation_context=invalidation_context, refs=refs, records=(left, right, appearance_record, candidate_record), event_digest=event.tombstone_digest)

    writer_context = authority.issue(claims(actions=("projection.write",), refs=refs, nonce="appearance-writer"), key_id="key")
    reader, writer, invalidator = adapters_for(writer_context)
    revoked_path = tmp_path / "appearance-revoked.json"
    memory = TabletopWorldMemory(str(revoked_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=writer, projection_invalidation_authorizer=invalidator)
    memory.record(left_object, pose(0.1, "left"), kind="observation", evidence=(evidence("left-sensor"),))
    authority.revocations.revoke_context(writer_context.context_digest, revoked_at=NOW)
    with pytest.raises(PermissionError, match="denied"):
        memory.record_appearance(left_object, left, asset_ref=evidence("left-asset"), viewpoint="overhead", context="table", descriptor_model="fixture", descriptor_version="1", quality=0.9, occluded=False, revision="appearance")

    bootstrap_writer_context = authority.issue(claims(actions=("projection.write",), refs=refs, nonce="candidate-bootstrap"), key_id="key")
    reader, bootstrap_writer, invalidator = adapters_for(bootstrap_writer_context)
    candidate_path = tmp_path / "candidate-read-only.json"
    bootstrap = TabletopWorldMemory(str(candidate_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=bootstrap_writer, projection_invalidation_authorizer=invalidator)
    bootstrap.record(left_object, pose(0.1, "left"), kind="observation", evidence=(evidence("left-sensor"),))
    bootstrap.record(right_object, pose(0.2, "right"), kind="observation", evidence=(evidence("right-sensor"),))
    persisted_appearance = bootstrap.record_appearance(left_object, left, asset_ref=evidence("left-asset"), viewpoint="overhead", context="table", descriptor_model="fixture", descriptor_version="1", quality=0.9, occluded=False, revision="appearance")
    read_only_context = authority.issue(claims(actions=("context.read",), refs=refs, nonce="candidate-read-only"), key_id="key")
    reader, read_only_writer, invalidator = adapters_for(read_only_context)
    denied = TabletopWorldMemory(str(candidate_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=read_only_writer, projection_invalidation_authorizer=invalidator)
    with pytest.raises(PermissionError, match="denied"):
        denied.propose_identity_candidates(persisted_appearance, session_id="s", candidate_id="ambiguous", candidate_object_refs=(denied.record_ref(left), denied.record_ref(right)), evidence_refs=(evidence("association"),), association_basis="visual candidate", expires_at=NOW + timedelta(minutes=5))


def test_hosted_identity_resolution_binds_current_writer_and_full_read_dependency_chain(tmp_path):
    """A reviewed resolution is only visible while every input remains granted."""

    draft = TabletopWorldMemory(clock=lambda: NOW)
    left_object = WorldObject(world_id=WORLD, object_id="left", class_label="cup")
    right_object = WorldObject(world_id=WORLD, object_id="right", class_label="cup")
    left = draft.record(left_object, pose(0.1, "left"), kind="observation", evidence=(evidence("left-sensor"),))
    right = draft.record(right_object, pose(0.2, "right"), kind="observation", evidence=(evidence("right-sensor"),))
    appearance = draft.record_appearance(left_object, left, asset_ref=evidence("left-asset"), viewpoint="overhead", context="table", descriptor_model="fixture", descriptor_version="1", quality=0.9, occluded=False, revision="appearance")
    candidate = draft.propose_identity_candidates(appearance, session_id="s", candidate_id="ambiguous", candidate_object_refs=(draft.record_ref(left), draft.record_ref(right)), evidence_refs=(evidence("association"),), association_basis="visual candidate", expires_at=NOW + timedelta(minutes=5))
    resolution = draft.resolve_identity(candidate, resolved_object_ref=draft.record_ref(left), reviewer_id="reviewer-metadata", evidence_refs=(evidence("review"),), valid_from=NOW + timedelta(minutes=1))
    appearance_record = draft._appearance_records[0]
    candidate_record = draft._identity_candidate_records[0]
    resolution_record = draft._resolution_records[0]
    records = (left, right, appearance_record, candidate_record, resolution_record)
    refs = tuple(dict.fromkeys((
        *(ref for record in records for ref in record.source_refs),
        *(draft.record_ref(record) for record in records),
        *(TemporalProjectionService.record_ref(record) for record in records),
        appearance.appearance_ref, candidate.candidate_ref, resolution.resolution_ref,
    )))
    event = create_projection_tombstone(event_id="resolution-review-revoked", target_ref=evidence("review"), reason="review evidence withdrawn", effective_at=NOW + timedelta(minutes=2), recorded_at=NOW + timedelta(minutes=2))
    authority = AccessContextAuthority({"key": b"robotics-key"}, issuer="https://control.example.test")

    def signed(writer_context, reader_context=None):
        reader_context = reader_context or authority.issue(claims(actions=("context.read",), refs=refs, nonce=f"reader-{writer_context.context_digest}"), key_id="key")
        invalidation_context = authority.issue(claims(actions=("procedure.invalidate",), refs=refs, nonce=f"invalidate-{writer_context.context_digest}"), key_id="key")
        return adapters(authority, reader_context=reader_context, writer_context=writer_context, invalidation_context=invalidation_context, refs=refs, records=records, event_digest=event.tombstone_digest)

    writer_context = authority.issue(claims(actions=("projection.write",), refs=refs, nonce="resolution-writer"), key_id="key")
    reader, writer, invalidator = signed(writer_context)
    path = tmp_path / "resolution.json"
    memory = TabletopWorldMemory(str(path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=writer, projection_invalidation_authorizer=invalidator)
    hosted_left = memory.record(left_object, pose(0.1, "left"), kind="observation", evidence=(evidence("left-sensor"),))
    hosted_right = memory.record(right_object, pose(0.2, "right"), kind="observation", evidence=(evidence("right-sensor"),))
    hosted_appearance = memory.record_appearance(left_object, hosted_left, asset_ref=evidence("left-asset"), viewpoint="overhead", context="table", descriptor_model="fixture", descriptor_version="1", quality=0.9, occluded=False, revision="appearance")
    hosted_candidate = memory.propose_identity_candidates(hosted_appearance, session_id="s", candidate_id="ambiguous", candidate_object_refs=(memory.record_ref(hosted_left), memory.record_ref(hosted_right)), evidence_refs=(evidence("association"),), association_basis="visual candidate", expires_at=NOW + timedelta(minutes=5))
    assert memory.resolve_identity(hosted_candidate, resolved_object_ref=memory.record_ref(hosted_left), reviewer_id="reviewer-metadata", evidence_refs=(evidence("review"),), valid_from=NOW + timedelta(minutes=1)) == resolution
    assert memory.resolved_identity(world_id=WORLD, candidate_ref=hosted_candidate.candidate_ref, at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), reader=reader) == resolution

    # Caller-controlled identity models and cross-world object references cannot
    # replace the canonical candidate selected by the signed write path.
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.resolve_identity(hosted_candidate.model_copy(update={"world_id": "other"}), resolved_object_ref=memory.record_ref(hosted_left), reviewer_id="forged", evidence_refs=(evidence("review"),), valid_from=NOW + timedelta(minutes=1))
    foreign = TabletopWorldMemory(clock=lambda: NOW).record(WorldObject(world_id="other", object_id="left", class_label="cup"), pose(0.1, "foreign"), kind="observation", evidence=(evidence("foreign"),))
    with pytest.raises(ValueError, match="exact candidate"):
        memory.resolve_identity(hosted_candidate, resolved_object_ref=TabletopWorldMemory.record_ref(foreign), reviewer_id="forged", evidence_refs=(evidence("review"),), valid_from=NOW + timedelta(minutes=1))

    # Reopening the canonical candidate with an expired writer cannot publish
    # an otherwise-valid resolution.
    bootstrap_path = tmp_path / "resolution-expired-writer.json"
    bootstrap = TabletopWorldMemory(str(bootstrap_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=reader, projection_writer=writer, projection_invalidation_authorizer=invalidator)
    bootstrap_left = bootstrap.record(left_object, pose(0.1, "left"), kind="observation", evidence=(evidence("left-sensor"),))
    bootstrap_right = bootstrap.record(right_object, pose(0.2, "right"), kind="observation", evidence=(evidence("right-sensor"),))
    bootstrap_appearance = bootstrap.record_appearance(left_object, bootstrap_left, asset_ref=evidence("left-asset"), viewpoint="overhead", context="table", descriptor_model="fixture", descriptor_version="1", quality=0.9, occluded=False, revision="appearance")
    bootstrap_candidate = bootstrap.propose_identity_candidates(bootstrap_appearance, session_id="s", candidate_id="ambiguous", candidate_object_refs=(bootstrap.record_ref(bootstrap_left), bootstrap.record_ref(bootstrap_right)), evidence_refs=(evidence("association"),), association_basis="visual candidate", expires_at=NOW + timedelta(minutes=5))
    expired_writer_context = authority.issue(claims(actions=("projection.write",), refs=refs, expires_at=NOW - timedelta(seconds=1), nonce="resolution-expired-writer"), key_id="key")
    expired_reader, expired_writer, expired_invalidator = signed(expired_writer_context)
    expired = TabletopWorldMemory(str(bootstrap_path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=expired_reader, projection_writer=expired_writer, projection_invalidation_authorizer=expired_invalidator)
    with pytest.raises(PermissionError, match="robotics world-memory"):
        expired.resolve_identity(bootstrap_candidate, resolved_object_ref=expired.record_ref(bootstrap_left), reviewer_id="reviewer-metadata", evidence_refs=(evidence("review"),), valid_from=NOW + timedelta(minutes=1))

    # A reader that can see the resolution row but lacks raw visual evidence
    # must not receive the derived reviewed choice.
    restricted_refs = tuple(ref for ref in refs if ref != evidence("left-sensor"))
    restricted_context = authority.issue(claims(actions=("context.read",), refs=restricted_refs, nonce="resolution-restricted-reader"), key_id="key")
    restricted_reader = HostedRoboticsWorldReader(authority, restricted_context, tenant_id="tenant", project_id="project", world_id=WORLD, exact_refs=restricted_refs, capability_digest=robotics_world_memory_scope_digest(tenant_id="tenant", project_id="project", world_id=WORLD, exact_refs=restricted_refs), clock=lambda: NOW)
    restricted = TabletopWorldMemory(str(path), tenant_id="tenant", project_id="project", clock=lambda: NOW, hosted_world_id=WORLD, hosted_reader=restricted_reader, projection_writer=writer, projection_invalidation_authorizer=invalidator)
    with pytest.raises(PermissionError, match="world-memory access denied"):
        restricted.resolved_identity(world_id=WORLD, candidate_ref=hosted_candidate.candidate_ref, at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), reader=restricted_reader)

    assert memory.invalidate_map_revision(evidence("review"), reason="review evidence withdrawn", at=NOW + timedelta(minutes=2), event_id=event.event_id, world_id=WORLD) == (TemporalProjectionService.record_ref(resolution_record),)
    assert memory.resolved_identity(world_id=WORLD, candidate_ref=hosted_candidate.candidate_ref, at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=reader) is None
    authority.revocations.revoke_context(reader.context.context_digest, revoked_at=NOW)
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.resolved_identity(world_id=WORLD, candidate_ref=hosted_candidate.candidate_ref, at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), reader=reader)
