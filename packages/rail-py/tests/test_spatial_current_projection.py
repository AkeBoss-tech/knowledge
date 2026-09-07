from datetime import UTC, datetime, timedelta
from hashlib import sha256

from krail.provider.v1 import ResourceRef
from rail.robotics_world_memory import Pose, TabletopWorldMemory, WorldObject
from rail.spatial_current_projection import SpatialCurrentProjection

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def ev(n):
    return ResourceRef(authority="fixture://spatial", resource_type="image", resource_id=n, version="1", digest="sha256:" + sha256(n.encode()).hexdigest())


def pose(x, rev, at=NOW):
    return Pose(frame_id="table", metres=(x, 0, 0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at, uncertainty_metres=.01, revision=rev, map_revision="map-1")


def populate(memory, world, count):
    return tuple(memory.record(WorldObject(world_id=world, object_id=f"o{i}", class_label="cup"), pose(i / count, str(i)), kind="observation", evidence=(ev(f"{world}-{i}"),)) for i in range(count))


def region(index, world="one"):
    return index.candidate_query(world_id=world, frame_id="table", map_revision="map-1", minimum=(0, 0, 0), maximum=(.02, 0, 0))


def test_rebuild_restart_and_local_move_have_measured_bounded_work():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    records = populate(memory, "one", 100)
    index = SpatialCurrentProjection(records, valid_at=NOW, known_at=NOW)
    first = region(index)
    assert first.status == "current-candidates"
    assert first.candidate_rows_read == 10 and first.cells_read == 1 and len(first.hits) == 3
    by_id = index.current_candidate(entity_authority="robotics://world/one", object_id="o42")
    assert by_id.candidate_rows_read == 1 and by_id.hits[0].object_id == "o42"

    later = NOW + timedelta(seconds=1)
    moved = memory.record(WorldObject(world_id="one", object_id="o0", class_label="cup"), pose(.9, "moved", later), kind="observation", evidence=(ev("moved"),))
    index.advance_snapshot(valid_at=later, known_at=later)
    work = index.apply(moved)
    assert work.history_rows_read == 2 and work.index_rows_touched == 2
    assert {hit.object_id for hit in region(index).hits} == {"o1", "o2"}

    restarted = SpatialCurrentProjection((*records, moved), valid_at=later, known_at=later)
    assert region(index) == region(restarted)


def test_duplicate_object_id_and_increasing_buckets_do_not_expand_local_update():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    one = memory.record(WorldObject(world_id="one", object_id="cup", class_label="cup"), pose(.01, "one"), kind="observation", evidence=(ev("one"),))
    other = memory.record(WorldObject(world_id="other", object_id="cup", class_label="cup"), pose(.01, "other"), kind="observation", evidence=(ev("other"),))
    background = tuple(memory.record(WorldObject(world_id="one", object_id=f"far-{i}", class_label="cup"), pose(float(i + 1), f"far-{i}"), kind="observation", evidence=(ev(f"far-{i}"),)) for i in range(500))
    index = SpatialCurrentProjection((one, other, *background), valid_at=NOW, known_at=NOW)
    later = NOW + timedelta(seconds=1)
    moved = memory.record(WorldObject(world_id="one", object_id="cup", class_label="cup"), pose(.5, "moved", later), kind="observation", evidence=(ev("moved"),))
    index.advance_snapshot(valid_at=later, known_at=later)
    assert index.apply(moved).index_rows_touched == 2
    assert region(index, "one").status == "current-candidates" and not region(index, "one").hits
    assert {hit.object_id for hit in region(index, "other").hits} == {"cup"}


def test_snapshot_must_be_advanced_and_region_cell_budget_is_bounded():
    memory = TabletopWorldMemory(clock=lambda: NOW)
    record = memory.record(WorldObject(world_id="one", object_id="cup", class_label="cup"), pose(.01, "one"), kind="observation", evidence=(ev("one"),))
    index = SpatialCurrentProjection((record,), valid_at=NOW, known_at=NOW)
    index.valid_at = NOW + timedelta(seconds=1)
    try:
        region(index)
    except RuntimeError as error:
        assert "advance_snapshot" in str(error)
    else:
        raise AssertionError("query accepted a silently changed snapshot")
    index.advance_snapshot(valid_at=NOW, known_at=NOW)
    oversized = index.candidate_query(world_id="one", frame_id="table", map_revision="map-1", minimum=(0, 0, 0), maximum=(100, 100, 100))
    assert oversized.status == "too-large" and oversized.cells_read > index.max_query_cells
