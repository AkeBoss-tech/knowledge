from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from krail.provider.v1 import ResourceRef
from rail.extension_registry import DomainExtensionRegistry
from rail.procedure_projection import TemporalProjectionService
from rail.robotics_world_memory import Pose, TabletopWorldMemory, WorldObject, register_world_memory_extension, robotics_world_extension, tabletop_episode_fixture, tabletop_fixture
from rail.semantic.repository import JsonSemanticStore, SemanticRow

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class Allow:
    def authorize(self, ref): pass


class AllowProjection:
    def authorize(self, record, *, at):
        return None


class Deny:
    def authorize(self, ref): raise PermissionError("revoked")


class DenyResource:
    def __init__(self, resource_id):
        self.resource_id = resource_id

    def authorize(self, ref):
        if ref.resource_id == self.resource_id:
            raise PermissionError("revoked")


class RevokeAfter:
    def __init__(self, allowed_calls):
        self.allowed_calls = allowed_calls
        self.calls = 0

    def authorize(self, ref):
        self.calls += 1
        if self.calls > self.allowed_calls:
            raise PermissionError("revoked")


def evidence(name):
    return ResourceRef(authority="fixture://camera", resource_type="image", resource_id=name, version="1", digest="sha256:" + sha256(name.encode()).hexdigest())


def pose(x, revision, at=NOW):
    return Pose(frame_id="table", metres=(x, 0.0, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at, uncertainty_metres=0.02, revision=revision, map_revision="map-1")


def test_registry_fixture_identity_and_structural_scene_refs():
    registry = DomainExtensionRegistry()
    descriptor = robotics_world_extension()
    registry.register(descriptor, {"robotics.world-memory.location": lambda inputs, config: dict(inputs[0])})
    memory, scene = tabletop_fixture(NOW)
    assert len(scene.object_refs) == 2 and scene.object_refs[0].authority == "robotics://world-memory"
    assert memory.locate_class(world_id="table-a", class_label="red-cup", at=NOW, known_at=NOW, reader=Allow()).status == "ambiguous"


def test_observed_estimated_occlusion_and_world_isolation():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    obj = WorldObject(world_id="one", object_id="cup", class_label="cup")
    observed = memory.record(obj, pose(0.1, "obs"), kind="observation", evidence=(evidence("obs"),))
    assert memory.location(world_id="one", object_id="cup", at=NOW, known_at=NOW, estimated=False, reader=Allow()).status == "observed"
    # Occlusion/unseen movement does not invent a current pose.
    unseen = memory.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=True, reader=Allow())
    assert unseen.status == "unknown" and unseen.evidence
    assert memory.location(world_id="one", object_id="missing", at=NOW, known_at=NOW, estimated=True, reader=Allow()).status == "unknown"
    estimated = memory.recalibrate(observed, pose(0.4, "map-2", NOW + timedelta(minutes=1)), evidence=(evidence("map-2"),), estimate_expires_at=NOW + timedelta(minutes=2))
    assert observed.payload["pose"]["metres"][0] == 0.1
    answer = memory.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=True, reader=Allow())
    assert (answer.status, answer.pose.metres[0]) == ("estimated", 0.4)
    assert memory.location(world_id="two", object_id="cup", at=NOW, known_at=NOW, estimated=True, reader=Allow()).status == "unknown"
    assert observed.record_digest in {ref.digest for ref in estimated.source_refs}
    assert memory.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=False, reader=Allow()).pose.metres[0] == 0.1


def test_location_rechecks_exact_evidence_access():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    memory.record(WorldObject(world_id="one", object_id="cube", class_label="cube"), pose(0.0, "1"), kind="observation", evidence=(evidence("cube"),))
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.location(world_id="one", object_id="cube", at=NOW, known_at=NOW, estimated=False, reader=Deny())


def test_estimates_require_a_future_expiry():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    obj = WorldObject(world_id="one", object_id="cube", class_label="cube")
    estimate_pose = pose(0.1, "estimate")
    with pytest.raises(ValueError, match="require an expiry"):
        memory.record(obj, estimate_pose, kind="estimate", evidence=(evidence("estimate"),))
    with pytest.raises(ValueError, match="must follow"):
        memory.record(obj, estimate_pose, kind="estimate", evidence=(evidence("estimate"),), estimate_expires_at=NOW)


def test_class_lookup_omits_unauthorized_and_future_objects_from_ambiguity():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    memory.record(WorldObject(world_id="one", object_id="visible", class_label="cup"), pose(0.1, "visible"), kind="observation", evidence=(evidence("visible"),))
    memory.record(WorldObject(world_id="one", object_id="hidden", class_label="cup"), pose(0.2, "hidden"), kind="observation", evidence=(evidence("hidden"),))
    memory.record(WorldObject(world_id="one", object_id="future", class_label="cup"), pose(0.3, "future", NOW + timedelta(minutes=1)), kind="observation", evidence=(evidence("future"),))
    answer = memory.locate_class(world_id="one", class_label="cup", at=NOW, known_at=NOW, reader=DenyResource("hidden"))
    assert (answer.status, answer.pose.metres[0]) == ("observed", 0.1)


