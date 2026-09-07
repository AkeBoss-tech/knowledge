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
from rail.spatial_current_projection import ProjectionWork, SpatialCurrentProjection


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


class AppearanceObservation(Strict):
    """Immutable, asset-referenced visual evidence for one exact object revision."""

    world_id: str
    object_id: str
    appearance_ref: ResourceRef
    source_observation_ref: ResourceRef
    asset_ref: ResourceRef
    crop_ref: ResourceRef | None = None
    mask_ref: ResourceRef | None = None
    viewpoint: str
    context: str
    descriptor_model: str
    descriptor_version: str
    quality: float = Field(ge=0, le=1)
    occluded: bool
    valid_at: datetime
    recorded_at: datetime


class IdentityCandidate(Strict):
    """A tentative, expiring association; it never merges object identities."""

    world_id: str
    session_id: str
    candidate_ref: ResourceRef
    appearance_ref: ResourceRef
    candidate_object_refs: tuple[ResourceRef, ...] = Field(min_length=2, max_length=16)
    evidence_refs: tuple[ResourceRef, ...]
    association_basis: str
    valid_from: datetime
    expires_at: datetime
    recorded_at: datetime


class PlaceRegion(Strict):
    """Axis-aligned bounded region in one explicit map/frame revision."""

    world_id: str
    session_id: str
    place_id: str
    region_id: str
    region_ref: ResourceRef
    frame_id: str
    map_revision: str
    min_metres: tuple[float, float, float]
    max_metres: tuple[float, float, float]
    evidence_refs: tuple[ResourceRef, ...]
    valid_from: datetime
    recorded_at: datetime

    @model_validator(mode="after")
    def _region_is_physical(self):
        if not all(isfinite(value) for value in (*self.min_metres, *self.max_metres)):
            raise ValueError("region coordinates must be finite")
        if any(low > high for low, high in zip(self.min_metres, self.max_metres, strict=True)):
            raise ValueError("region minimum cannot exceed maximum")
        return self


class SpatialRelation(Strict):
    """One immutable support, containment, or attachment assertion."""

    world_id: str
    session_id: str
    relation_ref: ResourceRef
    relation_type: Literal["support", "containment", "attachment"]
    subject_ref: ResourceRef
    object_ref: ResourceRef
    evidence_refs: tuple[ResourceRef, ...]
    valid_from: datetime
    recorded_at: datetime


class RegionObjectsAnswer(Strict):
    status: Literal["current", "unknown", "stale"]
    object_refs: tuple[ResourceRef, ...] = ()
    evidence: tuple[ResourceRef, ...] = ()

class IdentityResolution(Strict):
    world_id: str
    session_id: str
    resolution_ref: ResourceRef
    candidate_ref: ResourceRef
    resolved_object_ref: ResourceRef
    evidence_refs: tuple[ResourceRef, ...]
    reviewer_id: str
    valid_from: datetime
    recorded_at: datetime

