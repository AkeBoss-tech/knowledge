"""Disposable, local spatial candidate projection for robotics world-memory (#20).

This is deliberately *not* an authorized world-memory answer API. Callers
must resolve returned exact records through ``TabletopWorldMemory`` with its
reader and invalidation semantics before disclosing an answer. The projection
contains no asset bytes and is rebuilt solely from immutable TemporalRecords.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import floor, isfinite

from krail.provider.v1 import ResourceRef
from rail.temporal_records import TemporalRecord, query_temporal_records


@dataclass(frozen=True)
class SpatialHit:
    world_id: str
    object_id: str
    record_ref: ResourceRef
    frame_id: str
    map_revision: str
    metres: tuple[float, float, float]


@dataclass(frozen=True)
class ProjectionWork:
    """Measured local work, rather than a latency claim."""

    history_rows_read: int
    index_rows_touched: int


@dataclass(frozen=True)
class SpatialQueryResult:
    """Internal candidate set; it makes no authorization or freshness claim."""

    status: str
    hits: tuple[SpatialHit, ...]
    candidate_rows_read: int
    cells_read: int


def _ref(record: TemporalRecord) -> ResourceRef:
    return ResourceRef(
        authority="robotics://world-memory", resource_type="world-record",
        resource_id=record.record_id, version=record.revision, digest=record.record_digest,
    )


class SpatialCurrentProjection:
    """Rebuildable RAM grid over exact records, with no canonical writes."""

    max_query_cells = 4_096

    def __init__(self, records: tuple[TemporalRecord, ...], *, valid_at: datetime, known_at: datetime, cell_metres: float = 0.1) -> None:
        if not isfinite(cell_metres) or cell_metres <= 0:
            raise ValueError("cell_metres must be finite and positive")
        self.valid_at, self.known_at, self.cell_metres = valid_at, known_at, cell_metres
        self._built_snapshot = (valid_at, known_at)
        self._histories: dict[tuple[str, str], list[TemporalRecord]] = {}
        self._locations: dict[tuple[str, str], tuple[tuple[str, str, str, tuple[int, int, int]], SpatialHit]] = {}
        self._cells: dict[tuple[str, str, str, tuple[int, int, int]], dict[tuple[str, str], SpatialHit]] = {}
        self._frames: dict[tuple[str, str, str], int] = {}
        for record in records:
            if record.payload_schema == "robotics.world-memory":
                self._histories.setdefault((record.entity_authority, record.entity_id), []).append(record)
        self.rebuild()

    def _cell(self, metres: tuple[float, float, float]) -> tuple[int, int, int]:
        return tuple(floor(value / self.cell_metres) for value in metres)  # type: ignore[return-value]

    def rebuild(self) -> ProjectionWork:
        self._locations.clear()
        self._cells.clear()
        self._frames.clear()
        history_rows = index_rows = 0
        for key in self._histories:
            work = self._materialize(key)
            history_rows += work.history_rows_read
            index_rows += work.index_rows_touched
        return ProjectionWork(history_rows, index_rows)

    def advance_snapshot(self, *, valid_at: datetime, known_at: datetime) -> ProjectionWork:
        """Move to a new bitemporal snapshot by rebuilding from exact records."""
        self.valid_at, self.known_at = valid_at, known_at
        self._built_snapshot = (valid_at, known_at)
        return self.rebuild()

    def _require_current_snapshot(self) -> None:
        if (self.valid_at, self.known_at) != self._built_snapshot:
            raise RuntimeError("bitemporal snapshot changed; call advance_snapshot() before querying")

    def _remove(self, key: tuple[str, str]) -> int:
        previous = self._locations.pop(key, None)
        if previous is None:
            return 0
        bucket, _ = previous
        cell = self._cells[bucket]
        cell.pop(key, None)
        if not cell:
            self._cells.pop(bucket, None)
        frame = bucket[:3]
        self._frames[frame] -= 1
        if not self._frames[frame]:
            self._frames.pop(frame, None)
        return 1

    def _materialize(self, key: tuple[str, str]) -> ProjectionWork:
        history = self._histories[key]
        touched = self._remove(key)
        current = query_temporal_records(history, valid_at=self.valid_at, known_at=self.known_at)
        if not current:
            return ProjectionWork(len(history), touched)
        record = current[-1]
        payload, pose = record.payload, record.payload["pose"]
        # Estimate expiry is local record semantics. Observation freshness and
        # invalidation remain with the owning authorized world-memory read.
        if record.freshness != "current" or (payload["state"] == "estimate" and payload.get("estimate_expires_at") and datetime.fromisoformat(str(payload["estimate_expires_at"])) <= self.valid_at):
            return ProjectionWork(len(history), touched)
        world = str(payload["object"]["world_id"])
        hit = SpatialHit(world, key[1], _ref(record), str(pose["frame_id"]), str(pose["map_revision"]), tuple(pose["metres"]))
        bucket = (world, hit.frame_id, hit.map_revision, self._cell(hit.metres))
        self._cells.setdefault(bucket, {})[key] = hit
        self._locations[key] = (bucket, hit)
        frame = bucket[:3]
        self._frames[frame] = self._frames.get(frame, 0) + 1
        return ProjectionWork(len(history), touched + 1)

    def apply(self, record: TemporalRecord) -> ProjectionWork:
        """Update one exact entity entry and report actual local work."""
        if record.payload_schema != "robotics.world-memory":
            return ProjectionWork(0, 0)
        self._require_current_snapshot()
        key = (record.entity_authority, record.entity_id)
        self._histories.setdefault(key, []).append(record)
        return self._materialize(key)

    def current_candidate(self, *, entity_authority: str, object_id: str) -> SpatialQueryResult:
        """Find one materialized object without replaying its history."""
        self._require_current_snapshot()
        located = self._locations.get((entity_authority, object_id))
        if located is None:
            return SpatialQueryResult("abstained", (), 0, 0)
        return SpatialQueryResult("current-candidates", (located[1],), 1, 0)

    def candidate_query(self, *, world_id: str, frame_id: str, map_revision: str, minimum: tuple[float, float, float], maximum: tuple[float, float, float]) -> SpatialQueryResult:
        """Return bounded grid candidates for a region, or abstain on no hit."""
        self._require_current_snapshot()
        if not all(isfinite(value) for value in (*minimum, *maximum)):
            raise ValueError("region coordinates must be finite")
        if any(low > high for low, high in zip(minimum, maximum, strict=True)):
            raise ValueError("region minimum cannot exceed maximum")
        starts = tuple(floor(value / self.cell_metres) for value in minimum)
        ends = tuple(floor(value / self.cell_metres) for value in maximum)
        dimensions = tuple(end - start + 1 for start, end in zip(starts, ends, strict=True))
        cell_count = dimensions[0] * dimensions[1] * dimensions[2]
        if cell_count > self.max_query_cells:
            return SpatialQueryResult("too-large", (), 0, cell_count)
        cells = tuple(
            (x, y, z) for x in range(starts[0], ends[0] + 1)
            for y in range(starts[1], ends[1] + 1) for z in range(starts[2], ends[2] + 1)
        )
        buckets = tuple(self._cells.get((world_id, frame_id, map_revision, cell), {}) for cell in cells)
        candidates = tuple(hit for bucket in buckets for hit in bucket.values())
        hits = tuple(sorted((hit for hit in candidates if all(low <= value <= high for low, value, high in zip(minimum, hit.metres, maximum, strict=True))), key=lambda hit: (hit.world_id, hit.object_id, hit.record_ref.exact_key)))
        frame = (world_id, frame_id, map_revision)
        return SpatialQueryResult("current-candidates" if frame in self._frames else "abstained", hits, len(candidates), len(cells))
