from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from krail.provider.v1 import ResourceRef
from rail.extension_registry import DomainExtensionRegistry
from rail.robotics_world_memory import Pose, TabletopWorldMemory, WorldObject, register_world_memory_extension, robotics_world_extension, tabletop_episode_fixture, tabletop_fixture

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class Allow:
    def authorize(self, ref): pass


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
    estimated = memory.recalibrate(observed, pose(0.4, "map-2", NOW + timedelta(minutes=1)), evidence=(evidence("map-2"),))
    assert observed.payload["pose"]["metres"][0] == 0.1
    answer = memory.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=True, reader=Allow())
    assert (answer.status, answer.pose.metres[0]) == ("estimated", 0.4)
    assert memory.location(world_id="two", object_id="cup", at=NOW, known_at=NOW, estimated=True, reader=Allow()).status == "unknown"
    assert estimated.source_refs[-1].digest == observed.record_digest
    assert memory.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=False, reader=Allow()).pose.metres[0] == 0.1


def test_location_rechecks_exact_evidence_access():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    memory.record(WorldObject(world_id="one", object_id="cube", class_label="cube"), pose(0.0, "1"), kind="observation", evidence=(evidence("cube"),))
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.location(world_id="one", object_id="cube", at=NOW, known_at=NOW, estimated=False, reader=Deny())


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
