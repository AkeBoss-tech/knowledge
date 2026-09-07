from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from krail.provider.v1 import ResourceRef
from rail.extension_registry import DomainExtensionRegistry
from rail.robotics_world_memory import Pose, TabletopWorldMemory, WorldObject, robotics_world_extension, tabletop_fixture

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class Allow:
    def authorize(self, ref): pass


class Deny:
    def authorize(self, ref): raise PermissionError("revoked")


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
    memory = TabletopWorldMemory()
    obj = WorldObject(world_id="one", object_id="cup", class_label="cup")
    observed = memory.record(obj, pose(0.1, "obs"), kind="observation", evidence=(evidence("obs"),))
    assert memory.location(world_id="one", object_id="cup", at=NOW, known_at=NOW, estimated=False, reader=Allow()).status == "observed"
    # Occlusion/unseen movement does not invent a current pose.
    assert memory.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=True, reader=Allow()).status == "unknown"
    assert memory.location(world_id="one", object_id="missing", at=NOW, known_at=NOW, estimated=True, reader=Allow()).status == "unknown"
    estimated = memory.recalibrate(observed, pose(0.4, "map-2", NOW + timedelta(minutes=1)), evidence=(evidence("map-2"),))
    assert observed.payload["pose"]["metres"][0] == 0.1
    answer = memory.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=True, reader=Allow())
    assert (answer.status, answer.pose.metres[0]) == ("estimated", 0.4)
    assert memory.location(world_id="two", object_id="cup", at=NOW, known_at=NOW, estimated=True, reader=Allow()).status == "unknown"
    assert estimated.source_refs[-1].digest == observed.record_digest
    assert memory.location(world_id="one", object_id="cup", at=NOW + timedelta(minutes=1), known_at=NOW + timedelta(minutes=1), estimated=False, reader=Allow()).pose.metres[0] == 0.1


def test_location_rechecks_exact_evidence_access():
    memory = TabletopWorldMemory()
    memory.record(WorldObject(world_id="one", object_id="cube", class_label="cube"), pose(0.0, "1"), kind="observation", evidence=(evidence("cube"),))
    with pytest.raises(PermissionError, match="world-memory access denied"):
        memory.location(world_id="one", object_id="cube", at=NOW, known_at=NOW, estimated=False, reader=Deny())