def test_canonical_store_reopens_world_isolation_and_expiring_estimate(tmp_path):
    path = tmp_path / "semantic.json"
    memory = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    obj = WorldObject(world_id="one", object_id="cup", class_label="cup")
    memory.record(obj, pose(0.1, "obs"), kind="observation", evidence=(evidence("obs"),))
    memory.record(obj, pose(0.4, "estimate", NOW + timedelta(minutes=1)), kind="estimate", evidence=(evidence("estimate"),), estimate_expires_at=NOW + timedelta(minutes=2))
    reopened = TabletopWorldMemory(str(path), tenant_id="t", project_id="p")
    assert reopened.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=True, reader=Allow()).status == "estimated"
    assert reopened.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=3), estimated=True, reader=Allow()).status == "stale"
    assert reopened.location(world_id="other", object_id="cup", at=NOW, known_at=NOW, estimated=True, reader=Allow()).status == "unknown"
    with pytest.raises(PermissionError, match="world-memory access denied"):
        reopened.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=3), estimated=True, reader=Deny())


def test_late_arriving_observation_is_not_known_before_trusted_recorded_time(tmp_path):
    physical_time = NOW - timedelta(minutes=10)
    received_time = NOW
    memory = TabletopWorldMemory(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p", clock=lambda: received_time)
    memory.record(
        WorldObject(world_id="one", object_id="cup", class_label="cup"),
        pose(0.1, "late", physical_time),
        kind="observation",
        evidence=(evidence("late"),),
    )
    assert memory.location(world_id="one", object_id="cup", at=physical_time, known_at=physical_time, estimated=False, reader=Allow()).status == "unknown"
    assert memory.location(world_id="one", object_id="cup", at=physical_time, known_at=received_time, estimated=False, reader=Allow()).status == "observed"


def test_already_open_reader_refreshes_committed_writer_records(tmp_path):
    path = tmp_path / "semantic.json"
    reader = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    writer = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    writer.record(WorldObject(world_id="one", object_id="cup", class_label="cup"), pose(0.1, "1"), kind="observation", evidence=(evidence("writer"),))
    assert reader.location(world_id="one", object_id="cup", at=NOW, known_at=NOW, estimated=False, reader=Allow()).status == "observed"


def test_pre_projection_world_rows_migrate_idempotently_without_rewriting_history(tmp_path):
    path = tmp_path / "semantic.json"
    original = TabletopWorldMemory(clock=lambda: NOW).record(
        WorldObject(world_id="one", object_id="cup", class_label="cup"),
        pose(0.1, "legacy"),
        kind="observation",
        evidence=(evidence("legacy"),),
    )
    legacy_store = JsonSemanticStore(path)
    with legacy_store.transaction():
        legacy_store.put(
            SemanticRow(
                tenant_id="t", project_id="p", record_kind="robotics_world_record",
                record_id=original.record_digest, revision=1,
                payload={"record": original.model_dump(mode="json")},
                created_at=original.recorded_at, updated_at=original.recorded_at,
            ),
            expected_revision=0,
        )
    migrated = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    assert migrated.location(world_id="one", object_id="cup", at=NOW, known_at=NOW, estimated=False, reader=Allow()).status == "observed"
    projection_rows = migrated._projection.store.list("t", "p", kind="temporal_record")
    assert [row.payload["record"]["record_digest"] for row in projection_rows] == [original.record_digest]
    assert TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)._projection.store.list("t", "p", kind="temporal_record") == projection_rows

    conflicting_path = tmp_path / "conflicting-semantic.json"
    conflict_store = JsonSemanticStore(conflicting_path)
    conflicting = TabletopWorldMemory(clock=lambda: NOW).record(
        WorldObject(world_id="one", object_id="different", class_label="cup"),
        pose(0.2, "conflict"), kind="observation", evidence=(evidence("conflict"),),
    )
    with conflict_store.transaction():
        conflict_store.put(SemanticRow(tenant_id="t", project_id="p", record_kind="robotics_world_record", record_id=original.record_digest, revision=1, payload={"record": original.model_dump(mode="json")}, created_at=original.recorded_at, updated_at=original.recorded_at), expected_revision=0)
        conflict_store.put(SemanticRow(tenant_id="t", project_id="p", record_kind="temporal_record", record_id=original.record_digest, revision=1, payload={"record": conflicting.model_dump(mode="json")}, created_at=conflicting.recorded_at, updated_at=conflicting.recorded_at), expected_revision=0)
    with pytest.raises(ValueError, match="conflicting temporal record replay"):
        TabletopWorldMemory(str(conflicting_path), tenant_id="t", project_id="p", clock=lambda: NOW)


