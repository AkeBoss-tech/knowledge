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
from rail.semantic.models import canonical_digest
from rail.semantic.repository import SemanticRow
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


class SceneSnapshot(Strict):
    world_id: str
    session_id: str
    place_id: str
    scene_ref: ResourceRef
    object_refs: tuple[ResourceRef, ...]
    evidence_refs: tuple[ResourceRef, ...]
    valid_at: datetime
    recorded_at: datetime


class SceneEpisode(Strict):
    world_id: str
    session_id: str
    episode_ref: ResourceRef
    scene_refs: tuple[ResourceRef, ...]
    evidence_refs: tuple[ResourceRef, ...]
    valid_from: datetime
    completed_at: datetime | None = None
    recorded_at: datetime


class ObjectHistory(Strict):
    world_id: str
    object_id: str
    records: tuple[TemporalRecord, ...]
    evidence: tuple[ResourceRef, ...]


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
        self._scenes: list[SceneSnapshot] = []
        self._episodes: list[SceneEpisode] = []
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
            self._scenes = [SceneSnapshot.model_validate(row.payload["scene"]) for row in self._projection.store.list(self._tenant_id, self._project_id, kind="robotics_scene_snapshot")]
            self._episodes = [SceneEpisode.model_validate(row.payload["episode"]) for row in self._projection.store.list(self._tenant_id, self._project_id, kind="robotics_episode")]

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

    def _persist_scene(self, scene: SceneSnapshot, reader: WorldReader) -> SceneSnapshot:
        if scene in self._scenes:
            self._authorize_scene(scene, reader)
            return scene
        if self._projection is not None:
            with self._projection.store.transaction():
                # Both checks run while this transaction is open. A live
                # revocation during the final check aborts the insert.
                self._authorize_scene(scene, reader)
                current = self._projection.store.get(self._tenant_id, self._project_id, "robotics_scene_snapshot", scene.scene_ref.digest)
                payload = {"scene": scene.model_dump(mode="json")}
                if current is not None:
                    if current.payload != payload:
                        raise ValueError("conflicting scene snapshot replay")
                else:
                    self._projection.store.put(SemanticRow(tenant_id=self._tenant_id, project_id=self._project_id, record_kind="robotics_scene_snapshot", record_id=scene.scene_ref.digest, revision=1, payload=payload, created_at=scene.recorded_at, updated_at=scene.recorded_at), expected_revision=0)
                self._authorize_scene(scene, reader)
        else:
            self._authorize_scene(scene, reader)
            self._authorize_scene(scene, reader)
        self._scenes.append(scene)
        return scene

    def _persist_episode(self, episode: SceneEpisode, reader: WorldReader) -> SceneEpisode:
        if episode in self._episodes:
            self._authorize_episode(episode, reader)
            return episode
        if self._projection is not None:
            with self._projection.store.transaction():
                self._authorize_episode(episode, reader)
                payload = {"episode": episode.model_dump(mode="json")}
                current = self._projection.store.get(self._tenant_id, self._project_id, "robotics_episode", episode.episode_ref.digest)
                if current is not None:
                    if current.payload != payload:
                        raise ValueError("conflicting episode replay")
                else:
                    self._projection.store.put(SemanticRow(tenant_id=self._tenant_id, project_id=self._project_id, record_kind="robotics_episode", record_id=episode.episode_ref.digest, revision=1, payload=payload, created_at=episode.recorded_at, updated_at=episode.recorded_at), expected_revision=0)
                self._authorize_episode(episode, reader)
        else:
            self._authorize_episode(episode, reader)
            self._authorize_episode(episode, reader)
        self._episodes.append(episode)
        return episode

    def _record_for_ref(self, ref: ResourceRef) -> TemporalRecord | None:
        return next((record for record in self._records if self.record_ref(record).exact_key == ref.exact_key), None)

    def _authorize_scene(self, scene: SceneSnapshot, reader: WorldReader) -> None:
        try:
            reader.authorize(scene.scene_ref)
            for ref in scene.evidence_refs:
                reader.authorize(ref)
            for ref in scene.object_refs:
                record = self._record_for_ref(ref)
                if record is None:
                    raise PermissionError("scene record is unavailable")
                self._authorized_evidence(record, reader)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc

    def _authorize_episode(self, episode: SceneEpisode, reader: WorldReader) -> None:
        try:
            reader.authorize(episode.episode_ref)
            for ref in episode.evidence_refs:
                reader.authorize(ref)
            for scene_ref in episode.scene_refs:
                scene = next((item for item in self._scenes if item.scene_ref.exact_key == scene_ref.exact_key), None)
                if scene is None:
                    raise PermissionError("episode scene is unavailable")
                self._authorize_scene(scene, reader)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc

    def _scene_known_at(self, scene: SceneSnapshot, known_at: datetime) -> bool:
        """A snapshot cannot reveal a raw object record learned after `known_at`."""
        if scene.recorded_at > known_at:
            return False
        for ref in scene.object_refs:
            record = self._record_for_ref(ref)
            if record is None or (record.ingested_at or record.recorded_at) > known_at:
                return False
        return True

    def _scene_visible_at(self, scene: SceneSnapshot, at: datetime, known_at: datetime) -> bool:
        if scene.valid_at > at or not self._scene_known_at(scene, known_at):
            return False
        for ref in scene.object_refs:
            record = self._record_for_ref(ref)
            if record is None or record.valid_from > at or (record.valid_to is not None and at >= record.valid_to):
                return False
        return True

    def _episode_known_at(self, episode: SceneEpisode, known_at: datetime) -> bool:
        if episode.recorded_at > known_at:
            return False
        return all(
            (scene := next((item for item in self._scenes if item.scene_ref.exact_key == scene_ref.exact_key), None)) is not None
            and self._scene_known_at(scene, known_at)
            for scene_ref in episode.scene_refs
        )

    def _episode_visible_at(self, episode: SceneEpisode, at: datetime, known_at: datetime) -> bool:
        if episode.valid_from > at or not self._episode_known_at(episode, known_at):
            return False
        if episode.completed_at is not None:
            return episode.completed_at <= at
        return all(
            (scene := next((item for item in self._scenes if item.scene_ref.exact_key == scene_ref.exact_key), None)) is not None
            and self._scene_visible_at(scene, at, known_at)
            for scene_ref in episode.scene_refs
        )

    def scene(self, *, world_id: str, scene_id: str, records: tuple[TemporalRecord, ...], reader: WorldReader, place_id: str = "table", session_id: str = "default", valid_at: datetime | None = None, recorded_at: datetime | None = None, evidence_refs: tuple[ResourceRef, ...] = ()) -> SceneSnapshot:
        self._require_world(world_id)
        self._require_reader(reader)
        if not records:
            raise ValueError("scene snapshots require at least one exact object record")
        if any(record.entity_authority != f"robotics://world/{world_id}" for record in records):
            raise ValueError("scene records must belong to the exact world")
        refs = tuple(self.record_ref(record) for record in records)
        valid_at = valid_at or max(record.valid_from for record in records)
        if valid_at < max(record.valid_from for record in records):
            raise ValueError("scene valid time cannot precede a referenced object record")
        recorded_at = recorded_at or self._clock()
        scene_evidence = tuple(dict.fromkeys((*evidence_refs, *refs)))
        digest = canonical_digest({
            "schema_version": "krail.robotics-scene-snapshot.v1",
            "world_id": world_id, "session_id": session_id, "place_id": place_id,
            "scene_id": scene_id, "object_refs": [ref.model_dump(mode="json") for ref in refs],
            "evidence_refs": [ref.model_dump(mode="json") for ref in scene_evidence],
            "valid_at": valid_at.isoformat(), "recorded_at": recorded_at.isoformat(),
        })
        scene = SceneSnapshot(world_id=world_id, session_id=session_id, place_id=place_id, scene_ref=ResourceRef(authority="robotics://world-memory", resource_type="scene-snapshot", resource_id=scene_id, version="1", digest=digest), object_refs=refs, evidence_refs=scene_evidence, valid_at=valid_at, recorded_at=recorded_at)
        return self._persist_scene(scene, reader)

    def scene_at(self, *, world_id: str, place_id: str, session_id: str, at: datetime, known_at: datetime, reader: WorldReader) -> SceneSnapshot | None:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        choices = [scene for scene in self._scenes if scene.world_id == world_id and scene.place_id == place_id and scene.session_id == session_id and self._scene_visible_at(scene, at, known_at)]
        if not choices:
            return None
        scene = max(choices, key=lambda item: (item.valid_at, item.recorded_at, item.scene_ref.digest))
        self._authorize_scene(scene, reader)
        self._authorize_scene(scene, reader)
        return scene

    def episode(self, *, world_id: str, session_id: str, episode_id: str, scene_refs: tuple[ResourceRef, ...], reader: WorldReader, valid_from: datetime, completed_at: datetime | None = None, recorded_at: datetime | None = None, evidence_refs: tuple[ResourceRef, ...] = ()) -> SceneEpisode:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        scenes = [next((scene for scene in self._scenes if scene.scene_ref.exact_key == ref.exact_key), None) for ref in scene_refs]
        if not scene_refs or any(scene is None or scene.world_id != world_id or scene.session_id != session_id for scene in scenes):
            raise ValueError("episode scenes must be exact snapshots from one world session")
        complete_scenes = tuple(scene for scene in scenes if scene is not None)
        if completed_at is not None and completed_at < max(scene.valid_at for scene in complete_scenes):
            raise ValueError("completed episode cannot precede a referenced scene")
        recorded_at = recorded_at or self._clock()
        episode_evidence = tuple(dict.fromkeys((*evidence_refs, *scene_refs)))
        digest = canonical_digest({
            "schema_version": "krail.robotics-episode.v1", "world_id": world_id,
            "session_id": session_id, "episode_id": episode_id,
            "scene_refs": [ref.model_dump(mode="json") for ref in scene_refs],
            "evidence_refs": [ref.model_dump(mode="json") for ref in episode_evidence],
            "valid_from": valid_from.isoformat(),
            "completed_at": completed_at.isoformat() if completed_at else None,
            "recorded_at": recorded_at.isoformat(),
        })
        episode = SceneEpisode(world_id=world_id, session_id=session_id, episode_ref=ResourceRef(authority="robotics://world-memory", resource_type="episode", resource_id=episode_id, version="1", digest=digest), scene_refs=scene_refs, evidence_refs=episode_evidence, valid_from=valid_from, completed_at=completed_at, recorded_at=recorded_at)
        return self._persist_episode(episode, reader)

    def episode_at(self, *, world_id: str, session_id: str, at: datetime, known_at: datetime, reader: WorldReader) -> SceneEpisode | None:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        choices = [episode for episode in self._episodes if episode.world_id == world_id and episode.session_id == session_id and self._episode_visible_at(episode, at, known_at)]
        if not choices:
            return None
        episode = max(choices, key=lambda item: (item.valid_from, item.recorded_at, item.episode_ref.digest))
        self._authorize_episode(episode, reader)
        self._authorize_episode(episode, reader)
        return episode

    def object_history(self, *, world_id: str, object_id: str, valid_from: datetime, valid_to: datetime | None, known_at: datetime, reader: WorldReader) -> ObjectHistory:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        records = tuple(sorted((record for record in self._records if record.entity_authority == f"robotics://world/{world_id}" and record.entity_id == object_id and record.valid_from >= valid_from and (valid_to is None or record.valid_from < valid_to) and (record.ingested_at or record.recorded_at) <= known_at), key=lambda item: (item.valid_from, item.recorded_at, item.record_digest)))
        evidence: tuple[ResourceRef, ...] = ()
        for record in records:
            evidence += self._authorized_evidence(record, reader)
        evidence = tuple(dict.fromkeys(evidence))
        for record in records:
            self._authorized_evidence(record, reader)
        return ObjectHistory(world_id=world_id, object_id=object_id, records=records, evidence=evidence)

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


