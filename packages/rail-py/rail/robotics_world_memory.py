"""Bounded ROS-convention tabletop world-memory domain extension (#19).

Records remain temporal evidence; this module does not command a robot or
merge identities by similarity. Poses are SI metres in an explicit frame.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from krail.provider.v1 import ResourceRef
from rail.extension_registry import ExtensionDescriptor, describe_extension, describe_operator
from rail.temporal_records import TemporalRecord, create_temporal_record, query_temporal_records


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pose(Strict):
    frame_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_/.-]{0,127}$")
    metres: tuple[float, float, float]
    quaternion_xyzw: tuple[float, float, float, float]
    observed_at: datetime
    uncertainty_metres: float = Field(ge=0)
    revision: str


class WorldObject(Strict):
    world_id: str
    object_id: str
    class_label: str
    asset_ref: ResourceRef | None = None


class LocationAnswer(Strict):
    status: Literal["observed", "estimated", "unknown", "stale", "ambiguous"]
    pose: Pose | None = None
    evidence: tuple[ResourceRef, ...] = ()


class SceneEpisode(Strict):
    world_id: str
    scene_ref: ResourceRef
    object_refs: tuple[ResourceRef, ...]


class WorldReader(Protocol):
    def authorize(self, ref: ResourceRef) -> None: ...


def robotics_world_extension() -> ExtensionDescriptor:
    schema = "robotics.world-memory.v1"
    operator = describe_operator(operator_id="robotics.world-memory.location", version="1.0.0", input_schema=schema, output_schema=schema)
    return describe_extension(extension_id="robotics.world-memory", version="1.0.0", payload_schemas=(schema,), operators=(operator,))


class TabletopWorldMemory:
    """Small deterministic temporal adapter; worlds never share object IDs."""
    def __init__(self) -> None:
        self._records: list[TemporalRecord] = []

    def record(self, obj: WorldObject, pose: Pose, *, kind: Literal["observation", "estimate"], evidence: tuple[ResourceRef, ...]) -> TemporalRecord:
        if not evidence:
            raise ValueError("world-memory records require exact evidence")
        record = create_temporal_record(
            record_id=f"{obj.world_id}:{obj.object_id}:{kind}:{pose.revision}", entity_id=obj.object_id,
            entity_authority=f"robotics://world/{obj.world_id}", payload_schema="robotics.world-memory",
            payload_schema_version="1.0.0", kind="observation" if kind == "observation" else "estimate",
            authority="robotics://world-memory", writer_family="robotics-world-memory", valid_from=pose.observed_at,
            recorded_at=pose.observed_at, source_refs=evidence, revision=pose.revision,
            payload={"object": obj.model_dump(mode="json"), "pose": pose.model_dump(mode="json"), "state": kind},
        )
        if record not in self._records:
            self._records.append(record)
        return record

    @staticmethod
    def record_ref(record: TemporalRecord) -> ResourceRef:
        return ResourceRef(authority="robotics://world-memory", resource_type="world-record", resource_id=record.record_id, version=record.revision, digest=record.record_digest)

    def recalibrate(self, observation: TemporalRecord, pose: Pose, *, evidence: tuple[ResourceRef, ...]) -> TemporalRecord:
        """Adds an estimate; it never rewrites the sensor observation."""
        obj = WorldObject.model_validate(observation.payload["object"])
        return self.record(obj, pose, kind="estimate", evidence=tuple(dict.fromkeys((*evidence, self.record_ref(observation)))))

    def scene(self, *, world_id: str, scene_id: str, records: tuple[TemporalRecord, ...]) -> SceneEpisode:
        refs = tuple(self.record_ref(record) for record in records)
        digest = "sha256:" + sha256((world_id + ":" + scene_id + ":" + ":".join(ref.digest for ref in refs)).encode()).hexdigest()
        return SceneEpisode(world_id=world_id, scene_ref=ResourceRef(authority="robotics://world-memory", resource_type="scene", resource_id=scene_id, version="1", digest=digest), object_refs=refs)

    def location(self, *, world_id: str, object_id: str, at: datetime, known_at: datetime, estimated: bool, reader: WorldReader) -> LocationAnswer:
        candidates = [r for r in self._records if r.entity_authority == f"robotics://world/{world_id}" and r.entity_id == object_id]
        if not candidates:
            return LocationAnswer(status="unknown")
        current = query_temporal_records(candidates, valid_at=at, known_at=known_at)
        if not current:
            return LocationAnswer(status="unknown")
        record = current[-1]
        state = record.payload["state"]
        if state == "estimate" and not estimated:
            return LocationAnswer(status="unknown")
        pose = Pose.model_validate(record.payload["pose"])
        if estimated and state == "observation" and pose.observed_at < at:
            # Last seen is evidence, not an inferred present location.
            return LocationAnswer(status="unknown")
        try:
            for ref in record.source_refs:
                reader.authorize(ref)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc
        answer = LocationAnswer(status="observed" if state == "observation" else "estimated", pose=pose, evidence=record.source_refs)
        try:
            for ref in answer.evidence:
                reader.authorize(ref)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc
        return answer

    def locate_class(self, *, world_id: str, class_label: str, at: datetime, known_at: datetime, reader: WorldReader) -> LocationAnswer:
        ids = {str(record.payload["object"]["object_id"]) for record in self._records if record.entity_authority == f"robotics://world/{world_id}" and record.payload["object"]["class_label"] == class_label}
        if len(ids) != 1:
            return LocationAnswer(status="ambiguous" if ids else "unknown")
        return self.location(world_id=world_id, object_id=next(iter(ids)), at=at, known_at=known_at, estimated=True, reader=reader)


def tabletop_fixture(at: datetime) -> tuple[TabletopWorldMemory, SceneEpisode]:
    """Two visually similar cups plus a shared immutable scene reference."""
    memory = TabletopWorldMemory()
    evidence = lambda name: ResourceRef(authority="fixture://tabletop", resource_type="camera-observation", resource_id=name, version="1", digest="sha256:" + sha256(name.encode()).hexdigest())
    pose = lambda x, revision: Pose(frame_id="table", metres=(x, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at, uncertainty_metres=0.01, revision=revision)
    left = memory.record(WorldObject(world_id="table-a", object_id="cup-left", class_label="red-cup"), pose(0.1, "1"), kind="observation", evidence=(evidence("left"),))
    right = memory.record(WorldObject(world_id="table-a", object_id="cup-right", class_label="red-cup"), pose(0.3, "1"), kind="observation", evidence=(evidence("right"),))
    return memory, memory.scene(world_id="table-a", scene_id="opening", records=(left, right))


__all__ = ["LocationAnswer", "Pose", "SceneEpisode", "TabletopWorldMemory", "WorldObject", "WorldReader", "robotics_world_extension", "tabletop_fixture"]