def test_projection_stales_exact_source_and_map_dependencies_through_registry_after_reopen(tmp_path):
    path = tmp_path / "semantic.json"
    at = NOW + timedelta(minutes=1)
    memory = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    first = WorldObject(world_id="one", object_id="cup-a", class_label="cup")
    second = WorldObject(world_id="two", object_id="cup-b", class_label="cup")
    unaffected = WorldObject(world_id="three", object_id="cup-c", class_label="cup")
    memory.record(first, pose(0.1, "obs-a"), kind="observation", evidence=(evidence("obs-a"),))
    estimate_a = memory.record(first, pose(0.2, "estimate-a", at), kind="estimate", evidence=(evidence("estimate-a"),), estimate_expires_at=at + timedelta(minutes=10), recorded_at=at)
    memory.record(second, pose(0.3, "obs-b"), kind="observation", evidence=(evidence("obs-b"),))
    estimate_b = memory.record(second, pose(0.4, "estimate-b", at), kind="estimate", evidence=(evidence("estimate-b"),), estimate_expires_at=at + timedelta(minutes=10), recorded_at=at)
    memory.record(unaffected, pose(0.5, "obs-c"), kind="observation", evidence=(evidence("obs-c"),))
    memory.record(unaffected, pose(0.6, "estimate-c", at), kind="estimate", evidence=(evidence("estimate-c"),), estimate_expires_at=at + timedelta(minutes=10), recorded_at=at)
    memory.rebuild_projection(valid_at=at, known_at=at, at=at)
    before = {state.entity_id: state.revision for state in memory._projection.current_state("robotics-world-memory")}
    source_a = evidence("estimate-a")
    map_b = memory.map_revision_ref("two", "map-1")
    assert {ref.digest for ref in memory.invalidate_map_revision(source_a, reason="camera calibration withdrawn", at=at)} == {estimate_a.record_digest}
    assert {ref.digest for ref in memory.invalidate_map_revision(map_b, reason="map revision withdrawn", at=at)} == {estimate_b.record_digest}
    registry = DomainExtensionRegistry()
    register_world_memory_extension(registry, memory, Allow())
    stale_dispatch = registry.dispatch(
        "robotics.world-memory.location", "1.0.0", ((memory.record_ref(estimate_a), estimate_a.payload),),
        config={"world_id": "one", "object_id": "cup-a", "at": at.isoformat(), "known_at": at.isoformat(), "estimated": True}, authorizer=Allow(),
    )
    assert stale_dispatch.output["status"] == "stale"
    memory.recompute_projection(valid_at=at, known_at=at, at=at)
    reopened = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    later = at + timedelta(minutes=1)
    assert reopened.location(world_id="one", object_id="cup-a", at=later, known_at=later, estimated=True, reader=Allow()).status == "stale"
    assert reopened.location(world_id="two", object_id="cup-b", at=later, known_at=later, estimated=True, reader=Allow()).status == "stale"
    after = {state.entity_id: state.revision for state in reopened._projection.current_state("robotics-world-memory")}
    assert after["cup-a"] == before["cup-a"] + 1
    assert after["cup-b"] == before["cup-b"] + 1
    assert after["cup-c"] == before["cup-c"]
    assert reopened.location(world_id="three", object_id="cup-c", at=later, known_at=later, estimated=True, reader=Allow()).status == "estimated"


def test_canonical_dependency_invalidation_respects_historical_cutoffs_and_rebuild(tmp_path):
    path = tmp_path / "semantic.json"
    initial_at = NOW + timedelta(minutes=1)
    invalidated_at = NOW + timedelta(minutes=5)
    memory = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    obj = WorldObject(world_id="one", object_id="cup", class_label="cup")
    memory.record(obj, pose(0.1, "obs"), kind="observation", evidence=(evidence("obs"),))
    estimate = memory.record(
        obj, pose(0.2, "estimate", initial_at), kind="estimate", evidence=(evidence("estimate-camera"),),
        estimate_expires_at=NOW + timedelta(hours=1),
    )
    memory.rebuild_projection(valid_at=initial_at, known_at=initial_at, at=initial_at)
    assert memory.location(world_id="one", object_id="cup", at=initial_at, known_at=initial_at, estimated=True, reader=Allow()).status == "estimated"
    memory.invalidate_map_revision(evidence("estimate-camera"), reason="camera revoked", at=invalidated_at)
    # The persisted event is not visible before either its effective or known time.
    assert memory.location(world_id="one", object_id="cup", at=initial_at, known_at=initial_at, estimated=True, reader=Allow()).status == "estimated"
    historical = memory.recompute_projection(valid_at=initial_at, known_at=initial_at, at=invalidated_at)
    assert historical.affected_outputs == ()
    assert memory._projection.record_ref(estimate) in memory._projection.dirty_outputs("robotics-world-memory")
    assert memory.location(world_id="one", object_id="cup", at=initial_at, known_at=initial_at, estimated=True, reader=Allow()).status == "estimated"
    memory.recompute_projection(valid_at=invalidated_at, known_at=invalidated_at, at=invalidated_at)
    assert memory.location(world_id="one", object_id="cup", at=invalidated_at, known_at=invalidated_at, estimated=True, reader=Allow()).status == "stale"
    memory.rebuild_projection(valid_at=invalidated_at, known_at=invalidated_at, at=invalidated_at)
    assert memory.location(world_id="one", object_id="cup", at=invalidated_at, known_at=invalidated_at, estimated=True, reader=Allow()).status == "stale"
    assert any(estimate.record_digest in state.record_digests for state in memory._projection.current_state("robotics-world-memory") if state.entity_id == "cup")


