"""Bounded ROS-convention tabletop world-memory domain extension (#19).

Records remain temporal evidence; this module does not command a robot or
merge identities by similarity. Poses are SI metres in an explicit frame.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import isfinite, sqrt
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from krail.provider.v1 import ResourceRef
from rail.authorized_context import HostedRoboticsInvalidationAuthorizer, HostedRoboticsProjectionWriter, HostedRoboticsWorldReader
from rail.extension_registry import DomainExtensionRegistry, ExtensionDescriptor, HANDLER_LINEAGE_REFS, describe_extension, describe_operator
from rail.procedure_projection import ProjectionCheckpoint, ProjectionInvalidationAuthorizer, ProjectionRecomputeRun, ProjectionWriter, TemporalProjectionService
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
    map_revision: str

    @model_validator(mode="after")
    def _pose_is_physical(self):
        if not all(isfinite(value) for value in (*self.metres, *self.quaternion_xyzw, self.uncertainty_metres)):
            raise ValueError("pose values must be finite")
        if abs(sqrt(sum(value * value for value in self.quaternion_xyzw)) - 1.0) > 1e-3:
            raise ValueError("pose quaternion must be normalized")
        return self


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


class _FixtureReader:
    def authorize(self, ref: ResourceRef) -> None:
        return None


class _LocalProjectionWriter:
    """Explicit local-only writer seam until hosted robotics signing exists."""

    def authorize(self, record: TemporalRecord, *, at: datetime) -> None:
        return None


class _LocalProjectionInvalidationAuthorizer:
    """Explicit local-only invalidation seam until hosted signing exists."""

    def authorize_invalidation(self, event_id: str, changed_ref: ResourceRef, event_digest: str, *, at: datetime) -> None:
        return None


def robotics_world_extension() -> ExtensionDescriptor:
    schema = "robotics.world-memory.v1"
    operator = describe_operator(operator_id="robotics.world-memory.location", version="1.0.0", input_schema=schema, output_schema=schema, deterministic=False)
    return describe_extension(extension_id="robotics.world-memory", version="1.0.0", payload_schemas=(schema,), operators=(operator,))


def register_world_memory_extension(registry: DomainExtensionRegistry, memory: "TabletopWorldMemory", reader: WorldReader) -> ExtensionDescriptor:
    descriptor = robotics_world_extension()
    def location(_inputs, config):
        if type(config.get("estimated")) is not bool:
            raise ValueError("estimated must be a boolean")
        answer = memory.location(world_id=str(config["world_id"]), object_id=str(config["object_id"]), at=datetime.fromisoformat(str(config["at"])), known_at=datetime.fromisoformat(str(config["known_at"])), estimated=config["estimated"], reader=reader)
        # Dispatch binds these exact evidence records into InvocationResult.
        # This lookup is intentionally nondeterministic because its canonical
        # snapshot can change between invocations.
        return {**answer.model_dump(mode="json"), "derivation": "canonical-store lookup", HANDLER_LINEAGE_REFS: answer.evidence}
    registry.register(descriptor, {"robotics.world-memory.location": location})
    return descriptor


class TabletopWorldMemory:
    """Small deterministic temporal adapter; worlds never share object IDs."""
    def __init__(self, path: str | None = None, *, tenant_id: str = "local", project_id: str = "robotics", clock: Callable[[], datetime] | None = None, projection_id: str = "robotics-world-memory", projection_writer: ProjectionWriter | None = None, projection_invalidation_authorizer: ProjectionInvalidationAuthorizer | None = None, hosted_world_id: str | None = None, hosted_reader: WorldReader | None = None) -> None:
        self._records: list[TemporalRecord] = []
        self._tenant_id, self._project_id = tenant_id, project_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._projection_id = projection_id
        self._projection = TemporalProjectionService(path, tenant_id=tenant_id, project_id=project_id, clock=self._clock) if path is not None else None
        self._hosted_world_id = hosted_world_id
        self._hosted_reader = hosted_reader
        if hosted_world_id is not None:
            if path is None or projection_writer is None or projection_invalidation_authorizer is None or hosted_reader is None:
                raise ValueError("hosted world-memory requires canonical storage and signed reader, writer, and invalidation adapters")
            if not isinstance(hosted_reader, HostedRoboticsWorldReader) or not isinstance(projection_writer, HostedRoboticsProjectionWriter) or not isinstance(projection_invalidation_authorizer, HostedRoboticsInvalidationAuthorizer):
                raise ValueError("hosted world-memory requires supported signed robotics adapters")
            if any((adapter.world_id, adapter.tenant_id, adapter.project_id) != (hosted_world_id, tenant_id, project_id) for adapter in (hosted_reader, projection_writer, projection_invalidation_authorizer)):
                raise ValueError("hosted world-memory adapters must bind the same exact world")
        self._projection_writer = projection_writer or _LocalProjectionWriter()
        self._projection_invalidation_authorizer = projection_invalidation_authorizer or _LocalProjectionInvalidationAuthorizer()
        self._migrate_legacy_records()
        self._refresh()
        self._repair_projection_aliases()

    def _require_world(self, world_id: str) -> None:
        if self._hosted_world_id is not None and world_id != self._hosted_world_id:
            raise PermissionError("world-memory access denied")

    def _require_reader(self, reader: WorldReader) -> None:
        if self._hosted_reader is not None and reader is not self._hosted_reader:
            raise PermissionError("world-memory access denied")

    def _migrate_legacy_records(self) -> None:
        """Move pre-projection rows into canonical temporal history exactly once."""
        if self._projection is None:
            return
        for row in self._projection.store.list(self._tenant_id, self._project_id, kind="robotics_world_record"):
            record = TemporalRecord.model_validate(row.payload["record"])
            if record.payload_schema != "robotics.world-memory":
                raise ValueError("legacy world-memory row has an unexpected payload schema")
            if self._hosted_world_id is not None and record.entity_authority != f"robotics://world/{self._hosted_world_id}":
                continue
            self._projection.ingest(
                record,
                at=record.ingested_at or record.recorded_at,
                writer=self._projection_writer,
            )

    @staticmethod
    def _verified_world_parent(ref: ResourceRef, records: tuple[TemporalRecord, ...] | list[TemporalRecord]) -> TemporalRecord | None:
        if ref.authority != "robotics://world-memory" or ref.resource_type != "world-record":
            return None
        return next((item for item in records if (
            item.payload_schema == "robotics.world-memory"
            and item.payload_schema_version == "1.0.0"
            and item.writer_family == "robotics-world-memory"
            and item.entity_authority.startswith("robotics://world/")
            and item.record_digest == ref.digest
            and item.record_id == ref.resource_id
            and item.revision == ref.version
        )), None)

    def _repair_projection_aliases(self) -> None:
        if self._projection is None:
            return
        for record in self._records:
            if self._hosted_world_id is not None and record.entity_authority != f"robotics://world/{self._hosted_world_id}":
                continue
            self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)

    def _refresh(self) -> None:
        if self._projection is not None:
            self._records = [record for record in self._projection._records() if record.payload_schema == "robotics.world-memory"]

    def record(self, obj: WorldObject, pose: Pose, *, kind: Literal["observation", "estimate"], evidence: tuple[ResourceRef, ...], estimate_expires_at: datetime | None = None, recorded_at: datetime | None = None) -> TemporalRecord:
        self._require_world(obj.world_id)
        if not evidence:
            raise ValueError("world-memory records require exact evidence")
        if kind == "estimate" and estimate_expires_at is None:
            raise ValueError("world-memory estimates require an expiry")
        if kind == "observation" and estimate_expires_at is not None:
            raise ValueError("observations cannot carry estimate expiry")
        if estimate_expires_at is not None and estimate_expires_at <= pose.observed_at:
            raise ValueError("estimate expiry must follow observation time")
        # Known time belongs to the trusted ingestion boundary, never to an
        # untrusted physical-observation timestamp embedded in the payload.
        recorded_at = recorded_at or self._clock()
        record = create_temporal_record(
            record_id=f"{obj.world_id}:{obj.object_id}:{kind}:{pose.revision}", entity_id=obj.object_id,
            entity_authority=f"robotics://world/{obj.world_id}", payload_schema="robotics.world-memory",
            payload_schema_version="1.0.0", kind="observation" if kind == "observation" else "estimate",
            authority="robotics://world-memory", writer_family="robotics-world-memory", valid_from=pose.observed_at,
            recorded_at=recorded_at,
            source_refs=tuple(dict.fromkeys((*evidence, self.map_revision_ref(obj.world_id, pose.map_revision))) if kind == "estimate" else evidence),
            revision=pose.revision,
            payload={"object": obj.model_dump(mode="json"), "pose": pose.model_dump(mode="json"), "state": kind, "estimate_expires_at": estimate_expires_at.isoformat() if estimate_expires_at else None},
        )
        if record not in self._records:
            if self._projection is not None:
                self._projection.ingest(record, at=recorded_at, writer=self._projection_writer)
            self._records.append(record)
        return record

    @staticmethod
    def record_ref(record: TemporalRecord) -> ResourceRef:
        return ResourceRef(authority="robotics://world-memory", resource_type="world-record", resource_id=record.record_id, version=record.revision, digest=record.record_digest)

    @staticmethod
    def map_revision_ref(world_id: str, map_revision: str) -> ResourceRef:
        return ResourceRef(authority=f"robotics://world/{world_id}", resource_type="map-revision", resource_id=map_revision, version="1", digest="sha256:" + sha256(f"{world_id}:{map_revision}".encode()).hexdigest())

    def rebuild_projection(self, *, valid_at: datetime, known_at: datetime, at: datetime) -> ProjectionCheckpoint:
        if self._projection is None:
            raise RuntimeError("world-memory projection requires a canonical store path")
        return self._projection.rebuild(projection_id=self._projection_id, valid_at=valid_at, known_at=known_at, at=at)

    def recompute_projection(self, *, valid_at: datetime, known_at: datetime, at: datetime) -> ProjectionRecomputeRun:
        if self._projection is None:
            raise RuntimeError("world-memory projection requires a canonical store path")
        return self._projection.recompute(projection_id=self._projection_id, valid_at=valid_at, known_at=known_at, at=at)

    def invalidate_map_revision(self, changed_ref: ResourceRef, *, reason: str, at: datetime, event_id: str | None = None, effective_at: datetime | None = None, recorded_at: datetime | None = None, world_id: str | None = None) -> tuple[ResourceRef, ...]:
        if self._projection is None:
            raise RuntimeError("world-memory projection requires a canonical store path")
        if self._hosted_world_id is not None:
            if world_id is None:
                raise PermissionError("world-memory access denied")
            self._require_world(world_id)
        effective_at = effective_at or at
        recorded_at = recorded_at or at
        event_id = event_id or "robotics-invalidation:" + sha256(
            ("|".join((*changed_ref.exact_key, reason, effective_at.isoformat(), recorded_at.isoformat()))).encode()
        ).hexdigest()
        affected = self._projection.affected_region(changed_ref)
        self._projection.tombstone(
            changed_ref, event_id=event_id, reason=reason,
            effective_at=effective_at, recorded_at=recorded_at,
            authorizer=self._projection_invalidation_authorizer,
        )
        return affected

    def _authorized_evidence(self, record: TemporalRecord, reader: WorldReader) -> tuple[ResourceRef, ...]:
        refs = (*record.source_refs, self.record_ref(record))
        if self._projection is not None:
            refs += (self._projection.record_ref(record),)
            for source in record.source_refs + record.provenance_refs:
                parent = self._verified_world_parent(source, self._records)
                if parent is not None:
                    refs += (self._projection.record_ref(parent),)
        refs = tuple(dict.fromkeys(refs))
        try:
            for ref in refs:
                reader.authorize(ref)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc
        return refs

    def recalibrate(self, observation: TemporalRecord, pose: Pose, *, evidence: tuple[ResourceRef, ...], estimate_expires_at: datetime) -> TemporalRecord:
        """Adds an estimate; it never rewrites the sensor observation."""
        obj = WorldObject.model_validate(observation.payload["object"])
        # Projection edges are keyed by its canonical temporal-record ref, not
        # the public world-record handle. Existing historical rows retain
        # their immutable bytes; new recalibrations bridge to the exact
        # canonical parent so source invalidation reaches them transitively.
        parent_ref = self._projection.record_ref(observation) if self._projection is not None else self.record_ref(observation)
        return self.record(obj, pose, kind="estimate", evidence=tuple(dict.fromkeys((*evidence, parent_ref))), estimate_expires_at=estimate_expires_at)

    def _projection_marks_estimate_stale(self, record: TemporalRecord, *, valid_at: datetime, known_at: datetime) -> bool:
        if self._projection is None:
            return False
        dependency_keys = {ref.exact_key for ref in record.source_refs + record.provenance_refs}
        active_refs = self._projection.active_invalidation_refs(valid_at=valid_at, known_at=known_at)
        if dependency_keys & {ref.exact_key for ref in active_refs}:
            return True
        output_ref = self._projection.record_ref(record)
        if any(output_ref in self._projection.affected_region(ref) for ref in active_refs):
            return True
        for state in self._projection.current_state(self._projection_id):
            if (state.entity_authority, state.entity_id) != (record.entity_authority, record.entity_id):
                continue
            if state.valid_at > valid_at or state.known_at > known_at or record.record_digest not in state.record_digests:
                continue
            if any(item.input_ref.exact_key in dependency_keys and item.status in {"dirty", "stale", "missing"} for item in state.dependency_states):
                return True
        return False

    def scene(self, *, world_id: str, scene_id: str, records: tuple[TemporalRecord, ...], reader: WorldReader) -> SceneEpisode:
        self._require_world(world_id)
        self._require_reader(reader)
        if any(record.entity_authority != f"robotics://world/{world_id}" for record in records):
            raise ValueError("scene records must belong to the exact world")
        for record in records:
            self._authorized_evidence(record, reader)
        refs = tuple(self.record_ref(record) for record in records)
        digest = "sha256:" + sha256((world_id + ":" + scene_id + ":" + ":".join(ref.digest for ref in refs)).encode()).hexdigest()
        for record in records:
            self._authorized_evidence(record, reader)
        return SceneEpisode(world_id=world_id, scene_ref=ResourceRef(authority="robotics://world-memory", resource_type="scene", resource_id=scene_id, version="1", digest=digest), object_refs=refs)

    def location(self, *, world_id: str, object_id: str, at: datetime, known_at: datetime, estimated: bool, reader: WorldReader) -> LocationAnswer:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        candidates = [r for r in self._records if r.entity_authority == f"robotics://world/{world_id}" and r.entity_id == object_id and (estimated or r.kind == "observation")]
        if not candidates:
            return LocationAnswer(status="unknown")
        current = query_temporal_records(candidates, valid_at=at, known_at=known_at)
        if not current:
            return LocationAnswer(status="unknown")
        record = current[-1]
        state = record.payload["state"]
        evidence = self._authorized_evidence(record, reader)
        if state == "estimate" and not estimated:
            self._authorized_evidence(record, reader)
            return LocationAnswer(status="unknown", evidence=evidence)
        pose = Pose.model_validate(record.payload["pose"])
        expires = record.payload.get("estimate_expires_at")
        if state == "estimate" and expires is not None and datetime.fromisoformat(expires) <= at:
            self._authorized_evidence(record, reader)
            return LocationAnswer(status="stale", evidence=evidence)
        if state == "estimate" and self._projection_marks_estimate_stale(record, valid_at=at, known_at=known_at):
            self._authorized_evidence(record, reader)
            return LocationAnswer(status="stale", evidence=evidence)
        if estimated and state == "observation" and pose.observed_at < at:
            # Last seen is evidence, not an inferred present location.
            self._authorized_evidence(record, reader)
            return LocationAnswer(status="unknown", evidence=evidence)
        self._authorized_evidence(record, reader)
        return LocationAnswer(status="observed" if state == "observation" else "estimated", pose=pose, evidence=evidence)

    def locate_class(self, *, world_id: str, class_label: str, at: datetime, known_at: datetime, reader: WorldReader) -> LocationAnswer:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        ids = set()
        visible: list[TemporalRecord] = []
        candidates: dict[str, list[TemporalRecord]] = {}
        for record in self._records:
            if record.entity_authority == f"robotics://world/{world_id}" and record.payload["object"]["class_label"] == class_label:
                candidates.setdefault(record.entity_id, []).append(record)
        for object_id, history in candidates.items():
            current = query_temporal_records(history, valid_at=at, known_at=known_at)
            if not current:
                continue
            record = current[-1]
            try:
                self._authorized_evidence(record, reader)
            except PermissionError:
                continue
            ids.add(object_id)
            visible.append(record)
        if len(ids) != 1:
            for record in visible:
                self._authorized_evidence(record, reader)
            return LocationAnswer(status="ambiguous" if ids else "unknown")
        return self.location(world_id=world_id, object_id=next(iter(ids)), at=at, known_at=known_at, estimated=True, reader=reader)


def tabletop_fixture(at: datetime) -> tuple[TabletopWorldMemory, SceneEpisode]:
    """Two visually similar cups plus a shared immutable scene reference."""
    memory = TabletopWorldMemory()
    evidence = lambda name: ResourceRef(authority="fixture://tabletop", resource_type="camera-observation", resource_id=name, version="1", digest="sha256:" + sha256(name.encode()).hexdigest())
    pose = lambda x, revision: Pose(frame_id="table", metres=(x, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at, uncertainty_metres=0.01, revision=revision, map_revision="table-map-1")
    left = memory.record(WorldObject(world_id="table-a", object_id="cup-left", class_label="red-cup"), pose(0.1, "1"), kind="observation", evidence=(evidence("left"),), recorded_at=at)
    right = memory.record(WorldObject(world_id="table-a", object_id="cup-right", class_label="red-cup"), pose(0.3, "1"), kind="observation", evidence=(evidence("right"),), recorded_at=at)
    return memory, memory.scene(world_id="table-a", scene_id="opening", records=(left, right), reader=_FixtureReader())


def tabletop_episode_fixture(at: datetime) -> tuple[TabletopWorldMemory, dict[str, TemporalRecord]]:
    """Opening observation, occlusion/unseen move, estimate expiry, reobservation."""
    memory, _scene = tabletop_fixture(at)
    left = next(record for record in memory._records if record.entity_id == "cup-left")
    obj = WorldObject.model_validate(left.payload["object"])
    evidence = ResourceRef(authority="fixture://tabletop", resource_type="camera-observation", resource_id="left-reobserved", version="2", digest="sha256:" + sha256(b"left-reobserved").hexdigest())
    estimate = memory.record(obj, Pose(frame_id="table", metres=(0.5, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at + timedelta(minutes=1), uncertainty_metres=0.08, revision="estimate-1", map_revision="table-map-1"), kind="estimate", evidence=(memory.record_ref(left),), estimate_expires_at=at + timedelta(minutes=2), recorded_at=at + timedelta(minutes=1))
    reobserved = memory.record(obj, Pose(frame_id="table", metres=(0.45, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at + timedelta(minutes=3), uncertainty_metres=0.01, revision="obs-2", map_revision="table-map-2"), kind="observation", evidence=(evidence,), recorded_at=at + timedelta(minutes=3))
    return memory, {"left_initial": left, "left_estimate": estimate, "left_reobserved": reobserved}


__all__ = ["LocationAnswer", "Pose", "SceneEpisode", "TabletopWorldMemory", "WorldObject", "WorldReader", "register_world_memory_extension", "robotics_world_extension", "tabletop_fixture", "tabletop_episode_fixture"]