class ActionFreshness(Strict):
    status: Literal["usable", "needs-refresh", "unknown"]
    evidence: tuple[ResourceRef, ...] = ()


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
        self._records_by_ref: dict[tuple[str, str, str, str, str], TemporalRecord] = {}
        self._records_by_entity: dict[tuple[str, str], list[TemporalRecord]] = {}
        self._appearance_records: list[TemporalRecord] = []
        self._identity_candidate_records: list[TemporalRecord] = []
        self._region_records: list[TemporalRecord] = []
        self._relation_records: list[TemporalRecord] = []
        self._resolution_records: list[TemporalRecord] = []
        self._scenes: list[SceneSnapshot] = []
        self._episodes: list[SceneEpisode] = []
        self._spatial_current: SpatialCurrentProjection | None = None
        self.last_spatial_update_work: ProjectionWork | None = None
        self.last_region_read_work: dict[str, int] = {}
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

    def _authorize_publication(self, ref: ResourceRef) -> None:
        """Require the hosted write grant for a direct canonical metadata row."""
        if self._hosted_world_id is None:
            return
        try:
            self._projection_writer.authorize_publication(ref, content_digest=ref.digest, at=self._clock())
        except PermissionError as exc:
            raise PermissionError("world-memory publication denied") from exc

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
        for record in self._appearance_records:
            self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)
            self._projection.register_alias(self.appearance_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)
        for record in self._identity_candidate_records:
            self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)
            self._projection.register_alias(self.identity_candidate_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)
        for record in self._region_records:
            self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)
            self._projection.register_alias(self.region_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)
        for record in self._relation_records:
            self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)
            self._projection.register_alias(self.relation_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)
        for record in self._resolution_records:
            self._projection.register_alias(self.resolution_ref(record), self._projection.record_ref(record), at=record.ingested_at or record.recorded_at)

    def _refresh(self) -> None:
        if self._projection is not None:
            previous = tuple(record.record_digest for record in self._records)
            all_records = self._projection._records()
            self._records = [record for record in all_records if record.payload_schema == "robotics.world-memory"]
            if previous != tuple(record.record_digest for record in self._records):
                # A different process may have changed canonical records. Never
                # serve a RAM projection whose declared inputs no longer match.
                self._spatial_current = None
            self._reindex_world_records()
            self._appearance_records = [record for record in all_records if record.payload_schema == "robotics.appearance-observation"]
            self._identity_candidate_records = [record for record in all_records if record.payload_schema == "robotics.identity-candidate"]
            self._region_records = [record for record in all_records if record.payload_schema == "robotics.place-region"]
            self._relation_records = [record for record in all_records if record.payload_schema == "robotics.spatial-relation"]
            self._resolution_records = [record for record in all_records if record.payload_schema == "robotics.identity-resolution"]
            self._scenes = [SceneSnapshot.model_validate(row.payload["scene"]) for row in self._projection.store.list(self._tenant_id, self._project_id, kind="robotics_scene_snapshot")]
            self._episodes = [SceneEpisode.model_validate(row.payload["episode"]) for row in self._projection.store.list(self._tenant_id, self._project_id, kind="robotics_episode")]

    def _reindex_world_records(self) -> None:
        """Derived exact-ref/entity lookup; rebuilt after every canonical refresh."""
        self._records_by_ref = {self.record_ref(record).exact_key: record for record in self._records}
        grouped: dict[tuple[str, str], list[TemporalRecord]] = {}
        for record in self._records:
            grouped.setdefault((record.entity_authority, record.entity_id), []).append(record)
        self._records_by_entity = grouped

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
                self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=recorded_at)
            self._records.append(record)
            self._records_by_ref[self.record_ref(record).exact_key] = record
            self._records_by_entity.setdefault((record.entity_authority, record.entity_id), []).append(record)
            # A prepared RAM snapshot names canonical records as its declared
            # input.  Keep that exact snapshot complete for same-process
            # publication instead of leaving a newly admitted record invisible
            # until a second explicit rebuild.  ``apply`` still evaluates the
            # fixed valid/known cutoffs, so future/unknown records cannot leak.
            if self._spatial_current is not None:
                self.last_spatial_update_work = self._spatial_current.apply(record)
        return record

    def prepare_spatial_snapshot(self, *, valid_at: datetime, known_at: datetime) -> ProjectionWork:
        """Explicitly build the disposable candidate grid for one time snapshot.

        This is a bounded-scope admission point: it reports any full rebuild
        rather than hiding that cost inside an authorized region read.
        """
        self._refresh()
        self._spatial_current = SpatialCurrentProjection(tuple(self._records), valid_at=valid_at, known_at=known_at)
        return self._spatial_current.last_build_work

    @staticmethod
    def record_ref(record: TemporalRecord) -> ResourceRef:
        return ResourceRef(authority="robotics://world-memory", resource_type="world-record", resource_id=record.record_id, version=record.revision, digest=record.record_digest)

    @staticmethod
    def appearance_ref(record: TemporalRecord) -> ResourceRef:
        if record.payload_schema != "robotics.appearance-observation":
            raise ValueError("appearance reference requires an appearance observation")
        return ResourceRef(authority="robotics://world-memory", resource_type="appearance-observation", resource_id=record.record_id, version=record.revision, digest=record.record_digest)

    @staticmethod
    def identity_candidate_ref(record: TemporalRecord) -> ResourceRef:
        if record.payload_schema != "robotics.identity-candidate":
            raise ValueError("identity candidate reference requires an identity candidate")
        return ResourceRef(authority="robotics://world-memory", resource_type="identity-candidate", resource_id=record.record_id, version=record.revision, digest=record.record_digest)

    @staticmethod
    def region_ref(record: TemporalRecord) -> ResourceRef:
        if record.payload_schema != "robotics.place-region":
            raise ValueError("region reference requires a place region")
        return ResourceRef(authority="robotics://world-memory", resource_type="place-region", resource_id=record.record_id, version=record.revision, digest=record.record_digest)

    @staticmethod
    def relation_ref(record: TemporalRecord) -> ResourceRef:
        if record.payload_schema != "robotics.spatial-relation":
            raise ValueError("relation reference requires a spatial relation")
        return ResourceRef(authority="robotics://world-memory", resource_type="spatial-relation", resource_id=record.record_id, version=record.revision, digest=record.record_digest)

    @staticmethod
    def resolution_ref(record: TemporalRecord) -> ResourceRef:
        if record.payload_schema != "robotics.identity-resolution":
            raise ValueError("resolution reference requires an identity resolution")
        return ResourceRef(authority="robotics://world-memory", resource_type="identity-resolution", resource_id=record.record_id, version=record.revision, digest=record.record_digest)

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

    @staticmethod
    def _record_known_at(record: TemporalRecord, known_at: datetime) -> bool:
        return (record.ingested_at or record.recorded_at) <= known_at

    def _appearance_from_record(self, record: TemporalRecord) -> AppearanceObservation:
        if record.payload_schema != "robotics.appearance-observation":
            raise ValueError("not an appearance observation")
        payload = record.payload
        return AppearanceObservation(
            world_id=str(payload["world_id"]), object_id=str(payload["object_id"]),
            appearance_ref=self.appearance_ref(record),
            source_observation_ref=ResourceRef.model_validate(payload["source_observation_ref"]),
            asset_ref=ResourceRef.model_validate(payload["asset_ref"]),
            crop_ref=ResourceRef.model_validate(payload["crop_ref"]) if payload.get("crop_ref") else None,
            mask_ref=ResourceRef.model_validate(payload["mask_ref"]) if payload.get("mask_ref") else None,
            viewpoint=str(payload["viewpoint"]), context=str(payload["context"]),
            descriptor_model=str(payload["descriptor_model"]), descriptor_version=str(payload["descriptor_version"]),
            quality=float(payload["quality"]), occluded=bool(payload["occluded"]),
            valid_at=record.valid_from, recorded_at=record.recorded_at,
        )

    def _candidate_from_record(self, record: TemporalRecord) -> IdentityCandidate:
        if record.payload_schema != "robotics.identity-candidate":
            raise ValueError("not an identity candidate")
        payload = record.payload
        return IdentityCandidate(
            world_id=str(payload["world_id"]), session_id=str(payload["session_id"]),
            candidate_ref=self.identity_candidate_ref(record),
            appearance_ref=ResourceRef.model_validate(payload["appearance_ref"]),
            candidate_object_refs=tuple(ResourceRef.model_validate(ref) for ref in payload["candidate_object_refs"]),
            evidence_refs=tuple(ResourceRef.model_validate(ref) for ref in payload["evidence_refs"]),
            association_basis=str(payload["association_basis"]), valid_from=record.valid_from,
            expires_at=datetime.fromisoformat(str(payload["expires_at"])), recorded_at=record.recorded_at,
        )

    def _appearance_record_for_ref(self, ref: ResourceRef) -> TemporalRecord | None:
        return next((record for record in self._appearance_records if self.appearance_ref(record).exact_key == ref.exact_key), None)

    def _candidate_record_for_ref(self, ref: ResourceRef) -> TemporalRecord | None:
        return next((record for record in self._identity_candidate_records if self.identity_candidate_ref(record).exact_key == ref.exact_key), None)

    def _appearance_visible_at(self, record: TemporalRecord, at: datetime, known_at: datetime) -> bool:
        appearance = self._appearance_from_record(record)
        source = self._record_for_ref(appearance.source_observation_ref)
        return (
            appearance.valid_at <= at
            and self._record_known_at(record, known_at)
            and source is not None
            and source.valid_from <= at
            and (source.valid_to is None or at < source.valid_to)
            and self._record_known_at(source, known_at)
        )

    def _candidate_visible_at(self, record: TemporalRecord, at: datetime, known_at: datetime) -> bool:
        candidate = self._candidate_from_record(record)
        appearance = self._appearance_record_for_ref(candidate.appearance_ref)
        objects = tuple(self._record_for_ref(ref) for ref in candidate.candidate_object_refs)
        return (
            record.valid_to is not None
            and record.valid_from <= at < record.valid_to
            and self._record_known_at(record, known_at)
            and appearance is not None
            and self._appearance_visible_at(appearance, at, known_at)
            and all(item is not None and item.valid_from <= at and (item.valid_to is None or at < item.valid_to) and self._record_known_at(item, known_at) for item in objects)
        )

    def _authorized_appearance(self, record: TemporalRecord, reader: WorldReader) -> tuple[ResourceRef, ...]:
        appearance = self._appearance_from_record(record)
        source = self._record_for_ref(appearance.source_observation_ref)
        if source is None:
            raise PermissionError("world-memory access denied")
        items = [appearance.appearance_ref, self.record_ref(record), *record.source_refs]
        if self._projection is not None:
            items.append(self._projection.record_ref(record))
        refs = tuple(dict.fromkeys(items))
        try:
            for ref in refs:
                reader.authorize(ref)
            self._authorized_evidence(source, reader)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc
        return tuple(dict.fromkeys((*refs, *self._authorized_evidence(source, reader))))

    def _authorized_identity_candidate(self, record: TemporalRecord, reader: WorldReader) -> tuple[ResourceRef, ...]:
        candidate = self._candidate_from_record(record)
        appearance_record = self._appearance_record_for_ref(candidate.appearance_ref)
        objects = tuple(self._record_for_ref(ref) for ref in candidate.candidate_object_refs)
        if appearance_record is None or any(item is None for item in objects):
            raise PermissionError("world-memory access denied")
        items = [candidate.candidate_ref, self.record_ref(record), *record.source_refs]
        if self._projection is not None:
            items.append(self._projection.record_ref(record))
        refs = tuple(dict.fromkeys(items))
        try:
            for ref in refs:
                reader.authorize(ref)
            appearance_evidence = self._authorized_appearance(appearance_record, reader)
            object_evidence = tuple(ref for item in objects for ref in self._authorized_evidence(item, reader) if item is not None)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc
        return tuple(dict.fromkeys((*refs, *appearance_evidence, *object_evidence)))

    def _authorized_identity_resolution(self, record: TemporalRecord, reader: WorldReader) -> tuple[ResourceRef, ...]:
        """Authorize a resolution and every canonical dependency it discloses.

        A reviewed selection is not independently readable merely because its
        row is granted: it exposes its candidate, visual observation, raw
        object observations, and review evidence.  Reconstruct those inputs
        from canonical records instead of trusting a caller-supplied model.
        """

        payload = record.payload
        candidate_ref = ResourceRef.model_validate(payload["candidate_ref"])
        resolved_object_ref = ResourceRef.model_validate(payload["resolved_object_ref"])
        candidate_record = self._candidate_record_for_ref(candidate_ref)
        target = self._record_for_ref(resolved_object_ref)
        if (
            candidate_record is None
            or target is None
            or candidate_record.entity_authority != record.entity_authority
            or target.entity_authority != record.entity_authority
            or resolved_object_ref not in self._candidate_from_record(candidate_record).candidate_object_refs
        ):
            raise PermissionError("world-memory access denied")
        refs = [self.resolution_ref(record), self.record_ref(record), *record.source_refs]
        if self._projection is not None:
            refs.append(self._projection.record_ref(record))
        refs = tuple(dict.fromkeys(refs))
        try:
            for ref in refs:
                reader.authorize(ref)
            candidate_evidence = self._authorized_identity_candidate(candidate_record, reader)
            object_evidence = self._authorized_evidence(target, reader)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc
        return tuple(dict.fromkeys((*refs, *candidate_evidence, *object_evidence)))

    def _projection_marks_record_stale(self, record: TemporalRecord, *, valid_at: datetime, known_at: datetime) -> bool:
        if self._projection is None:
            return False
        active_refs = self._projection.active_invalidation_refs(valid_at=valid_at, known_at=known_at)
        active_keys = {ref.exact_key for ref in active_refs}
        if any(ref.exact_key in active_keys for ref in record.source_refs):
            return True
        canonical = self._projection.record_ref(record)
        return any(canonical in self._projection.affected_region(ref) for ref in active_refs)

    def record_appearance(
        self, obj: WorldObject, source_observation: TemporalRecord, *, asset_ref: ResourceRef,
        crop_ref: ResourceRef | None = None, mask_ref: ResourceRef | None = None,
        viewpoint: str, context: str, descriptor_model: str, descriptor_version: str,
        quality: float, occluded: bool, revision: str, valid_at: datetime | None = None,
        recorded_at: datetime | None = None,
    ) -> AppearanceObservation:
        """Persist asset metadata; bytes remain at their referenced authority."""

        self._require_world(obj.world_id)
        if source_observation not in self._records or source_observation.entity_authority != f"robotics://world/{obj.world_id}" or source_observation.entity_id != obj.object_id or source_observation.kind != "observation":
            raise ValueError("appearance requires an exact observation for the same world object")
        if not all(value.strip() for value in (viewpoint, context, descriptor_model, descriptor_version, revision)):
            raise ValueError("appearance metadata must be non-empty")
        valid_at = valid_at or source_observation.valid_from
        if valid_at < source_observation.valid_from:
            raise ValueError("appearance cannot predate its source observation")
        recorded_at = recorded_at or self._clock()
        if recorded_at < (source_observation.ingested_at or source_observation.recorded_at):
            raise ValueError("appearance cannot be recorded before its source observation is known")
        if not isfinite(quality) or not 0 <= quality <= 1:
            raise ValueError("appearance quality must be finite and between zero and one")
        source_ref = self.record_ref(source_observation)
        refs = tuple(dict.fromkeys((source_ref, asset_ref, *(item for item in (crop_ref, mask_ref) if item is not None))))
        record = create_temporal_record(
            record_id=f"{obj.world_id}:{obj.object_id}:appearance:{revision}", entity_id=f"{obj.object_id}:appearance:{revision}",
            entity_authority=f"robotics://world/{obj.world_id}", payload_schema="robotics.appearance-observation",
            payload_schema_version="1.0.0", kind="observation", authority="robotics://world-memory",
            writer_family="robotics-world-memory", valid_from=valid_at, recorded_at=recorded_at,
            source_refs=refs, revision=revision,
            payload={
                "world_id": obj.world_id, "object_id": obj.object_id,
                "source_observation_ref": source_ref.model_dump(mode="json"),
                "asset_ref": asset_ref.model_dump(mode="json"),
                "crop_ref": crop_ref.model_dump(mode="json") if crop_ref else None,
                "mask_ref": mask_ref.model_dump(mode="json") if mask_ref else None,
                "viewpoint": viewpoint, "context": context, "descriptor_model": descriptor_model,
                "descriptor_version": descriptor_version, "quality": quality, "occluded": occluded,
            },
        )
        if record not in self._appearance_records:
            if self._projection is not None:
                self._projection.ingest(record, at=recorded_at, writer=self._projection_writer)
                self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=recorded_at)
                self._projection.register_alias(self.appearance_ref(record), self._projection.record_ref(record), at=recorded_at)
            self._appearance_records.append(record)
        return self._appearance_from_record(record)

    def appearance_gallery(self, *, world_id: str, object_id: str, at: datetime, known_at: datetime, reader: WorldReader, limit: int = 8) -> tuple[AppearanceObservation, ...]:
        self._require_world(world_id)
        self._require_reader(reader)
        if not 1 <= limit <= 8:
            raise ValueError("appearance gallery limit must be between 1 and 8")
        self._refresh()
        records = [record for record in self._appearance_records if self._appearance_from_record(record).world_id == world_id and self._appearance_from_record(record).object_id == object_id and self._appearance_visible_at(record, at, known_at)]
        records.sort(key=lambda record: (-self._appearance_from_record(record).quality, self._appearance_from_record(record).occluded, -record.valid_from.timestamp(), -record.recorded_at.timestamp(), record.record_digest))
        selected = tuple(records[:limit])
        for record in selected:
            self._authorized_appearance(record, reader)
        for record in selected:
            self._authorized_appearance(record, reader)
        return tuple(self._appearance_from_record(record) for record in selected)

    def propose_identity_candidates(
        self, appearance: AppearanceObservation, *, session_id: str, candidate_id: str,
        candidate_object_refs: tuple[ResourceRef, ...], evidence_refs: tuple[ResourceRef, ...],
        association_basis: str, expires_at: datetime, recorded_at: datetime | None = None,
    ) -> IdentityCandidate:
        self._require_world(appearance.world_id)
        self._refresh()
        appearance_record = self._appearance_record_for_ref(appearance.appearance_ref)
        candidate_records = tuple(self._record_for_ref(ref) for ref in candidate_object_refs)
        if appearance_record is None or len(candidate_object_refs) < 2 or len({ref.exact_key for ref in candidate_object_refs}) != len(candidate_object_refs) or any(record is None or record.entity_authority != f"robotics://world/{appearance.world_id}" for record in candidate_records):
            raise ValueError("identity candidates require two or more exact records from one world")
        if not all(value.strip() for value in (session_id, candidate_id, association_basis)):
            raise ValueError("identity candidate metadata must be non-empty")
        if expires_at <= appearance.valid_at:
            raise ValueError("identity candidate expiry must follow appearance time")
        recorded_at = recorded_at or self._clock()
        if recorded_at < max((appearance_record.ingested_at or appearance_record.recorded_at), *(record.ingested_at or record.recorded_at for record in candidate_records if record is not None)):
            raise ValueError("identity candidate cannot be recorded before its inputs are known")
        if any(record is not None and record.valid_from > appearance.valid_at for record in candidate_records):
            raise ValueError("identity candidate cannot reference a future object record")
        lineage = tuple(dict.fromkeys((appearance.appearance_ref, *candidate_object_refs, *evidence_refs)))
        record = create_temporal_record(
            record_id=f"{appearance.world_id}:{appearance.object_id}:identity-candidate:{candidate_id}",
            entity_id=f"{appearance.object_id}:identity-candidate:{candidate_id}", entity_authority=f"robotics://world/{appearance.world_id}",
            payload_schema="robotics.identity-candidate", payload_schema_version="1.0.0", kind="hypothesis",
            authority="robotics://world-memory", writer_family="robotics-world-memory",
            valid_from=appearance.valid_at, valid_to=expires_at, recorded_at=recorded_at,
            source_refs=lineage, revision=candidate_id,
            payload={
                "world_id": appearance.world_id, "session_id": session_id,
                "appearance_ref": appearance.appearance_ref.model_dump(mode="json"),
                "candidate_object_refs": [ref.model_dump(mode="json") for ref in candidate_object_refs],
                "evidence_refs": [ref.model_dump(mode="json") for ref in evidence_refs],
                "association_basis": association_basis, "expires_at": expires_at.isoformat(),
            },
        )
        if record not in self._identity_candidate_records:
            if self._projection is not None:
                self._projection.ingest(record, at=recorded_at, writer=self._projection_writer)
                self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=recorded_at)
                self._projection.register_alias(self.identity_candidate_ref(record), self._projection.record_ref(record), at=recorded_at)
            self._identity_candidate_records.append(record)
        return self._candidate_from_record(record)

    def identity_candidates(self, *, world_id: str, appearance_ref: ResourceRef, at: datetime, known_at: datetime, reader: WorldReader) -> tuple[IdentityCandidate, ...]:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        records = [record for record in self._identity_candidate_records if self._candidate_from_record(record).world_id == world_id and self._candidate_from_record(record).appearance_ref.exact_key == appearance_ref.exact_key and self._candidate_visible_at(record, at, known_at)]
        records.sort(key=lambda record: (record.valid_from, record.recorded_at, record.record_digest))
        for record in records:
            self._authorized_identity_candidate(record, reader)
        for record in records:
            self._authorized_identity_candidate(record, reader)
        return tuple(self._candidate_from_record(record) for record in records)

    def resolve_identity(self, candidate: IdentityCandidate, *, resolved_object_ref: ResourceRef, reviewer_id: str, evidence_refs: tuple[ResourceRef, ...], valid_from: datetime, recorded_at: datetime | None = None) -> IdentityResolution:
        """Record an explicit reviewed choice; it never changes object identity.

        ``reviewer_id`` is asserted review metadata.  The signed projection
        writer is the authenticated actor; callers must not treat this label
        as a substitute for authenticated reviewer identity.
        """
        self._require_world(candidate.world_id); self._refresh()
        candidate_record = self._candidate_record_for_ref(candidate.candidate_ref)
        canonical_candidate = self._candidate_from_record(candidate_record) if candidate_record is not None else None
        target = self._record_for_ref(resolved_object_ref)
        if canonical_candidate is None or candidate != canonical_candidate or target is None or resolved_object_ref not in canonical_candidate.candidate_object_refs or target.entity_authority != f"robotics://world/{canonical_candidate.world_id}" or not reviewer_id.strip() or not evidence_refs:
            raise ValueError("identity resolution requires an exact candidate, listed object, reviewer, and evidence")
        if valid_from < candidate.valid_from:
            raise ValueError("identity resolution cannot predate its candidate")
        recorded_at = recorded_at or self._clock()
        if recorded_at < max(candidate_record.ingested_at or candidate_record.recorded_at, target.ingested_at or target.recorded_at):
            raise ValueError("identity resolution cannot be recorded before its inputs are known")
        record = create_temporal_record(
            record_id=f"{canonical_candidate.world_id}:{canonical_candidate.session_id}:identity-resolution:{canonical_candidate.candidate_ref.digest}:{reviewer_id}", entity_id=f"identity-resolution:{canonical_candidate.candidate_ref.digest}", entity_authority=f"robotics://world/{canonical_candidate.world_id}", payload_schema="robotics.identity-resolution", payload_schema_version="1.0.0", kind="approved_state", authority="robotics://world-memory", writer_family="robotics-world-memory", valid_from=valid_from, recorded_at=recorded_at, source_refs=tuple(dict.fromkeys((canonical_candidate.candidate_ref, resolved_object_ref, *evidence_refs))), revision=reviewer_id,
            payload={"world_id": canonical_candidate.world_id, "session_id": canonical_candidate.session_id, "candidate_ref": canonical_candidate.candidate_ref.model_dump(mode="json"), "resolved_object_ref": resolved_object_ref.model_dump(mode="json"), "evidence_refs": [ref.model_dump(mode="json") for ref in evidence_refs], "reviewer_id": reviewer_id},
        )
        if record not in self._resolution_records:
            if self._projection is not None:
                self._projection.ingest(record, at=recorded_at, writer=self._projection_writer)
                self._projection.register_alias(self.resolution_ref(record), self._projection.record_ref(record), at=recorded_at)
            self._resolution_records.append(record)
        return IdentityResolution(world_id=candidate.world_id, session_id=candidate.session_id, resolution_ref=self.resolution_ref(record), candidate_ref=candidate.candidate_ref, resolved_object_ref=resolved_object_ref, evidence_refs=evidence_refs, reviewer_id=reviewer_id, valid_from=valid_from, recorded_at=recorded_at)

    def resolved_identity(self, *, world_id: str, candidate_ref: ResourceRef, at: datetime, known_at: datetime, reader: WorldReader) -> IdentityResolution | None:
        self._require_world(world_id); self._require_reader(reader); self._refresh()
        records = [r for r in self._resolution_records if r.valid_from <= at and self._record_known_at(r, known_at) and r.payload["world_id"] == world_id and ResourceRef.model_validate(r.payload["candidate_ref"]).exact_key == candidate_ref.exact_key]
        if not records: return None
        record = max(records, key=lambda r: (r.valid_from, r.recorded_at, r.record_digest))
        if self._projection_marks_record_stale(record, valid_at=at, known_at=known_at):
            return None
        self._authorized_identity_resolution(record, reader)
        payload=record.payload
        result=IdentityResolution(world_id=str(payload["world_id"]), session_id=str(payload["session_id"]), resolution_ref=self.resolution_ref(record), candidate_ref=ResourceRef.model_validate(payload["candidate_ref"]), resolved_object_ref=ResourceRef.model_validate(payload["resolved_object_ref"]), evidence_refs=tuple(ResourceRef.model_validate(x) for x in payload["evidence_refs"]), reviewer_id=str(payload["reviewer_id"]), valid_from=record.valid_from, recorded_at=record.recorded_at)
        # Repeat the complete authorization chain immediately before return to
        # close a revocation race between materialization and disclosure.
        self._authorized_identity_resolution(record, reader)
        return result

    def action_freshness(self, *, world_id: str, object_id: str, at: datetime, known_at: datetime, reader: WorldReader, required_frame_id: str | None = None, required_map_revision: str | None = None, region_ref: ResourceRef | None = None) -> ActionFreshness:
        region = None
        if region_ref is not None:
            region = self.objects_in_region(world_id=world_id, region_ref=region_ref, at=at, known_at=known_at, reader=reader)
            if region.status == "stale": return ActionFreshness(status="needs-refresh", evidence=region.evidence)
        answer = self.location(world_id=world_id, object_id=object_id, at=at, known_at=known_at, estimated=True, reader=reader)
        if answer.status == "stale": return ActionFreshness(status="needs-refresh", evidence=answer.evidence)
        if answer.status not in {"observed", "estimated"} or answer.pose is None: return ActionFreshness(status="unknown", evidence=answer.evidence)
        if (required_frame_id and answer.pose.frame_id != required_frame_id) or (required_map_revision and answer.pose.map_revision != required_map_revision): return ActionFreshness(status="unknown", evidence=answer.evidence)
        evidence = answer.evidence
        if region_ref is not None:
            assert region is not None
            evidence = tuple(dict.fromkeys((*evidence, *region.evidence)))
            if region.status == "stale": return ActionFreshness(status="needs-refresh", evidence=evidence)
            history = [r for r in self._records if r.entity_id == object_id and r.entity_authority == f"robotics://world/{world_id}"]
            current = query_temporal_records(history, valid_at=at, known_at=known_at)
            if region.status != "current" or not current or self.record_ref(current[-1]) not in region.object_refs: return ActionFreshness(status="unknown", evidence=evidence)
        for ref in evidence: reader.authorize(ref)
        return ActionFreshness(status="usable", evidence=evidence)

    def _region_from_record(self, record: TemporalRecord) -> PlaceRegion:
        if record.payload_schema != "robotics.place-region":
            raise ValueError("not a place region")
        payload = record.payload
        return PlaceRegion(
            world_id=str(payload["world_id"]), session_id=str(payload["session_id"]),
            place_id=str(payload["place_id"]), region_id=str(payload["region_id"]),
            region_ref=self.region_ref(record), frame_id=str(payload["frame_id"]),
            map_revision=str(payload["map_revision"]),
            min_metres=tuple(payload["min_metres"]), max_metres=tuple(payload["max_metres"]),
            evidence_refs=tuple(ResourceRef.model_validate(ref) for ref in payload["evidence_refs"]),
            valid_from=record.valid_from, recorded_at=record.recorded_at,
        )

    def _relation_from_record(self, record: TemporalRecord) -> SpatialRelation:
        if record.payload_schema != "robotics.spatial-relation":
            raise ValueError("not a spatial relation")
        payload = record.payload
        return SpatialRelation(
            world_id=str(payload["world_id"]), session_id=str(payload["session_id"]),
            relation_ref=self.relation_ref(record), relation_type=str(payload["relation_type"]),
            subject_ref=ResourceRef.model_validate(payload["subject_ref"]),
            object_ref=ResourceRef.model_validate(payload["object_ref"]),
            evidence_refs=tuple(ResourceRef.model_validate(ref) for ref in payload["evidence_refs"]),
            valid_from=record.valid_from, recorded_at=record.recorded_at,
        )

    def _region_record_for_ref(self, ref: ResourceRef) -> TemporalRecord | None:
        return next((record for record in self._region_records if self.region_ref(record).exact_key == ref.exact_key), None)

    def _relation_record_for_ref(self, ref: ResourceRef) -> TemporalRecord | None:
        return next((record for record in self._relation_records if self.relation_ref(record).exact_key == ref.exact_key), None)

    def _authorized_region(self, record: TemporalRecord, reader: WorldReader) -> tuple[ResourceRef, ...]:
        region = self._region_from_record(record)
        items = [region.region_ref, self.record_ref(record), *record.source_refs]
        if self._projection is not None:
            items.append(self._projection.record_ref(record))
        refs = tuple(dict.fromkeys(items))
        try:
            for ref in refs:
                reader.authorize(ref)
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc
        return refs

    def _authorized_relation(self, record: TemporalRecord, reader: WorldReader) -> tuple[ResourceRef, ...]:
        relation = self._relation_from_record(record)
        subject = self._record_for_ref(relation.subject_ref)
        target_record = self._record_for_ref(relation.object_ref)
        target_region = self._region_record_for_ref(relation.object_ref)
        if subject is None or (target_record is None and target_region is None):
            raise PermissionError("world-memory access denied")
        items = [relation.relation_ref, self.record_ref(record), *record.source_refs]
        if self._projection is not None:
            items.append(self._projection.record_ref(record))
        refs = tuple(dict.fromkeys(items))
        try:
            for ref in refs:
                reader.authorize(ref)
            subject_evidence = self._authorized_evidence(subject, reader)
            target_evidence = self._authorized_evidence(target_record, reader) if target_record is not None else self._authorized_region(target_region, reader)  # type: ignore[arg-type]
        except PermissionError as exc:
            raise PermissionError("world-memory access denied") from exc
        return tuple(dict.fromkeys((*refs, *subject_evidence, *target_evidence)))

    def record_region(
        self, *, world_id: str, session_id: str, place_id: str, region_id: str,
        frame_id: str, map_revision: str, min_metres: tuple[float, float, float],
        max_metres: tuple[float, float, float], evidence_refs: tuple[ResourceRef, ...],
        revision: str, valid_from: datetime, recorded_at: datetime | None = None,
    ) -> PlaceRegion:
        self._require_world(world_id)
        if not evidence_refs or not all(value.strip() for value in (session_id, place_id, region_id, frame_id, map_revision, revision)):
            raise ValueError("place regions require exact evidence and non-empty metadata")
        recorded_at = recorded_at or self._clock()
        record = create_temporal_record(
            record_id=f"{world_id}:{session_id}:{place_id}:region:{region_id}:{revision}",
            entity_id=f"place:{place_id}:region:{region_id}", entity_authority=f"robotics://world/{world_id}",
            payload_schema="robotics.place-region", payload_schema_version="1.0.0", kind="approved_state",
            authority="robotics://world-memory", writer_family="robotics-world-memory",
            valid_from=valid_from, recorded_at=recorded_at, source_refs=tuple(dict.fromkeys(evidence_refs)), revision=revision,
            payload={
                "world_id": world_id, "session_id": session_id, "place_id": place_id,
                "region_id": region_id, "frame_id": frame_id, "map_revision": map_revision,
                "min_metres": min_metres, "max_metres": max_metres,
                "evidence_refs": [ref.model_dump(mode="json") for ref in evidence_refs],
            },
        )
        region = self._region_from_record(record)
        if record not in self._region_records:
            if self._projection is not None:
                self._projection.ingest(record, at=recorded_at, writer=self._projection_writer)
                self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=recorded_at)
                self._projection.register_alias(self.region_ref(record), self._projection.record_ref(record), at=recorded_at)
            self._region_records.append(record)
        return region

    def record_relation(
        self, *, world_id: str, session_id: str, relation_id: str,
        relation_type: Literal["support", "containment", "attachment"], subject_ref: ResourceRef,
        object_ref: ResourceRef, evidence_refs: tuple[ResourceRef, ...], revision: str,
        valid_from: datetime, recorded_at: datetime | None = None,
    ) -> SpatialRelation:
        self._require_world(world_id)
        self._refresh()
        subject = self._record_for_ref(subject_ref)
        target_record = self._record_for_ref(object_ref)
        target_region = self._region_record_for_ref(object_ref)
        if subject is None or subject.entity_authority != f"robotics://world/{world_id}" or (target_record is None and target_region is None):
            raise ValueError("spatial relation requires exact same-world subject and target")
        if target_record is not None and target_record.entity_authority != f"robotics://world/{world_id}":
            raise ValueError("spatial relation target must belong to the exact world")
        if target_region is not None and self._region_from_record(target_region).world_id != world_id:
            raise ValueError("spatial relation target must belong to the exact world")
        if subject_ref.exact_key == object_ref.exact_key or not evidence_refs or not all(value.strip() for value in (session_id, relation_id, revision)):
            raise ValueError("spatial relation requires distinct exact endpoints, evidence, and metadata")
        target_valid_from = target_record.valid_from if target_record is not None else target_region.valid_from  # type: ignore[union-attr]
        if valid_from < max(subject.valid_from, target_valid_from):
            raise ValueError("spatial relation cannot predate its endpoints")
        recorded_at = recorded_at or self._clock()
        target_known_at = (target_record.ingested_at or target_record.recorded_at) if target_record is not None else (target_region.ingested_at or target_region.recorded_at)  # type: ignore[union-attr]
        if recorded_at < max(subject.ingested_at or subject.recorded_at, target_known_at):
            raise ValueError("spatial relation cannot be recorded before its endpoints are known")
        lineage = tuple(dict.fromkeys((subject_ref, object_ref, *evidence_refs)))
        record = create_temporal_record(
            record_id=f"{world_id}:{session_id}:relation:{relation_id}:{revision}",
            entity_id=f"relation:{relation_id}", entity_authority=f"robotics://world/{world_id}",
            payload_schema="robotics.spatial-relation", payload_schema_version="1.0.0", kind="reported_claim",
            authority="robotics://world-memory", writer_family="robotics-world-memory",
            valid_from=valid_from, recorded_at=recorded_at, source_refs=lineage, revision=revision,
            payload={
                "world_id": world_id, "session_id": session_id, "relation_type": relation_type,
                "subject_ref": subject_ref.model_dump(mode="json"), "object_ref": object_ref.model_dump(mode="json"),
                "evidence_refs": [ref.model_dump(mode="json") for ref in evidence_refs],
            },
        )
        relation = self._relation_from_record(record)
        if record not in self._relation_records:
            if self._projection is not None:
                self._projection.ingest(record, at=recorded_at, writer=self._projection_writer)
                self._projection.register_alias(self.record_ref(record), self._projection.record_ref(record), at=recorded_at)
                self._projection.register_alias(self.relation_ref(record), self._projection.record_ref(record), at=recorded_at)
            self._relation_records.append(record)
        return relation

    def spatial_relations(self, *, world_id: str, session_id: str, at: datetime, known_at: datetime, reader: WorldReader) -> tuple[SpatialRelation, ...]:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        records = [record for record in self._relation_records if self._relation_from_record(record).world_id == world_id and self._relation_from_record(record).session_id == session_id and record.valid_from <= at and self._record_known_at(record, known_at)]
        records.sort(key=lambda record: (record.valid_from, record.recorded_at, record.record_digest))
        for record in records:
            self._authorized_relation(record, reader)
        for record in records:
            self._authorized_relation(record, reader)
        return tuple(self._relation_from_record(record) for record in records)

    def objects_in_region(self, *, world_id: str, region_ref: ResourceRef, at: datetime, known_at: datetime, reader: WorldReader) -> RegionObjectsAnswer:
        self._require_world(world_id)
        self._require_reader(reader)
        self._refresh()
        region_record = self._region_record_for_ref(region_ref)
        if region_record is None or not self._record_known_at(region_record, known_at) or region_record.valid_from > at:
            return RegionObjectsAnswer(status="unknown")
        region = self._region_from_record(region_record)
        if region.world_id != world_id:
            return RegionObjectsAnswer(status="unknown")
        evidence = list(self._authorized_region(region_record, reader))
        active: tuple[ResourceRef, ...] = ()
        if self._projection is not None:
            active = self._projection.active_invalidation_refs(valid_at=at, known_at=known_at)
            canonical = self._projection.record_ref(region_record)
            if any(ref.exact_key in {item.exact_key for item in active} for ref in region_record.source_refs) or any(canonical in self._projection.affected_region(ref) for ref in active):
                return RegionObjectsAnswer(status="stale", evidence=tuple(evidence))
        records_by_object: dict[str, list[TemporalRecord]] = {}
        spatial = self._spatial_current
        if spatial is not None and not active and (spatial.valid_at, spatial.known_at) == (at, known_at) and not spatial.requires_conservative_fallback(world_id=world_id, frame_id=region.frame_id, map_revision=region.map_revision):
            margin = spatial.max_uncertainty_metres
            candidates = spatial.candidate_query(
                world_id=world_id, frame_id=region.frame_id, map_revision=region.map_revision,
                minimum=tuple(value - margin for value in region.min_metres),
                maximum=tuple(value + margin for value in region.max_metres),
            )
            # A broad region is not silently changed into an unbounded scan.
            # The caller must explicitly prepare a suitable projection/region.
            if candidates.status == "too-large":
                return RegionObjectsAnswer(status="unknown", evidence=tuple(evidence))
            for hit in candidates.hits:
                record = self._record_for_ref(hit.record_ref)
                if record is not None:
                    records_by_object.setdefault(record.entity_id, []).append(record)
            self.last_region_read_work = {"canonical_rows_refreshed": len(self._records), "exact_candidate_rows": len(candidates.hits), "history_rows_selected": sum(len(value) for value in records_by_object.values())}
        else:
            for record in self._records:
                if record.entity_authority == f"robotics://world/{world_id}":
                    records_by_object.setdefault(record.entity_id, []).append(record)
            self.last_region_read_work = {"canonical_rows_refreshed": len(self._records), "exact_candidate_rows": 0, "history_rows_selected": sum(len(value) for value in records_by_object.values())}
        inside: list[ResourceRef] = []
        unknown = stale = False
        for history in records_by_object.values():
            current = query_temporal_records(history, valid_at=at, known_at=known_at)
            if not current:
                continue
            record = current[-1]
            try:
                record_evidence = self._authorized_evidence(record, reader)
            except PermissionError:
                continue
            evidence.extend(record_evidence)
            state = record.payload["state"]
            pose = Pose.model_validate(record.payload["pose"])
            expires = record.payload.get("estimate_expires_at")
            if self._projection_marks_record_stale(record, valid_at=at, known_at=known_at):
                stale = True
                continue
            if (state == "estimate" and ((expires is not None and datetime.fromisoformat(expires) <= at) or self._projection_marks_estimate_stale(record, valid_at=at, known_at=known_at))):
                stale = True
                continue
            if state == "observation" and pose.observed_at < at:
                unknown = True
                continue
            if pose.frame_id != region.frame_id or pose.map_revision != region.map_revision:
                unknown = True
                continue
            definitely_outside = any(value + pose.uncertainty_metres < lower or value - pose.uncertainty_metres > upper for value, lower, upper in zip(pose.metres, region.min_metres, region.max_metres, strict=True))
            definitely_inside = all(lower + pose.uncertainty_metres <= value <= upper - pose.uncertainty_metres for value, lower, upper in zip(pose.metres, region.min_metres, region.max_metres, strict=True))
            if definitely_outside:
                continue
            if not definitely_inside:
                unknown = True
                continue
            inside.append(self.record_ref(record))
        for ref in tuple(dict.fromkeys(evidence)):
            reader.authorize(ref)
        if stale:
            return RegionObjectsAnswer(status="stale", evidence=tuple(dict.fromkeys(evidence)))
        if unknown:
            return RegionObjectsAnswer(status="unknown", evidence=tuple(dict.fromkeys(evidence)))
        return RegionObjectsAnswer(status="current", object_refs=tuple(sorted(inside, key=lambda ref: ref.exact_key)), evidence=tuple(dict.fromkeys(evidence)))

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
                self._authorize_publication(scene.scene_ref)
                self._authorize_scene(scene, reader)
                current = self._projection.store.get(self._tenant_id, self._project_id, "robotics_scene_snapshot", scene.scene_ref.digest)
                payload = {"scene": scene.model_dump(mode="json")}
                if current is not None:
                    if current.payload != payload:
                        raise ValueError("conflicting scene snapshot replay")
                else:
                    self._projection.store.put(SemanticRow(tenant_id=self._tenant_id, project_id=self._project_id, record_kind="robotics_scene_snapshot", record_id=scene.scene_ref.digest, revision=1, payload=payload, created_at=scene.recorded_at, updated_at=scene.recorded_at), expected_revision=0)
                self._authorize_publication(scene.scene_ref)
                self._authorize_scene(scene, reader)
        else:
            self._authorize_publication(scene.scene_ref)
            self._authorize_scene(scene, reader)
            self._authorize_publication(scene.scene_ref)
            self._authorize_scene(scene, reader)
        self._scenes.append(scene)
        return scene

    def _persist_episode(self, episode: SceneEpisode, reader: WorldReader) -> SceneEpisode:
        if episode in self._episodes:
            self._authorize_episode(episode, reader)
            return episode
        if self._projection is not None:
            with self._projection.store.transaction():
                self._authorize_publication(episode.episode_ref)
                self._authorize_episode(episode, reader)
                payload = {"episode": episode.model_dump(mode="json")}
                current = self._projection.store.get(self._tenant_id, self._project_id, "robotics_episode", episode.episode_ref.digest)
                if current is not None:
                    if current.payload != payload:
                        raise ValueError("conflicting episode replay")
                else:
                    self._projection.store.put(SemanticRow(tenant_id=self._tenant_id, project_id=self._project_id, record_kind="robotics_episode", record_id=episode.episode_ref.digest, revision=1, payload=payload, created_at=episode.recorded_at, updated_at=episode.recorded_at), expected_revision=0)
                self._authorize_publication(episode.episode_ref)
                self._authorize_episode(episode, reader)
        else:
            self._authorize_publication(episode.episode_ref)
            self._authorize_episode(episode, reader)
            self._authorize_publication(episode.episode_ref)
            self._authorize_episode(episode, reader)
        self._episodes.append(episode)
        return episode

    def _record_for_ref(self, ref: ResourceRef) -> TemporalRecord | None:
        return self._records_by_ref.get(ref.exact_key)

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
        records = tuple(sorted((record for record in self._records_by_entity.get((f"robotics://world/{world_id}", object_id), ()) if record.valid_from >= valid_from and (valid_to is None or record.valid_from < valid_to) and (record.ingested_at or record.recorded_at) <= known_at), key=lambda item: (item.valid_from, item.recorded_at, item.record_digest)))
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
        candidates = [r for r in self._records_by_entity.get((f"robotics://world/{world_id}", object_id), ()) if estimated or r.kind == "observation"]
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
    table_region = memory.record_region(
        world_id="table-a", session_id="session-1", place_id="table", region_id="left-zone",
        frame_id="table", map_revision="table-map-1", min_metres=(0.0, 0.0, -0.1),
        max_metres=(0.2, 0.4, 0.1), evidence_refs=(ResourceRef(authority="fixture://tabletop", resource_type="region-calibration", resource_id="left-zone", version="1", digest="sha256:" + sha256(b"left-zone").hexdigest()),),
        revision="1", valid_from=at, recorded_at=at,
    )
    left_support = memory.record_relation(
        world_id="table-a", session_id="session-1", relation_id="left-supported-by-table",
        relation_type="support", subject_ref=memory.record_ref(left), object_ref=table_region.region_ref,
        evidence_refs=(ResourceRef(authority="fixture://tabletop", resource_type="relation-observation", resource_id="left-support", version="1", digest="sha256:" + sha256(b"left-support").hexdigest()),),
        revision="1", valid_from=at, recorded_at=at,
    )
    initial_appearance = memory.record_appearance(
        WorldObject.model_validate(left.payload["object"]), left,
        asset_ref=ResourceRef(authority="fixture://tabletop", resource_type="image", resource_id="left-opening", version="1", digest="sha256:" + sha256(b"left-opening").hexdigest()),
        crop_ref=ResourceRef(authority="fixture://tabletop", resource_type="crop", resource_id="left-opening-crop", version="1", digest="sha256:" + sha256(b"left-opening-crop").hexdigest()),
        viewpoint="overhead", context="opening tabletop", descriptor_model="fixture-descriptor",
        descriptor_version="1", quality=0.9, occluded=False, revision="opening", recorded_at=at,
    )
    ambiguous_identity = memory.propose_identity_candidates(
        initial_appearance, session_id="session-1", candidate_id="opening-red-cup",
        candidate_object_refs=(memory.record_ref(left), memory.record_ref(next(record for record in memory._records if record.entity_id == "cup-right"))),
        evidence_refs=(ResourceRef(authority="fixture://tabletop", resource_type="association", resource_id="opening-red-cup", version="1", digest="sha256:" + sha256(b"opening-red-cup").hexdigest()),),
        association_basis="same class and bounded appearance descriptors", expires_at=at + timedelta(minutes=2), recorded_at=at,
    )
    obj = WorldObject.model_validate(left.payload["object"])
    evidence = ResourceRef(authority="fixture://tabletop", resource_type="camera-observation", resource_id="left-reobserved", version="2", digest="sha256:" + sha256(b"left-reobserved").hexdigest())
    estimate = memory.record(obj, Pose(frame_id="table", metres=(0.5, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at + timedelta(minutes=1), uncertainty_metres=0.08, revision="estimate-1", map_revision="table-map-1"), kind="estimate", evidence=(memory.record_ref(left),), estimate_expires_at=at + timedelta(minutes=2), recorded_at=at + timedelta(minutes=1))
    right = next(record for record in memory._records if record.entity_id == "cup-right")
    occluded = memory.scene(world_id="table-a", scene_id="occluded", records=(estimate, right), reader=_FixtureReader(), place_id="table", session_id="session-1", valid_at=at + timedelta(minutes=1), recorded_at=at + timedelta(minutes=1))
    reobserved = memory.record(obj, Pose(frame_id="table", metres=(0.45, 0.2, 0.0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at + timedelta(minutes=3), uncertainty_metres=0.01, revision="obs-2", map_revision="table-map-2"), kind="observation", evidence=(evidence,), recorded_at=at + timedelta(minutes=3))
    identity_resolution = memory.resolve_identity(ambiguous_identity, resolved_object_ref=memory.record_ref(left), reviewer_id="fixture-reviewer", evidence_refs=(evidence,), valid_from=at + timedelta(minutes=3), recorded_at=at + timedelta(minutes=3))
    final = memory.scene(world_id="table-a", scene_id="reobserved", records=(reobserved, right), reader=_FixtureReader(), place_id="table", session_id="session-1", valid_at=at + timedelta(minutes=3), recorded_at=at + timedelta(minutes=3))
    episode = memory.episode(world_id="table-a", session_id="session-1", episode_id="occlusion-and-reobservation", scene_refs=(opening.scene_ref, occluded.scene_ref, final.scene_ref), reader=_FixtureReader(), valid_from=at, completed_at=at + timedelta(minutes=3), recorded_at=at + timedelta(minutes=3))
    return memory, {"left_initial": left, "left_estimate": estimate, "left_reobserved": reobserved, "identity_resolution": identity_resolution, "opening_scene": opening, "occluded_scene": occluded, "final_scene": final, "episode": episode, "table_region": table_region, "left_support": left_support, "initial_appearance": initial_appearance, "ambiguous_identity": ambiguous_identity}


__all__ = ["ActionFreshness", "AppearanceObservation", "IdentityCandidate", "IdentityResolution", "LocationAnswer", "ObjectHistory", "PlaceRegion", "Pose", "RegionObjectsAnswer", "SceneEpisode", "SceneSnapshot", "SpatialRelation", "TabletopWorldMemory", "WorldObject", "WorldReader", "register_world_memory_extension", "robotics_world_extension", "tabletop_fixture", "tabletop_episode_fixture"]