def test_recalibration_uses_canonical_parent_for_immediate_transitive_invalidation(tmp_path):
    path = tmp_path / "semantic.json"
    estimate_at = NOW + timedelta(minutes=1)
    invalidated_at = NOW + timedelta(minutes=2)
    memory = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    obj = WorldObject(world_id="one", object_id="cup", class_label="cup")
    observation = memory.record(obj, pose(0.1, "sensor"), kind="observation", evidence=(evidence("sensor"),))
    estimate = memory.recalibrate(
        observation, pose(0.2, "calibrated", estimate_at), evidence=(evidence("calibration"),),
        estimate_expires_at=NOW + timedelta(hours=1),
    )
    memory.rebuild_projection(valid_at=estimate_at, known_at=estimate_at, at=estimate_at)
    assert memory.location(world_id="one", object_id="cup", at=estimate_at, known_at=estimate_at, estimated=True, reader=Allow()).status == "estimated"
    affected = memory.invalidate_map_revision(evidence("sensor"), reason="sensor withdrawn", at=invalidated_at)
    assert {ref.digest for ref in affected} == {observation.record_digest, estimate.record_digest}
    assert memory.location(world_id="one", object_id="cup", at=estimate_at, known_at=estimate_at, estimated=True, reader=Allow()).status == "estimated"
    assert memory.location(world_id="one", object_id="cup", at=invalidated_at, known_at=invalidated_at, estimated=True, reader=Allow()).status == "stale"
    memory.recompute_projection(valid_at=invalidated_at, known_at=invalidated_at, at=invalidated_at)
    assert memory.location(world_id="one", object_id="cup", at=invalidated_at, known_at=invalidated_at, estimated=True, reader=Allow()).status == "stale"
    memory.rebuild_projection(valid_at=invalidated_at, known_at=invalidated_at, at=invalidated_at)
    assert memory.location(world_id="one", object_id="cup", at=invalidated_at, known_at=invalidated_at, estimated=True, reader=Allow()).status == "stale"


def test_reopen_repairs_legacy_public_parent_alias_without_rewriting_records(tmp_path):
    path = tmp_path / "semantic.json"
    estimate_at = NOW + timedelta(minutes=1)
    legacy = TabletopWorldMemory(clock=lambda: NOW)
    obj = WorldObject(world_id="one", object_id="cup", class_label="cup")
    observation = legacy.record(obj, pose(0.1, "sensor"), kind="observation", evidence=(evidence("legacy-sensor"),))
    legacy_estimate = legacy.record(
        obj, pose(0.2, "legacy-estimate", estimate_at), kind="estimate",
        evidence=(legacy.record_ref(observation),), estimate_expires_at=NOW + timedelta(hours=1),
    )
    service = TemporalProjectionService(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    for record in (observation, legacy_estimate):
        service.ingest(record, at=NOW, writer=AllowProjection())
    repaired = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    assert {record.record_digest for record in repaired._records} == {observation.record_digest, legacy_estimate.record_digest}
    repaired.rebuild_projection(valid_at=estimate_at, known_at=estimate_at, at=estimate_at)
    invalidated_at = NOW + timedelta(minutes=2)
    assert {ref.digest for ref in repaired.invalidate_map_revision(evidence("legacy-sensor"), reason="sensor withdrawn", at=invalidated_at)} == {observation.record_digest, legacy_estimate.record_digest}
    assert repaired.location(world_id="one", object_id="cup", at=invalidated_at, known_at=invalidated_at, estimated=True, reader=Allow()).status == "stale"


def test_tabletop_episode_registry_dispatch_and_no_identity_merge():
    memory, episode = tabletop_episode_fixture(NOW)
    registry = DomainExtensionRegistry()
    register_world_memory_extension(registry, memory, Allow())
    initial = episode["left_initial"]
    result = registry.dispatch("robotics.world-memory.location", "1.0.0", ((memory.record_ref(initial), initial.payload),), config={"world_id": "table-a", "object_id": "cup-left", "at": NOW.isoformat(), "known_at": NOW.isoformat(), "estimated": False}, authorizer=Allow())
    assert result.output["status"] == "observed"
    assert "__krail_lineage_refs__" not in result.output
    assert {ref.exact_key for ref in result.input_refs} == {memory.record_ref(initial).exact_key, initial.source_refs[0].exact_key}
    # Similar cups stay ambiguous until an exact object ID is supplied.
    assert memory.locate_class(world_id="table-a", class_label="red-cup", at=NOW, known_at=NOW, reader=Allow()).status == "ambiguous"
    assert memory.identity_candidates(world_id="table-a", appearance_ref=episode["initial_appearance"].appearance_ref, at=NOW, known_at=NOW, reader=Allow()) == (episode["ambiguous_identity"],)
    assert memory.resolved_identity(world_id="table-a", candidate_ref=episode["ambiguous_identity"].candidate_ref, at=NOW, known_at=NOW, reader=Allow()) is None
    assert memory.resolved_identity(world_id="table-a", candidate_ref=episode["ambiguous_identity"].candidate_ref, at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=3), reader=Allow()) == episode["identity_resolution"]
    region_answer = memory.objects_in_region(world_id="table-a", region_ref=episode["table_region"].region_ref, at=NOW, known_at=NOW, reader=Allow())
    assert region_answer.status == "current" and region_answer.object_refs == (memory.record_ref(initial),)
    assert memory.spatial_relations(world_id="table-a", session_id="session-1", at=NOW, known_at=NOW, reader=Allow()) == (episode["left_support"],)
    expired = memory.location(world_id="table-a", object_id="cup-left", at=NOW + timedelta(minutes=2, seconds=1), known_at=NOW + timedelta(minutes=2, seconds=1), estimated=True, reader=Allow())
    assert expired.status == "stale" and expired.evidence
    reobserved = memory.location(world_id="table-a", object_id="cup-left", at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=3), estimated=False, reader=Allow())
    assert (reobserved.status, reobserved.pose.map_revision) == ("observed", "table-map-2")
    denied_registry = DomainExtensionRegistry()
    register_world_memory_extension(denied_registry, memory, Deny())
    with pytest.raises(PermissionError, match="world-memory access denied"):
        denied_registry.dispatch("robotics.world-memory.location", "1.0.0", ((memory.record_ref(initial), initial.payload),), config={"world_id": "table-a", "object_id": "cup-left", "at": NOW.isoformat(), "known_at": NOW.isoformat(), "estimated": False}, authorizer=Allow())

    with pytest.raises(ValueError, match="estimated must be a boolean"):
        registry.dispatch("robotics.world-memory.location", "1.0.0", ((memory.record_ref(initial), initial.payload),), config={"world_id": "table-a", "object_id": "cup-left", "at": NOW.isoformat(), "known_at": NOW.isoformat(), "estimated": "false"}, authorizer=Allow())