def tabletop_fixture(at: datetime, path: str | None = None, *, tenant_id: str = "local", project_id: str = "robotics") -> tuple[TabletopWorldMemory, SceneSnapshot]:
    """Two visually similar cups plus a shared immutable scene reference."""
    memory = TabletopWorldMemory(path, tenant_id=tenant_id, project_id=project_id, clock=lambda: at)
    evidence = lambda name: ResourceRef(authority="fixture://tabletop", resource_type="camera-observation", resource_id=name, version="1", digest="sha256:" + sha256(name.encode()).hexdigest())
    pose = lambda x, revision: Pose(frame_id="table", metres=(x, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at, uncertainty_metres=0.01, revision=revision, map_revision="table-map-1")
    left = memory.record(WorldObject(world_id="table-a", object_id="cup-left", class_label="red-cup"), pose(0.1, "1"), kind="observation", evidence=(evidence("left"),), recorded_at=at)
    right = memory.record(WorldObject(world_id="table-a", object_id="cup-right", class_label="red-cup"), pose(0.3, "1"), kind="observation", evidence=(evidence("right"),), recorded_at=at)
    return memory, memory.scene(world_id="table-a", scene_id="opening", records=(left, right), reader=_FixtureReader(), place_id="table", session_id="session-1", valid_at=at, recorded_at=at)


def tabletop_episode_fixture(at: datetime, path: str | None = None, *, tenant_id: str = "local", project_id: str = "robotics") -> tuple[TabletopWorldMemory, dict[str, object]]:
    """Opening observation, occlusion/unseen move, estimate expiry, reobservation."""
    memory, opening = tabletop_fixture(at, path, tenant_id=tenant_id, project_id=project_id)
    left = next(record for record in memory._records if record.entity_id == "cup-left")
    obj = WorldObject.model_validate(left.payload["object"])
    evidence = ResourceRef(authority="fixture://tabletop", resource_type="camera-observation", resource_id="left-reobserved", version="2", digest="sha256:" + sha256(b"left-reobserved").hexdigest())
    estimate = memory.record(obj, Pose(frame_id="table", metres=(0.5, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at + timedelta(minutes=1), uncertainty_metres=0.08, revision="estimate-1", map_revision="table-map-1"), kind="estimate", evidence=(memory.record_ref(left),), estimate_expires_at=at + timedelta(minutes=2), recorded_at=at + timedelta(minutes=1))
    right = next(record for record in memory._records if record.entity_id == "cup-right")
    occluded = memory.scene(world_id="table-a", scene_id="occluded", records=(estimate, right), reader=_FixtureReader(), place_id="table", session_id="session-1", valid_at=at + timedelta(minutes=1), recorded_at=at + timedelta(minutes=1))
    reobserved = memory.record(obj, Pose(frame_id="table", metres=(0.45, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at + timedelta(minutes=3), uncertainty_metres=0.01, revision="obs-2", map_revision="table-map-2"), kind="observation", evidence=(evidence,), recorded_at=at + timedelta(minutes=3))
    final = memory.scene(world_id="table-a", scene_id="reobserved", records=(reobserved, right), reader=_FixtureReader(), place_id="table", session_id="session-1", valid_at=at + timedelta(minutes=3), recorded_at=at + timedelta(minutes=3))
    episode = memory.episode(world_id="table-a", session_id="session-1", episode_id="occlusion-and-reobservation", scene_refs=(opening.scene_ref, occluded.scene_ref, final.scene_ref), reader=_FixtureReader(), valid_from=at, completed_at=at + timedelta(minutes=3), recorded_at=at + timedelta(minutes=3))
    return memory, {"left_initial": left, "left_estimate": estimate, "left_reobserved": reobserved, "opening_scene": opening, "occluded_scene": occluded, "final_scene": final, "episode": episode}


__all__ = ["LocationAnswer", "ObjectHistory", "Pose", "SceneEpisode", "SceneSnapshot", "TabletopWorldMemory", "WorldObject", "WorldReader", "register_world_memory_extension", "robotics_world_extension", "tabletop_fixture", "tabletop_episode_fixture"]