def test_authorization_is_rechecked_before_location_scene_and_ambiguity_outputs():
    memory, scene = tabletop_fixture(NOW)
    left = next(record for record in memory._records if record.entity_id == "cup-left")
    assert scene.object_refs
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.location(world_id="table-a", object_id="cup-left", at=NOW, known_at=NOW, estimated=False, reader=RevokeAfter(2))
    right = next(record for record in memory._records if record.entity_id == "cup-right")
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.scene(world_id="table-a", scene_id="revoked", records=(left, right), reader=RevokeAfter(4))
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.locate_class(world_id="table-a", class_label="red-cup", at=NOW, known_at=NOW, reader=RevokeAfter(4))


def test_scene_evidence_is_authorized_and_episode_handles_hour_boundary():
    memory, episode = tabletop_episode_fixture(datetime(2026, 9, 7, 12, 59, tzinfo=UTC))
    initial = episode["left_initial"]
    right = next(record for record in memory._records if record.entity_id == "cup-right")
    assert episode["left_reobserved"].valid_from == datetime(2026, 9, 7, 13, 2, tzinfo=UTC)
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.scene(world_id="table-a", scene_id="restricted", records=(initial, right), reader=Deny())
    other = memory.record(WorldObject(world_id="other", object_id="cup", class_label="cup"), pose(0.0, "other"), kind="observation", evidence=(evidence("other"),))
    with pytest.raises(ValueError, match="exact world"):
        memory.scene(world_id="table-a", scene_id="mixed", records=(initial, other), reader=Allow())


def test_scene_and_episode_authorize_supplied_evidence_before_persisting(tmp_path):
    path = tmp_path / "semantic.json"
    memory, _ = tabletop_fixture(NOW, str(path), tenant_id="t", project_id="p")
    left = next(record for record in memory._records if record.entity_id == "cup-left")
    private = evidence("private-denied")
    scenes_before = memory._projection.store.list("t", "p", kind="robotics_scene_snapshot")
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.scene(
            world_id="table-a", scene_id="private-evidence", records=(left,),
            reader=DenyResource("private-denied"), evidence_refs=(private,),
        )
    assert memory._projection.store.list("t", "p", kind="robotics_scene_snapshot") == scenes_before
    scene = memory.scene(world_id="table-a", scene_id="public", records=(left,), reader=Allow())
    episodes_before = memory._projection.store.list("t", "p", kind="robotics_episode")
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.episode(
            world_id="table-a", session_id="default", episode_id="private-evidence",
            scene_refs=(scene.scene_ref,), reader=DenyResource("private-denied"),
            valid_from=NOW, evidence_refs=(private,),
        )
    assert memory._projection.store.list("t", "p", kind="robotics_episode") == episodes_before


def test_scene_and_episode_rollback_when_final_authorization_is_revoked(tmp_path):
    path = tmp_path / "semantic.json"
    memory, _ = tabletop_fixture(NOW, str(path), tenant_id="t", project_id="p")
    left = next(record for record in memory._records if record.entity_id == "cup-left")
    scenes_before = memory._projection.store.list("t", "p", kind="robotics_scene_snapshot")
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.scene(world_id="table-a", scene_id="revoked-final", records=(left,), reader=RevokeAfter(5))
    reopened = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    assert reopened._projection.store.list("t", "p", kind="robotics_scene_snapshot") == scenes_before
    scene = memory.scene(world_id="table-a", scene_id="public-final", records=(left,), reader=Allow())
    episodes_before = memory._projection.store.list("t", "p", kind="robotics_episode")
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.episode(
            world_id="table-a", session_id="default", episode_id="revoked-final",
            scene_refs=(scene.scene_ref,), reader=RevokeAfter(7), valid_from=NOW,
        )
    reopened = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    assert reopened._projection.store.list("t", "p", kind="robotics_episode") == episodes_before


def test_scene_and_episode_digests_bind_full_exact_refs_and_membership():
    memory, _ = tabletop_fixture(NOW)
    left = next(record for record in memory._records if record.entity_id == "cup-left")
    right = next(record for record in memory._records if record.entity_id == "cup-right")
    same_digest = "sha256:" + "a" * 64
    ref_a = ResourceRef(authority="fixture://one", resource_type="annotation", resource_id="same", version="1", digest=same_digest)
    ref_b = ResourceRef(authority="fixture://two", resource_type="annotation", resource_id="different", version="1", digest=same_digest)
    first = memory.scene(world_id="table-a", scene_id="exact-ref", records=(left,), reader=Allow(), valid_at=NOW, recorded_at=NOW, evidence_refs=(ref_a,))
    second = memory.scene(world_id="table-a", scene_id="exact-ref", records=(left,), reader=Allow(), valid_at=NOW, recorded_at=NOW, evidence_refs=(ref_b,))
    assert first.scene_ref.digest != second.scene_ref.digest
    all_refs = (memory.record_ref(left), memory.record_ref(right))
    only_left = memory.scene(world_id="table-a", scene_id="membership", records=(left,), reader=Allow(), valid_at=NOW, recorded_at=NOW, evidence_refs=all_refs)
    only_right = memory.scene(world_id="table-a", scene_id="membership", records=(right,), reader=Allow(), valid_at=NOW, recorded_at=NOW, evidence_refs=all_refs)
    assert only_left.scene_ref.digest != only_right.scene_ref.digest
    forward = memory.episode(world_id="table-a", session_id="default", episode_id="ordering", scene_refs=(only_left.scene_ref, only_right.scene_ref), reader=Allow(), valid_from=NOW, recorded_at=NOW, evidence_refs=(only_left.scene_ref, only_right.scene_ref))
    reverse = memory.episode(world_id="table-a", session_id="default", episode_id="ordering", scene_refs=(only_right.scene_ref, only_left.scene_ref), reader=Allow(), valid_from=NOW, recorded_at=NOW, evidence_refs=(only_left.scene_ref, only_right.scene_ref))
    assert forward.episode_ref.digest != reverse.episode_ref.digest


def test_scene_and_episode_historical_queries_hide_future_constituents():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    obj = WorldObject(world_id="table-a", object_id="cup", class_label="cup")
    future = memory.record(obj, pose(0.3, "future", NOW + timedelta(minutes=2)), kind="observation", evidence=(evidence("future"),), recorded_at=NOW)
    with pytest.raises(ValueError, match="cannot precede"):
        memory.scene(world_id="table-a", scene_id="impossible", records=(future,), reader=Allow(), valid_at=NOW, recorded_at=NOW)
    opening = memory.record(obj, pose(0.1, "opening"), kind="observation", evidence=(evidence("opening"),), recorded_at=NOW)
    opening_scene = memory.scene(world_id="table-a", scene_id="opening", records=(opening,), reader=Allow(), valid_at=NOW, recorded_at=NOW)
    future_scene = memory.scene(world_id="table-a", scene_id="future", records=(future,), reader=Allow(), valid_at=NOW + timedelta(minutes=2), recorded_at=NOW)
    incomplete = memory.episode(world_id="table-a", session_id="default", episode_id="incomplete", scene_refs=(opening_scene.scene_ref, future_scene.scene_ref), reader=Allow(), valid_from=NOW, recorded_at=NOW)
    assert memory.episode_at(world_id="table-a", session_id="default", at=NOW + timedelta(minutes=1), known_at=NOW, reader=Allow()) is None
    completed = memory.episode(world_id="table-a", session_id="default", episode_id="completed", scene_refs=(opening_scene.scene_ref, future_scene.scene_ref), reader=Allow(), valid_from=NOW, completed_at=NOW + timedelta(minutes=2), recorded_at=NOW)
    assert memory.episode_at(world_id="table-a", session_id="default", at=NOW + timedelta(minutes=1), known_at=NOW, reader=Allow()) is None
    assert memory.episode_at(world_id="table-a", session_id="default", at=NOW + timedelta(minutes=2), known_at=NOW, reader=Allow()).episode_ref == completed.episode_ref
    assert incomplete.episode_ref != completed.episode_ref


def test_persisted_appearance_gallery_and_identity_candidates_are_bitemporal_and_never_merge(tmp_path):
    path = tmp_path / "semantic.json"
    memory, _ = tabletop_fixture(NOW, str(path), tenant_id="t", project_id="p")
    left = next(record for record in memory._records if record.entity_id == "cup-left")
    right = next(record for record in memory._records if record.entity_id == "cup-right")
    left_object = WorldObject.model_validate(left.payload["object"])
    appearance = memory.record_appearance(
        left_object, left, asset_ref=evidence("left-image"), crop_ref=evidence("left-crop"),
        mask_ref=evidence("left-mask"), viewpoint="overhead", context="tabletop",
        descriptor_model="fixture-descriptor", descriptor_version="1", quality=0.9,
        occluded=False, revision="appearance-1", recorded_at=NOW + timedelta(minutes=1),
    )
    candidate = memory.propose_identity_candidates(
        appearance, session_id="session-1", candidate_id="red-cup-association",
        candidate_object_refs=(memory.record_ref(left), memory.record_ref(right)),
        evidence_refs=(evidence("association"),), association_basis="same class and bounded visual features",
        expires_at=NOW + timedelta(minutes=3), recorded_at=NOW + timedelta(minutes=2),
    )
    assert memory.appearance_gallery(world_id="table-a", object_id="cup-left", at=NOW, known_at=NOW, reader=Allow()) == ()
    assert memory.identity_candidates(world_id="table-a", appearance_ref=appearance.appearance_ref, at=NOW, known_at=NOW, reader=Allow()) == ()
    memory.rebuild_projection(valid_at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), at=NOW + timedelta(minutes=2))
    reopened = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    gallery = reopened.appearance_gallery(world_id="table-a", object_id="cup-left", at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Allow())
    assert gallery == (appearance,) and gallery[0].asset_ref == evidence("left-image") and gallery[0].crop_ref == evidence("left-crop") and gallery[0].mask_ref == evidence("left-mask")
    assert reopened.identity_candidates(world_id="table-a", appearance_ref=appearance.appearance_ref, at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Allow()) == (candidate,)
    assert reopened.identity_candidates(world_id="table-a", appearance_ref=appearance.appearance_ref, at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=3), reader=Allow()) == ()
    assert reopened.appearance_gallery(world_id="other", object_id="cup-left", at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Allow()) == ()
    assert reopened.identity_candidates(world_id="other", appearance_ref=appearance.appearance_ref, at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Allow()) == ()
    with pytest.raises(ValueError, match="gallery limit"):
        reopened.appearance_gallery(world_id="table-a", object_id="cup-left", at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Allow(), limit=9)
    assert reopened.locate_class(world_id="table-a", class_label="red-cup", at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Allow()).status == "ambiguous"
    with pytest.raises(PermissionError, match="world-memory access denied"):
        reopened.appearance_gallery(world_id="table-a", object_id="cup-left", at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Deny())
    with pytest.raises(PermissionError, match="world-memory access denied"):
        reopened.identity_candidates(world_id="table-a", appearance_ref=appearance.appearance_ref, at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Deny())


def test_persisted_regions_relations_and_bounded_same_frame_membership(tmp_path):
    path = tmp_path / "semantic.json"
    memory, _ = tabletop_fixture(NOW, str(path), tenant_id="t", project_id="p")
    left = next(record for record in memory._records if record.entity_id == "cup-left")
    right = next(record for record in memory._records if record.entity_id == "cup-right")
    region = memory.record_region(
        world_id="table-a", session_id="session-1", place_id="table", region_id="left-zone",
        frame_id="table", map_revision="table-map-1", min_metres=(0.0, 0.0, -0.1),
        max_metres=(0.2, 0.4, 0.1), evidence_refs=(evidence("left-zone"),), revision="1",
        valid_from=NOW, recorded_at=NOW,
    )
    relation = memory.record_relation(
        world_id="table-a", session_id="session-1", relation_id="left-supported-by-table",
        relation_type="support", subject_ref=memory.record_ref(left), object_ref=region.region_ref,
        evidence_refs=(evidence("left-support"),), revision="1", valid_from=NOW, recorded_at=NOW,
    )
    containment = memory.record_relation(
        world_id="table-a", session_id="session-1", relation_id="left-contained-in-zone",
        relation_type="containment", subject_ref=memory.record_ref(left), object_ref=region.region_ref,
        evidence_refs=(evidence("left-containment"),), revision="1", valid_from=NOW, recorded_at=NOW,
    )
    attachment = memory.record_relation(
        world_id="table-a", session_id="session-1", relation_id="left-attached-right",
        relation_type="attachment", subject_ref=memory.record_ref(left), object_ref=memory.record_ref(right),
        evidence_refs=(evidence("left-attachment"),), revision="1", valid_from=NOW, recorded_at=NOW,
    )
    answer = memory.objects_in_region(world_id="table-a", region_ref=region.region_ref, at=NOW, known_at=NOW, reader=Allow())
    assert answer.status == "current" and answer.object_refs == (memory.record_ref(left),) and answer.evidence
    assert memory.action_freshness(world_id="table-a", object_id="cup-left", at=NOW, known_at=NOW, reader=Allow(), required_frame_id="table", required_map_revision="table-map-1", region_ref=region.region_ref).status == "usable"
    assert {item.relation_type for item in memory.spatial_relations(world_id="table-a", session_id="session-1", at=NOW, known_at=NOW, reader=Allow())} == {relation.relation_type, containment.relation_type, attachment.relation_type}
    memory.rebuild_projection(valid_at=NOW, known_at=NOW, at=NOW)
    reopened = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    assert reopened.objects_in_region(world_id="table-a", region_ref=region.region_ref, at=NOW, known_at=NOW, reader=Allow()).object_refs == (reopened.record_ref(left),)
    assert reopened.objects_in_region(world_id="other", region_ref=region.region_ref, at=NOW, known_at=NOW, reader=Allow()).status == "unknown"
    reopened.invalidate_map_revision(evidence("left-zone"), reason="region calibration withdrawn", at=NOW + timedelta(minutes=1))
    assert reopened.objects_in_region(world_id="table-a", region_ref=region.region_ref, at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), reader=Allow()).status == "stale"
    assert reopened.action_freshness(world_id="table-a", object_id="cup-left", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), reader=Allow(), region_ref=region.region_ref).status == "needs-refresh"
    with pytest.raises(PermissionError, match="world-memory access denied"):
        reopened.objects_in_region(world_id="table-a", region_ref=region.region_ref, at=NOW, known_at=NOW, reader=Deny())
    # A frame mismatch is not transformed or guessed; it makes this bounded
    # membership query abstain instead of returning a partial assertion.
    reopened.record(WorldObject(world_id="table-a", object_id="camera-frame", class_label="cup"), Pose(frame_id="camera", metres=(0.1, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=NOW, uncertainty_metres=0.01, revision="camera", map_revision="table-map-1"), kind="observation", evidence=(evidence("camera-frame"),), recorded_at=NOW)
    assert reopened.objects_in_region(world_id="table-a", region_ref=region.region_ref, at=NOW, known_at=NOW, reader=Allow()).status == "unknown"
    estimate = reopened.record(WorldObject.model_validate(left.payload["object"]), pose(0.1, "left-estimate", NOW + timedelta(minutes=1)), kind="estimate", evidence=(evidence("left-estimate"),), estimate_expires_at=NOW + timedelta(minutes=2), recorded_at=NOW + timedelta(minutes=1))
    assert estimate.payload["state"] == "estimate"
    assert reopened.objects_in_region(world_id="table-a", region_ref=region.region_ref, at=NOW + timedelta(minutes=2), known_at=NOW + timedelta(minutes=2), reader=Allow()).status == "stale"


def test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing(tmp_path):
    path = tmp_path / "semantic.json"
    memory, fixture = tabletop_episode_fixture(NOW, str(path), tenant_id="t", project_id="p")
    opening = fixture["opening_scene"]
    occluded = fixture["occluded_scene"]
    final = fixture["final_scene"]
    episode = fixture["episode"]
    right_ref = next(ref for ref in opening.object_refs if "cup-right" in ref.resource_id)
    assert right_ref in occluded.object_refs and right_ref in final.object_refs
    reopened = TabletopWorldMemory(str(path), tenant_id="t", project_id="p", clock=lambda: NOW)
    assert reopened.scene_at(world_id="table-a", place_id="table", session_id="session-1", at=NOW, known_at=NOW, reader=Allow()).scene_ref == opening.scene_ref
    assert reopened.scene_at(world_id="table-a", place_id="table", session_id="session-1", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), reader=Allow()).scene_ref == occluded.scene_ref
    assert reopened.scene_at(world_id="table-a", place_id="table", session_id="session-1", at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=3), reader=Allow()).scene_ref == final.scene_ref
    assert reopened.scene_at(world_id="table-a", place_id="table", session_id="session-1", at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=1), reader=Allow()).scene_ref == occluded.scene_ref
    history = reopened.object_history(world_id="table-a", object_id="cup-left", valid_from=NOW, valid_to=NOW + timedelta(minutes=4), known_at=NOW + timedelta(minutes=3), reader=Allow())
    assert tuple(record.payload["state"] for record in history.records) == ("observation", "estimate", "observation")
    assert history.evidence
    assert reopened.episode_at(world_id="table-a", session_id="session-1", at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=3), reader=Allow()).episode_ref == episode.episode_ref
    assert reopened.episode_at(world_id="table-a", session_id="session-1", at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=1), reader=Allow()) is None
    assert reopened.scene_at(world_id="other", place_id="table", session_id="session-1", at=NOW + timedelta(minutes=3), known_at=NOW + timedelta(minutes=3), reader=Allow()) is None
    with pytest.raises(PermissionError, match="world-memory access denied"):
        reopened.scene_at(world_id="table-a", place_id="table", session_id="session-1", at=NOW, known_at=NOW, reader=Deny())
    with pytest.raises(PermissionError, match="world-memory access denied"):
        reopened.object_history(world_id="table-a", object_id="cup-left", valid_from=NOW, valid_to=None, known_at=NOW + timedelta(minutes=3), reader=Deny())


def test_scene_and_episode_do_not_become_known_before_their_raw_records(tmp_path):
    memory = TabletopWorldMemory(str(tmp_path / "semantic.json"), tenant_id="t", project_id="p", clock=lambda: NOW)
    record = memory.record(
        WorldObject(world_id="table-a", object_id="cup", class_label="cup"),
        pose(0.1, "late", NOW), kind="observation", evidence=(evidence("late"),),
        recorded_at=NOW + timedelta(minutes=5),
    )
    scene = memory.scene(
        world_id="table-a", scene_id="malformed-early-snapshot", records=(record,), reader=Allow(),
        session_id="session-1", valid_at=NOW, recorded_at=NOW,
    )
    memory.episode(
        world_id="table-a", session_id="session-1", episode_id="malformed-early-episode",
        scene_refs=(scene.scene_ref,), reader=Allow(), valid_from=NOW, recorded_at=NOW,
    )
    assert memory.scene_at(world_id="table-a", place_id="table", session_id="session-1", at=NOW, known_at=NOW, reader=Allow()) is None
    assert memory.episode_at(world_id="table-a", session_id="session-1", at=NOW, known_at=NOW, reader=Allow()) is None
    assert memory.scene_at(world_id="table-a", place_id="table", session_id="session-1", at=NOW, known_at=NOW + timedelta(minutes=5), reader=Allow()).scene_ref == scene.scene_ref
