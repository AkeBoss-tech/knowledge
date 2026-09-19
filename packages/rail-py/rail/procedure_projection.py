"""Bounded, rebuildable temporal current-state projection (#18).

The canonical ``temporal_record`` and tombstone rows are immutable history.
Everything else in this module is a disposable index: it can be replayed from
that history and never grants authority to alter it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from krail.provider.v1 import ResourceRef
from rail.semantic.repository import JsonSemanticStore, SemanticRow
from rail.temporal_records import TemporalRecord, query_temporal_records, verify_temporal_record_integrity

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


def _digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


class ProjectionWriter(Protocol):
    def authorize(self, record: TemporalRecord, *, at: datetime) -> None: ...


class ProjectionInvalidationAuthorizer(Protocol):
    """Existing signed ``procedure.invalidate`` action seam for tombstones."""

    def authorize_invalidation(
        self, event_id: str, changed_ref: ResourceRef, event_digest: str, *, at: datetime
    ) -> None: ...


class ProjectionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    projection_id: str
    checkpoint_digest: Digest
    record_digests: tuple[str, ...] = Field(max_length=10_000)
    edge_digests: tuple[str, ...] = Field(max_length=10_000)
    current_state_digests: tuple[str, ...] = Field(max_length=10_000)
    input_record_digests: tuple[str, ...] = Field(max_length=10_000)
    tombstone_digests: tuple[str, ...] = Field(max_length=10_000)
    revision: int = Field(ge=0)
    rebuilt_at: datetime


class ProjectionEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    output_ref: ResourceRef
    input_ref: ResourceRef
    edge_digest: Digest


class ProjectionAlias(BaseModel):
    """Trusted exact-ref alias to a canonical temporal output."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    alias_ref: ResourceRef
    canonical_ref: ResourceRef
    alias_digest: Digest


class ProjectionTombstone(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str
    target_ref: ResourceRef
    effective_at: datetime
    recorded_at: datetime
    reason: str = Field(min_length=1, max_length=1024)
    tombstone_digest: Digest


class ProjectionCurrentState(BaseModel):
    """One rebuildable current-state row for one temporal entity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    projection_id: str
    entity_authority: str
    entity_id: str
    record_digests: tuple[Digest, ...] = Field(max_length=128)
    input_record_digests: tuple[Digest, ...] = Field(max_length=128)
    dependency_states: tuple["ProjectionDependencyState", ...] = Field(max_length=256)
    valid_at: datetime
    known_at: datetime
    revision: int = Field(ge=1)
    state_digest: Digest


class ProjectionDependencyState(BaseModel):
    """Exact input identity and the state observed while materializing output."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    input_ref: ResourceRef
    status: str = Field(pattern=r"^(current|dirty|stale|missing)$")


class ProjectionDirtyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    projection_id: str
    output_ref: ResourceRef
    cause_ref: ResourceRef
    reason: str = Field(min_length=1, max_length=1024)
    enqueued_at: datetime
    cleared_at: datetime | None = None
    dirty_digest: Digest


class ProjectionDirtyEvent(BaseModel):
    """Append-only recorded-time transition for one dirty queue output."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: Digest
    projection_id: str
    output_ref: ResourceRef
    transition: str = Field(pattern=r"^(enqueue|clear)$")
    event_at: datetime
    queue_revision: int = Field(ge=1)
    entry: ProjectionDirtyEntry


class ProjectionRecomputeRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    projection_id: str
    affected_outputs: tuple[ResourceRef, ...] = Field(max_length=10_000)
    changed_entities: tuple[str, ...] = Field(max_length=10_000)
    recomputed_at: datetime
    run_digest: Digest


def _record_ref(record: TemporalRecord) -> ResourceRef:
    """Typed exact output ref used in dependency edges, never a bare digest."""

    return ResourceRef(
        authority="krail://temporal-projection",
        resource_type="temporal-record",
        resource_id=record.record_id,
        version=record.revision,
        digest=record.record_digest,
    )


def _tombstone_digest(values: dict[str, object]) -> str:
    return _digest(values)


def create_projection_tombstone(
    *, event_id: str, target_ref: ResourceRef, reason: str, effective_at: datetime, recorded_at: datetime
) -> ProjectionTombstone:
    """Create the exact immutable event before a caller signs its write grant."""
    provisional = {
        "event_id": event_id, "target_ref": target_ref.model_dump(mode="json"),
        "effective_at": effective_at.isoformat(), "recorded_at": recorded_at.isoformat(),
        "reason": reason,
    }
    return ProjectionTombstone(**provisional, tombstone_digest=_tombstone_digest(provisional))


class TemporalProjectionService:
    """Idempotent temporal ingest and rebuild over the canonical store."""

    def __init__(self, path: str, *, tenant_id: str, project_id: str, clock=None) -> None:
        self.store = JsonSemanticStore(path)
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def record_ref(record: TemporalRecord) -> ResourceRef:
        return _record_ref(record)

    @staticmethod
    def current_state_ref(state: ProjectionCurrentState) -> ResourceRef:
        """Exact reference to the persisted current-state materialization."""

        return ResourceRef(
            authority="krail://temporal-projection",
            resource_type="projection-current-state",
            resource_id=TemporalProjectionService._current_id(
                state.projection_id,
                TemporalProjectionService._entity_key_from_identity(
                    state.entity_authority, state.entity_id
                ),
            ),
            version=str(state.revision),
            digest=state.state_digest,
        )

    def _rows(self, kind: str):
        return self.store.list(self.tenant_id, self.project_id, kind=kind)  # type: ignore[arg-type]

    @staticmethod
    def _scope_cursor_id() -> str:
        return "temporal-records"

    def scope_cursor(self) -> int:
        """Canonical tenant/project change cursor for temporal readers."""
        row = self.store.get(self.tenant_id, self.project_id, "temporal_scope_cursor", self._scope_cursor_id())
        return row.revision if row is not None else 0

    def _advance_scope_cursor_locked(self, *, at: datetime, temporal_record_id: str | None = None) -> int:
        row = self.store.get(self.tenant_id, self.project_id, "temporal_scope_cursor", self._scope_cursor_id())
        revision = (row.revision if row else 0) + 1
        self.store.put(SemanticRow(
            tenant_id=self.tenant_id, project_id=self.project_id,
            record_kind="temporal_scope_cursor", record_id=self._scope_cursor_id(),
            revision=revision, payload={"revision": revision},
            created_at=row.created_at if row else at, updated_at=at,
        ), expected_revision=row.revision if row else 0)
        self.store.put(SemanticRow(
            tenant_id=self.tenant_id, project_id=self.project_id,
            record_kind="temporal_scope_change", record_id=f"{revision:020d}",
            revision=1, payload={"cursor": revision, "temporal_record_id": temporal_record_id},
            created_at=at, updated_at=at,
        ), expected_revision=0)
        return revision

    def touch_scope_cursor(self, *, at: datetime) -> int:
        """Publish any cached world-memory mutation to scoped readers."""
        with self.store.transaction():
            return self._advance_scope_cursor_locked(at=at)

    def temporal_changes_after(self, cursor: int, *, limit: int = 256) -> tuple[int, tuple[str, ...]] | None:
        """Return exact temporal row IDs since a prior canonical cursor.

        ``None`` asks callers to rebuild: an old store without ledger rows,
        an oversized gap, or a non-temporal change must never be guessed.
        """
        current = self.scope_cursor()
        if cursor == current:
            return current, ()
        if cursor < 0 or current - cursor > limit:
            return None
        ids: list[str] = []
        for revision in range(cursor + 1, current + 1):
            row = self.store.get(self.tenant_id, self.project_id, "temporal_scope_change", f"{revision:020d}")
            if row is None or row.payload.get("cursor") != revision:
                return None
            record_id = row.payload.get("temporal_record_id")
            if record_id is None:
                return None
            ids.append(str(record_id))
        if len(set(ids)) != len(ids):
            return None
        return current, tuple(ids)

    @staticmethod
    def _entity_key(record: TemporalRecord) -> str:
        return _digest({"authority": record.entity_authority, "entity_id": record.entity_id})

    @staticmethod
    def _entity_key_from_identity(entity_authority: str, entity_id: str) -> str:
        return _digest({"authority": entity_authority, "entity_id": entity_id})

    @staticmethod
    def _current_id(projection_id: str, entity_key: str) -> str:
        return _digest({"projection_id": projection_id, "entity_key": entity_key})

    @staticmethod
    def _dirty_id(projection_id: str, output_ref: ResourceRef) -> str:
        return _digest({"projection_id": projection_id, "output_ref": output_ref.model_dump(mode="json")})

    def _put_dirty_event_locked(
        self,
        *,
        entry: ProjectionDirtyEntry,
        transition: str,
        event_at: datetime,
        queue_revision: int,
    ) -> None:
        body = {
            "projection_id": entry.projection_id,
            "output_ref": entry.output_ref.model_dump(mode="json"),
            "transition": transition,
            "event_at": event_at.isoformat(),
            "queue_revision": queue_revision,
            "entry": entry.model_dump(mode="json"),
        }
        event = ProjectionDirtyEvent(event_id=_digest(body), **body)
        self.store.put(
            SemanticRow(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                record_kind="procedure_projection_dirty_event",
                record_id=event.event_id,
                revision=1,
                payload={"event": event.model_dump(mode="json")},
                created_at=event_at,
                updated_at=event_at,
            ),
            expected_revision=0,
        )

    def _ensure_dirty_history_locked(
        self, *, entry: ProjectionDirtyEntry, queue_revision: int
    ) -> None:
        """Seed the exact interval still available in a pre-ledger queue row."""

        events = (
            ProjectionDirtyEvent.model_validate(row.payload["event"])
            for row in self.store.list(
                self.tenant_id,
                self.project_id,
                kind="procedure_projection_dirty_event",
            )
        )
        if any(
            event.output_ref.exact_key == entry.output_ref.exact_key
            and event.projection_id == entry.projection_id
            for event in events
        ):
            return
        body = entry.model_dump(mode="json")
        body["cleared_at"] = None
        body.pop("dirty_digest")
        enqueued = ProjectionDirtyEntry(**body, dirty_digest=_digest(body))
        enqueue_revision = max(1, queue_revision - (entry.cleared_at is not None))
        self._put_dirty_event_locked(
            entry=enqueued,
            transition="enqueue",
            event_at=entry.enqueued_at,
            queue_revision=enqueue_revision,
        )
        if entry.cleared_at is not None:
            self._put_dirty_event_locked(
                entry=entry,
                transition="clear",
                event_at=entry.cleared_at,
                queue_revision=queue_revision,
            )

    def _known_projection_ids_locked(self) -> tuple[str, ...]:
        """Existing checkpoints name all materializations that need an update."""
        values = {"default"}
        for row in self.store.list(self.tenant_id, self.project_id, kind="procedure_projection_checkpoint"):
            values.add(ProjectionCheckpoint.model_validate(row.payload["checkpoint"]).projection_id)
        return tuple(sorted(values))

    def _resolve_alias_locked(self, ref: ResourceRef) -> ResourceRef | None:
        for row in self.store.list(self.tenant_id, self.project_id, kind="procedure_projection_alias"):
            alias = ProjectionAlias.model_validate(row.payload["alias"])
            if alias.alias_ref.exact_key == ref.exact_key:
                return alias.canonical_ref
        return None

    def _put_edge_locked(self, *, output_ref: ResourceRef, input_ref: ResourceRef, at: datetime) -> None:
        edge = ProjectionEdge(
            output_ref=output_ref,
            input_ref=input_ref,
            edge_digest=_digest({"output_ref": output_ref.model_dump(mode="json"), "input_ref": input_ref.model_dump(mode="json")}),
        )
        if self.store.get(self.tenant_id, self.project_id, "procedure_projection_edge", edge.edge_digest) is None:
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id, project_id=self.project_id,
                    record_kind="procedure_projection_edge", record_id=edge.edge_digest,
                    revision=1, payload={"edge": edge.model_dump(mode="json")},
                    created_at=at, updated_at=at,
                ), expected_revision=0,
            )

    def register_alias(self, alias_ref: ResourceRef, canonical_ref: ResourceRef, *, at: datetime) -> ProjectionAlias:
        """Persist one trusted immutable exact-ref alias and repair dependent edges."""

        if not any(_record_ref(record).exact_key == canonical_ref.exact_key for record in self._records()):
            raise ValueError("projection alias must target an existing exact temporal record")
        body = {"alias_ref": alias_ref.model_dump(mode="json"), "canonical_ref": canonical_ref.model_dump(mode="json")}
        alias = ProjectionAlias(**body, alias_digest=_digest(body))
        alias_id = _digest({"alias_ref": body["alias_ref"]})
        with self.store.transaction():
            current = self.store.get(self.tenant_id, self.project_id, "procedure_projection_alias", alias_id)
            if current is not None:
                existing = ProjectionAlias.model_validate(current.payload["alias"])
                if existing.alias_digest != alias.alias_digest:
                    raise ValueError("conflicting projection alias replay")
            else:
                self.store.put(SemanticRow(
                    tenant_id=self.tenant_id, project_id=self.project_id,
                    record_kind="procedure_projection_alias", record_id=alias_id,
                    revision=1, payload={"alias": alias.model_dump(mode="json")},
                    created_at=at, updated_at=at,
                ), expected_revision=0)
                self._advance_scope_cursor_locked(at=at)
            for row in self.store.list(self.tenant_id, self.project_id, kind="temporal_record"):
                output = TemporalRecord.model_validate(row.payload["record"])
                if any(ref.exact_key == alias_ref.exact_key for ref in output.source_refs + output.provenance_refs):
                    self._put_edge_locked(output_ref=_record_ref(output), input_ref=canonical_ref, at=at)
        return alias

    def _ingest_locked(self, record: TemporalRecord, *, at: datetime) -> TemporalRecord:
        payload = {"record": record.model_dump(mode="json")}
        current = self.store.get(self.tenant_id, self.project_id, "temporal_record", record.record_digest)
        if current is not None:
            if current.payload != payload:
                raise ValueError("conflicting temporal record replay")
            return record
        self.store.put(
            SemanticRow(
                tenant_id=self.tenant_id, project_id=self.project_id,
                record_kind="temporal_record", record_id=record.record_digest,
                revision=1, payload=payload, created_at=at, updated_at=at,
            ), expected_revision=0,
        )
        self._advance_scope_cursor_locked(at=at, temporal_record_id=record.record_digest)
        output_ref = _record_ref(record)
        input_refs = record.source_refs + record.provenance_refs
        if record.supersedes_digest is not None:
            parent = self.store.get(
                self.tenant_id, self.project_id, "temporal_record", record.supersedes_digest
            )
            if parent is not None:
                input_refs += (_record_ref(TemporalRecord.model_validate(parent.payload["record"])),)
        expanded_inputs: dict[tuple[str, str, str, str, str], ResourceRef] = {}
        for ref in input_refs:
            expanded_inputs.setdefault(ref.exact_key, ref)
            alias = self._resolve_alias_locked(ref)
            if alias is not None:
                expanded_inputs.setdefault(alias.exact_key, alias)
        for ref in expanded_inputs.values():
            self._put_edge_locked(output_ref=output_ref, input_ref=ref, at=at)
        for projection_id in self._known_projection_ids_locked():
            self._enqueue_locked(
                projection_id=projection_id, output_ref=output_ref, cause_ref=output_ref,
                reason="temporal record ingested", at=at,
            )
            if record.supersedes_digest is not None:
                parent = self.store.get(
                    self.tenant_id, self.project_id, "temporal_record", record.supersedes_digest
                )
                if parent is not None:
                    parent_ref = _record_ref(TemporalRecord.model_validate(parent.payload["record"]))
                    for dependent in self.affected_region(parent_ref):
                        self._enqueue_locked(
                            projection_id=projection_id, output_ref=dependent, cause_ref=parent_ref,
                            reason="temporal record superseded", at=at,
                        )
        return record

    def ingest(self, record: TemporalRecord, *, at: datetime, writer: ProjectionWriter) -> TemporalRecord:
        verify_temporal_record_integrity(record)
        writer.authorize(record, at=self.clock())
        with self.store.transaction():
            return self._ingest_locked(record, at=at)

    def ingest_many(self, records: tuple[TemporalRecord, ...], *, at: datetime, writer: ProjectionWriter, before_commit=None) -> tuple[TemporalRecord, ...]:
        """Atomically ingest a bounded batch through the same writer authority."""
        for record in records:
            verify_temporal_record_integrity(record)
            writer.authorize(record, at=self.clock())
        with self.store.transaction():
            result = tuple(self._ingest_locked(record, at=at) for record in records)
            if before_commit is not None:
                before_commit()
            return result

    def tombstone(
        self,
        target_ref: ResourceRef,
        *,
        event_id: str,
        reason: str,
        effective_at: datetime,
        recorded_at: datetime,
        authorizer: ProjectionInvalidationAuthorizer,
    ) -> ProjectionTombstone:
        """Persist an immutable removal fact under the live write action.

        ``recorded_at`` is evidence chronology only.  The authorizer receives
        the current clock so an expired/revoked signed context cannot be
        revived by backdating the tombstone.
        """
        tombstone = create_projection_tombstone(
            event_id=event_id, target_ref=target_ref, reason=reason,
            effective_at=effective_at, recorded_at=recorded_at,
        )
        if not callable(getattr(authorizer, "authorize_invalidation", None)):
            raise PermissionError("projection invalidation action denied")
        live_at = self.clock()
        try:
            authorizer.authorize_invalidation(event_id, target_ref, tombstone.tombstone_digest, at=live_at)
        except PermissionError as exc:
            raise PermissionError("projection invalidation action denied") from exc
        with self.store.transaction():
            current = self.store.get(self.tenant_id, self.project_id, "procedure_projection_tombstone", event_id)
            if current is not None:
                existing = ProjectionTombstone.model_validate(current.payload["tombstone"])
                if existing.tombstone_digest != tombstone.tombstone_digest:
                    raise ValueError("conflicting projection tombstone identifier replay")
                authorizer.authorize_invalidation(event_id, target_ref, tombstone.tombstone_digest, at=self.clock())
                return existing
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id, project_id=self.project_id,
                    record_kind="procedure_projection_tombstone", record_id=event_id,
                    revision=1, payload={"tombstone": tombstone.model_dump(mode="json")},
                    created_at=recorded_at, updated_at=recorded_at,
                ), expected_revision=0,
            )
            self._advance_scope_cursor_locked(at=recorded_at)
            for projection_id in self._known_projection_ids_locked():
                self._enqueue_locked(
                    projection_id=projection_id, output_ref=target_ref, cause_ref=target_ref,
                    reason="temporal record tombstoned", at=recorded_at,
                )
                # External exact source invalidations must also dirty the
                # records that consumed them. Rebuild derives the same stale
                # dependency state from this immutable event.
                for dependent in self.affected_region(target_ref):
                    self._enqueue_locked(
                        projection_id=projection_id, output_ref=dependent, cause_ref=target_ref,
                        reason="temporal dependency invalidated", at=recorded_at,
                    )
        authorizer.authorize_invalidation(event_id, target_ref, tombstone.tombstone_digest, at=self.clock())
        return tombstone

    def affected_region(self, changed_ref: ResourceRef) -> tuple[ResourceRef, ...]:
        """Compute the bounded reverse exact-edge region without mutating history."""

        edges = [
            ProjectionEdge.model_validate(row.payload["edge"])
            for row in self.store.list(self.tenant_id, self.project_id, kind="procedure_projection_edge")
        ]
        frontier = {changed_ref.exact_key}
        affected: dict[str, ResourceRef] = {}
        while frontier:
            next_frontier: set[str] = set()
            for edge in edges:
                if edge.input_ref.exact_key in frontier and edge.output_ref.exact_key not in affected:
                    affected[edge.output_ref.exact_key] = edge.output_ref
                    next_frontier.add(edge.output_ref.exact_key)
            frontier = next_frontier
        return tuple(affected[key] for key in sorted(affected))

    def _enqueue_locked(
        self, *, projection_id: str, output_ref: ResourceRef, cause_ref: ResourceRef, reason: str, at: datetime
    ) -> ProjectionDirtyEntry:
        entry_id = self._dirty_id(projection_id, output_ref)
        provisional = {
            "projection_id": projection_id, "output_ref": output_ref.model_dump(mode="json"),
            "cause_ref": cause_ref.model_dump(mode="json"), "reason": reason,
            "enqueued_at": at.isoformat(), "cleared_at": None,
        }
        entry = ProjectionDirtyEntry(**provisional, dirty_digest=_digest(provisional))
        current = self.store.get(self.tenant_id, self.project_id, "procedure_projection_dirty", entry_id)
        if current is None:
            self.store.put(SemanticRow(
                tenant_id=self.tenant_id, project_id=self.project_id, record_kind="procedure_projection_dirty",
                record_id=entry_id, revision=1, payload={"entry": entry.model_dump(mode="json")}, created_at=at, updated_at=at,
            ), expected_revision=0)
            self._put_dirty_event_locked(
                entry=entry, transition="enqueue", event_at=at, queue_revision=1
            )
            return entry
        existing = ProjectionDirtyEntry.model_validate(current.payload["entry"])
        self._ensure_dirty_history_locked(
            entry=existing, queue_revision=current.revision
        )
        if existing.cleared_at is None:
            if existing.cause_ref == cause_ref and existing.reason == reason:
                return existing
            # A later exact invalidation supersedes an ingest-only dirty hint,
            # including when both name the same temporal record. Keep the
            # actionable cause and reason that must survive recomputation.
            self.store.put(SemanticRow(
                tenant_id=self.tenant_id, project_id=self.project_id, record_kind="procedure_projection_dirty",
                record_id=entry_id, revision=current.revision + 1, payload={"entry": entry.model_dump(mode="json")},
                created_at=current.created_at, updated_at=at,
            ), expected_revision=current.revision)
            self._put_dirty_event_locked(
                entry=entry,
                transition="enqueue",
                event_at=at,
                queue_revision=current.revision + 1,
            )
            return entry
        self.store.put(SemanticRow(
            tenant_id=self.tenant_id, project_id=self.project_id, record_kind="procedure_projection_dirty",
            record_id=entry_id, revision=current.revision + 1, payload={"entry": entry.model_dump(mode="json")},
            created_at=current.created_at, updated_at=at,
        ), expected_revision=current.revision)
        self._put_dirty_event_locked(
            entry=entry,
            transition="enqueue",
            event_at=at,
            queue_revision=current.revision + 1,
        )
        return entry

    def mark_dirty(self, changed_ref: ResourceRef, *, projection_id: str, reason: str, at: datetime) -> tuple[ResourceRef, ...]:
        affected = self.affected_region(changed_ref)
        with self.store.transaction():
            for output_ref in affected:
                self._enqueue_locked(projection_id=projection_id, output_ref=output_ref, cause_ref=changed_ref, reason=reason, at=at)
        return affected

    def dirty_outputs(self, projection_id: str) -> tuple[ResourceRef, ...]:
        values = [ProjectionDirtyEntry.model_validate(row.payload["entry"]) for row in self._rows("procedure_projection_dirty")]
        return tuple(sorted((item.output_ref for item in values if item.projection_id == projection_id and item.cleared_at is None), key=lambda ref: ref.exact_key))

    def dirty_entries_at(
        self, projection_id: str, *, known_at: datetime
    ) -> tuple[ProjectionDirtyEntry, ...]:
        """Return dirty work visible at one recorded-time cutoff.

        A later correction must not contaminate an earlier knowledge query, and
        clearing rebuild work must not erase the interval during which callers
        were required to abstain.
        """

        if known_at.tzinfo is None or known_at.utcoffset() is None:
            raise ValueError("known_at must include a timezone")
        events = [
            ProjectionDirtyEvent.model_validate(row.payload["event"])
            for row in self._rows("procedure_projection_dirty_event")
        ]
        latest: dict[tuple[str, str, str, str, str], ProjectionDirtyEvent] = {}
        for event in events:
            if event.projection_id != projection_id or event.event_at > known_at:
                continue
            key = event.output_ref.exact_key
            prior = latest.get(key)
            if prior is None or (event.event_at, event.queue_revision) > (
                prior.event_at,
                prior.queue_revision,
            ):
                latest[key] = event

        # Stores created before the event ledger retain their best available
        # mutable queue state until that output receives its first transition.
        event_keys = {
            event.output_ref.exact_key
            for event in events
            if event.projection_id == projection_id
        }
        legacy = [
            ProjectionDirtyEntry.model_validate(row.payload["entry"])
            for row in self._rows("procedure_projection_dirty")
        ]
        values = [
            event.entry for event in latest.values() if event.transition == "enqueue"
        ]
        values.extend(
            item
            for item in legacy
            if item.projection_id == projection_id
            and item.output_ref.exact_key not in event_keys
            and item.enqueued_at <= known_at
            and (item.cleared_at is None or known_at < item.cleared_at)
        )
        return tuple(
            sorted(
                values,
                key=lambda item: item.output_ref.exact_key,
            )
        )

    def _records(self) -> tuple[TemporalRecord, ...]:
        return tuple(TemporalRecord.model_validate(row.payload["record"]) for row in self._rows("temporal_record"))

    def temporal_records(self) -> tuple[TemporalRecord, ...]:
        """Return integrity-checked canonical temporal rows to typed adapters."""

        records = self._records()
        for record in records:
            verify_temporal_record_integrity(record)
        return records

    def _tombstones(self) -> tuple[ProjectionTombstone, ...]:
        return tuple(ProjectionTombstone.model_validate(row.payload["tombstone"]) for row in self._rows("procedure_projection_tombstone"))

    def active_invalidation_refs(self, *, valid_at: datetime, known_at: datetime) -> tuple[ResourceRef, ...]:
        """Exact invalidation targets visible at a bitemporal query cutoff."""

        return tuple(sorted(
            (item.target_ref for item in self._tombstones() if item.effective_at <= valid_at and item.recorded_at <= known_at),
            key=lambda ref: ref.exact_key,
        ))

    def _transitive_stale_keys(self, direct_keys: set[str], refs: tuple[ResourceRef, ...]) -> set[str]:
        """A stale exact input makes every reverse-edge dependent stale too."""

        values = set(direct_keys)
        for ref in refs:
            values.update(item.exact_key for item in self.affected_region(ref))
        return values

    def _current_records(self, records: tuple[TemporalRecord, ...], *, valid_at: datetime, known_at: datetime) -> tuple[TemporalRecord, ...]:
        tombstoned = {
            item.target_ref.exact_key for item in self._tombstones()
            if item.effective_at <= valid_at and item.recorded_at <= known_at
        }
        # One entity may carry independent assertions from several declared
        # authorities/writer families. Replay supersession within each exact
        # history identity, then retain the live competitors for the projection
        # to represent as conflict rather than mixing their revision chains.
        by_history: dict[tuple[str, str, str, str, str, str], list[TemporalRecord]] = {}
        for record in records:
            key = (
                record.entity_authority,
                record.entity_id,
                record.payload_schema,
                record.payload_schema_version,
                record.authority,
                record.writer_family,
            )
            by_history.setdefault(key, []).append(record)
        return tuple(
            item for key in sorted(by_history)
            for group in (by_history[key],)
            for item in query_temporal_records(group, valid_at=valid_at, known_at=known_at)
            if _record_ref(item).exact_key not in tombstoned
        )

    def _dependency_states(
        self, *, selected: tuple[TemporalRecord, ...], records: tuple[TemporalRecord, ...],
        dirty_keys: set[str], stale_keys: set[str], direct_stale_keys: set[str],
        current_keys: set[str], valid_at: datetime, known_at: datetime,
    ) -> tuple[ProjectionDependencyState, ...]:
        by_ref = {_record_ref(item).exact_key: item for item in records}
        states: dict[str, ProjectionDependencyState] = {}

        def stale_authority_input(ref: ResourceRef, seen: set[tuple[str, str, str, str, str]]) -> bool:
            """Propagate explicit canonical freshness through record edges."""
            if ref.exact_key in seen:
                return False
            if ref.exact_key in stale_keys or ref.exact_key in dirty_keys:
                return True
            resolved = self._resolve_alias_locked(ref)
            if resolved is not None:
                return stale_authority_input(resolved, {*seen, ref.exact_key})
            source = by_ref.get(ref.exact_key)
            if source is None:
                return False
            if (
                source.freshness != "current"
                or _record_ref(source).exact_key not in current_keys
                or _record_ref(source).exact_key in stale_keys
                or _record_ref(source).exact_key in dirty_keys
            ):
                return True
            next_seen = {*seen, ref.exact_key}
            return any(stale_authority_input(child, next_seen) for child in source.source_refs + source.provenance_refs)
        for record in selected:
            input_refs = record.source_refs + record.provenance_refs
            explicit_input_keys = {ref.exact_key for ref in input_refs}
            if record.supersedes_digest is not None:
                parent = next((item for item in records if item.record_digest == record.supersedes_digest), None)
                if parent is not None:
                    input_refs += (_record_ref(parent),)
            expanded_inputs: dict[tuple[str, str, str, str, str], ResourceRef] = {}
            for input_ref in input_refs:
                expanded_inputs.setdefault(input_ref.exact_key, input_ref)
                alias = self._resolve_alias_locked(input_ref)
                if alias is not None:
                    expanded_inputs.setdefault(alias.exact_key, alias)
            lineage_parent_keys = {
                _record_ref(item).exact_key for item in records
                if record.supersedes_digest == item.record_digest
            }
            for input_ref in expanded_inputs.values():
                if (
                    input_ref.exact_key in lineage_parent_keys
                    and input_ref.exact_key not in explicit_input_keys
                ):
                    # Supersession is lineage, not an inherited dependency on
                    # every source consumed by the prior revision. A successor
                    # that replaces invalid evidence may recover after review;
                    # an explicit invalidation of the parent revision itself
                    # still fails closed.
                    status = "stale" if input_ref.exact_key in direct_stale_keys else "current"
                elif input_ref.exact_key in stale_keys:
                    status = "stale"
                elif input_ref.exact_key in dirty_keys:
                    status = "dirty"
                elif stale_authority_input(input_ref, set()):
                    status = "stale"
                elif input_ref.exact_key in by_ref:
                    status = "current" if input_ref.exact_key in current_keys else "stale"
                else:
                    # External exact refs are immutable inputs here; resolving
                    # their provider freshness is outside this projection.
                    status = "current"
                states[input_ref.exact_key] = ProjectionDependencyState(input_ref=input_ref, status=status)
        return tuple(states[key] for key in sorted(states))

    def _write_current_locked(self, *, projection_id: str, entity: tuple[str, str], selected: tuple[TemporalRecord, ...], inputs: tuple[TemporalRecord, ...], dependency_states: tuple[ProjectionDependencyState, ...], valid_at: datetime, known_at: datetime, at: datetime) -> bool:
        record_digests = tuple(sorted(item.record_digest for item in selected))
        input_record_digests = tuple(sorted({
            *(item.record_digest for item in inputs if (item.ingested_at or item.recorded_at) <= known_at),
            *(item.input_ref.digest for item in dependency_states),
        }))
        body = {"projection_id": projection_id, "entity_authority": entity[0], "entity_id": entity[1], "record_digests": record_digests, "input_record_digests": input_record_digests, "dependency_states": [item.model_dump(mode="json") for item in dependency_states], "valid_at": valid_at.isoformat(), "known_at": known_at.isoformat()}
        row_id = self._current_id(projection_id, self._entity_key(selected[0]) if selected else _digest({"authority": entity[0], "entity_id": entity[1]}))
        current = self.store.get(self.tenant_id, self.project_id, "procedure_projection_current", row_id)
        state_digest = _digest(body)
        if current is not None and ProjectionCurrentState.model_validate(current.payload["state"]).state_digest == state_digest:
            return False
        revision = (current.revision if current else 0) + 1
        state = ProjectionCurrentState(**body, revision=revision, state_digest=state_digest)
        self.store.put(SemanticRow(
            tenant_id=self.tenant_id, project_id=self.project_id, record_kind="procedure_projection_current", record_id=row_id,
            revision=revision, payload={"state": state.model_dump(mode="json")},
            created_at=current.created_at if current else at, updated_at=at,
        ), expected_revision=current.revision if current else 0)
        return True

    def recompute(self, *, projection_id: str, valid_at: datetime, known_at: datetime, at: datetime) -> ProjectionRecomputeRun:
        """Consume only durable dirty outputs and leave equal current rows untouched."""
        with self.store.transaction():
            pending_entries = [
                ProjectionDirtyEntry.model_validate(row.payload["entry"])
                for row in self.store.list(self.tenant_id, self.project_id, kind="procedure_projection_dirty")
                if row.payload["entry"]["projection_id"] == projection_id
                and row.payload["entry"].get("cleared_at") is None
            ]
            tombstone_targets = {item.target_ref.exact_key for item in self._tombstones()}
            active_invalidation_keys = {
                ref.exact_key for ref in self.active_invalidation_refs(valid_at=valid_at, known_at=known_at)
            }
            # Dirty entries caused by a canonical invalidation remain pending
            # until that event is visible at this bitemporal query cutoff.
            # Ingest/supersession hints have no separate event cutoff and are
            # eligible immediately.
            eligible_entries = [
                item for item in pending_entries
                if item.cause_ref.exact_key not in tombstone_targets
                or item.cause_ref.exact_key in active_invalidation_keys
            ]
            pending = tuple(sorted((item.output_ref for item in eligible_entries), key=lambda ref: ref.exact_key))
            consumed_revisions = {
                self._dirty_id(projection_id, item.output_ref): self.store.get(
                    self.tenant_id, self.project_id, "procedure_projection_dirty", self._dirty_id(projection_id, item.output_ref)
                ).revision
                for item in eligible_entries
            }
            records = tuple(TemporalRecord.model_validate(row.payload["record"]) for row in self.store.list(self.tenant_id, self.project_id, kind="temporal_record"))
            by_ref = {_record_ref(record).exact_key: record for record in records}
            entities = {(by_ref[item.exact_key].entity_authority, by_ref[item.exact_key].entity_id) for item in pending if item.exact_key in by_ref}
            # Evaluate only outputs being recomputed and their exact direct
            # temporal inputs.  This gives dependency state its real temporal
            # meaning without replaying unrelated entities.
            evaluation_entities = set(entities)
            for output_ref in pending:
                output = by_ref.get(output_ref.exact_key)
                if output is None:
                    continue
                for input_ref in output.source_refs + output.provenance_refs:
                    parent = by_ref.get(input_ref.exact_key)
                    if parent is not None:
                        evaluation_entities.add((parent.entity_authority, parent.entity_id))
            by_entity: dict[tuple[str, str], list[TemporalRecord]] = {}
            for item in records:
                if (item.entity_authority, item.entity_id) in evaluation_entities:
                    by_entity.setdefault((item.entity_authority, item.entity_id), []).append(item)
            current_records = tuple(
                item for group in by_entity.values()
                for item in self._current_records(tuple(group), valid_at=valid_at, known_at=known_at)
            )
            current_keys = {_record_ref(item).exact_key for item in current_records}
            dirty_keys = {item.exact_key for item in pending}
            active_invalidation_refs = self.active_invalidation_refs(valid_at=valid_at, known_at=known_at)
            # Ingest and supersession enqueue temporal-record refs as work
            # hints; they are not invalidation facts. External dirty causes
            # remain stale signals for adapters whose canonical invalidation
            # event lives in the shared semantic store rather than here.
            direct_stale_keys = {
                *{
                    item.cause_ref.exact_key
                    for item in eligible_entries
                    if item.cause_ref.exact_key not in tombstone_targets
                    and (
                        item.cause_ref.exact_key not in by_ref
                        or item.reason not in {
                            "temporal record ingested",
                            "temporal record superseded",
                        }
                    )
                },
                *active_invalidation_keys,
            }
            stale_keys = self._transitive_stale_keys(direct_stale_keys, active_invalidation_refs)
            selected = {entity: [] for entity in entities}
            for item in current_records:
                entity = (item.entity_authority, item.entity_id)
                if entity in selected:
                    selected[entity].append(item)
            changed: list[str] = []
            for entity in sorted(entities):
                inputs = tuple(by_entity[entity])
                dependencies = self._dependency_states(selected=tuple(selected[entity]), records=records, dirty_keys=dirty_keys, stale_keys=stale_keys, direct_stale_keys=direct_stale_keys, current_keys=current_keys, valid_at=valid_at, known_at=known_at)
                if self._write_current_locked(projection_id=projection_id, entity=entity, selected=tuple(selected[entity]), inputs=inputs, dependency_states=dependencies, valid_at=valid_at, known_at=known_at, at=at):
                    changed.append(self._entity_key(by_ref[next(item.exact_key for item in pending if item.exact_key in by_ref and (by_ref[item.exact_key].entity_authority, by_ref[item.exact_key].entity_id) == entity)]))
            for output_ref in pending:
                entry_id = self._dirty_id(projection_id, output_ref)
                row = self.store.get(self.tenant_id, self.project_id, "procedure_projection_dirty", entry_id)
                if row is None or row.revision != consumed_revisions[entry_id]:
                    continue
                entry = ProjectionDirtyEntry.model_validate(row.payload["entry"])
                if entry.cleared_at is not None:
                    continue
                self._ensure_dirty_history_locked(
                    entry=entry, queue_revision=row.revision
                )
                body = entry.model_dump(mode="json")
                body["cleared_at"] = at.isoformat()
                body.pop("dirty_digest")
                cleared = ProjectionDirtyEntry(**body, dirty_digest=_digest(body))
                self.store.put(SemanticRow(tenant_id=self.tenant_id, project_id=self.project_id, record_kind="procedure_projection_dirty", record_id=entry_id, revision=row.revision + 1, payload={"entry": cleared.model_dump(mode="json")}, created_at=row.created_at, updated_at=at), expected_revision=row.revision)
                self._put_dirty_event_locked(
                    entry=cleared,
                    transition="clear",
                    event_at=at,
                    queue_revision=row.revision + 1,
                )
            run_body = {"projection_id": projection_id, "affected_outputs": [item.model_dump(mode="json") for item in pending], "changed_entities": sorted(changed), "recomputed_at": at.isoformat()}
            run = ProjectionRecomputeRun(**run_body, run_digest=_digest(run_body))
            existing_run = self.store.get(self.tenant_id, self.project_id, "procedure_projection_recompute_run", run.run_digest)
            if existing_run is not None:
                return ProjectionRecomputeRun.model_validate(existing_run.payload["run"])
            self.store.put(SemanticRow(tenant_id=self.tenant_id, project_id=self.project_id, record_kind="procedure_projection_recompute_run", record_id=run.run_digest, revision=1, payload={"run": run.model_dump(mode="json")}, created_at=at, updated_at=at), expected_revision=0)
        return run

    def current_state(self, projection_id: str) -> tuple[ProjectionCurrentState, ...]:
        return tuple(sorted((ProjectionCurrentState.model_validate(row.payload["state"]) for row in self._rows("procedure_projection_current") if row.payload["state"]["projection_id"] == projection_id), key=lambda value: (value.entity_authority, value.entity_id)))

    def actionable_record(self, record: TemporalRecord, *, projection_id: str) -> bool:
        """Evaluate one exact output against live canonical projection state.

        This is intentionally candidate-scoped: immutable/history reads may
        contain several independent roots, but no root becomes actionable while
        another non-invalidated root for the same entity remains current.
        Persisted tombstones are consulted at the live clock, so a stale cached
        materialization cannot leak guidance before recomputation or restart.
        """

        verify_temporal_record_integrity(record)
        # A selected record that its authoritative procedure/source marked
        # stale, missing, or conflicted is an abstention result even when its
        # temporal interval and cached dependencies still look current.
        if record.freshness != "current":
            return False
        with self.store.transaction():
            now = self.clock()
            records = self._records()
            by_digest = {item.record_digest: item for item in records}
            if record.record_digest not in by_digest:
                return False
            current = self._current_records(records, valid_at=now, known_at=now)
            live_entity = tuple(
                item for item in current
                if (item.entity_authority, item.entity_id)
                == (record.entity_authority, record.entity_id)
            )
            if record.record_digest not in {item.record_digest for item in live_entity}:
                return False
            active_refs = self.active_invalidation_refs(valid_at=now, known_at=now)
            direct_stale_keys = {ref.exact_key for ref in active_refs}
            stale_keys = self._transitive_stale_keys(direct_stale_keys, active_refs)
            record_ref = _record_ref(record)
            if record_ref.exact_key in stale_keys:
                return False
            live_competitors = tuple(
                item for item in live_entity
                if item.record_digest != record.record_digest
                and _record_ref(item).exact_key not in stale_keys
            )
            if live_competitors:
                return False
            pending = tuple(
                ProjectionDirtyEntry.model_validate(row.payload["entry"])
                for row in self._rows("procedure_projection_dirty")
                if row.payload["entry"]["projection_id"] == projection_id
                and row.payload["entry"].get("cleared_at") is None
            )
            actionable_dirty = {
                item.output_ref.exact_key
                for item in pending
                if item.reason not in {
                    "temporal record ingested",
                    "temporal record superseded",
                }
            }
            if record_ref.exact_key in actionable_dirty:
                return False
            dependencies = self._dependency_states(
                selected=(record,),
                records=records,
                dirty_keys=actionable_dirty,
                stale_keys=stale_keys,
                direct_stale_keys=direct_stale_keys,
                current_keys={_record_ref(item).exact_key for item in current},
                valid_at=now,
                known_at=now,
            )
            if any(item.status != "current" for item in dependencies):
                return False
            # A unique cached row may corroborate adapter-owned invalidation
            # state after its dirty event is consumed, but never establishes
            # current time eligibility or resolves live competitors.
            states = tuple(
                state
                for state in self.current_state(projection_id)
                if (state.entity_authority, state.entity_id)
                == (record.entity_authority, record.entity_id)
            )
            if (
                len(states) == 1
                and states[0].record_digests == (record.record_digest,)
                and any(item.status != "current" for item in states[0].dependency_states)
            ):
                return False
            return True

    def rebuild(self, *, projection_id: str, valid_at: datetime, known_at: datetime, at: datetime) -> ProjectionCheckpoint:
        records = self._records()
        current = self._current_records(records, valid_at=valid_at, known_at=known_at)
        record_digests = tuple(sorted(item.record_digest for item in current))
        edges = self.store.list(self.tenant_id, self.project_id, kind="procedure_projection_edge")
        edge_digests = tuple(sorted(ProjectionEdge.model_validate(row.payload["edge"]).edge_digest for row in edges))
        # Rebuild is recovery-oriented; it may scan canonical history, while
        # normal updates use ``mark_dirty`` + ``recompute``.
        with self.store.transaction():
            entities = {(item.entity_authority, item.entity_id) for item in records}
            for entity in sorted(entities):
                entity_selected = tuple(item for item in current if (item.entity_authority, item.entity_id) == entity)
                active_invalidation_refs = self.active_invalidation_refs(valid_at=valid_at, known_at=known_at)
                direct_stale_keys = {ref.exact_key for ref in active_invalidation_refs}
                dependencies = self._dependency_states(selected=entity_selected, records=records, dirty_keys=set(), stale_keys=self._transitive_stale_keys(direct_stale_keys, active_invalidation_refs), direct_stale_keys=direct_stale_keys, current_keys={_record_ref(item).exact_key for item in current}, valid_at=valid_at, known_at=known_at)
                self._write_current_locked(projection_id=projection_id, entity=entity, selected=entity_selected, inputs=tuple(item for item in records if (item.entity_authority, item.entity_id) == entity), dependency_states=dependencies, valid_at=valid_at, known_at=known_at, at=at)
        state_digests = tuple(sorted(item.state_digest for item in self.current_state(projection_id)))
        input_record_digests = tuple(sorted(item.record_digest for item in records if (item.ingested_at or item.recorded_at) <= known_at))
        tombstone_digests = tuple(sorted(item.tombstone_digest for item in self._tombstones() if item.recorded_at <= known_at))
        body = {"projection_id": projection_id, "record_digests": record_digests, "edge_digests": edge_digests, "current_state_digests": state_digests, "input_record_digests": input_record_digests, "tombstone_digests": tombstone_digests, "valid_at": valid_at.isoformat(), "known_at": known_at.isoformat()}
        checkpoint = ProjectionCheckpoint(
            projection_id=projection_id, checkpoint_digest=_digest(body),
            record_digests=record_digests, edge_digests=edge_digests, current_state_digests=state_digests,
            input_record_digests=input_record_digests, tombstone_digests=tombstone_digests,
            revision=1, rebuilt_at=at,
        )
        with self.store.transaction():
            current_row = self.store.get(self.tenant_id, self.project_id, "procedure_projection_checkpoint", projection_id)
            if current_row is not None:
                existing = ProjectionCheckpoint.model_validate(current_row.payload["checkpoint"])
                if existing.checkpoint_digest == checkpoint.checkpoint_digest:
                    return existing
            revision = current_row.revision if current_row else 0
            checkpoint = checkpoint.model_copy(update={"revision": revision + 1})
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id, project_id=self.project_id,
                    record_kind="procedure_projection_checkpoint", record_id=projection_id,
                    revision=revision + 1, payload={"checkpoint": checkpoint.model_dump(mode="json")},
                    created_at=current_row.created_at if current_row else at, updated_at=at,
                ), expected_revision=revision,
            )
        return checkpoint


__all__ = [
    "ProjectionAlias", "ProjectionCheckpoint", "ProjectionCurrentState", "ProjectionDependencyState", "ProjectionDirtyEntry", "ProjectionDirtyEvent", "ProjectionEdge",
    "ProjectionInvalidationAuthorizer", "ProjectionRecomputeRun", "ProjectionTombstone",
    "TemporalProjectionService", "create_projection_tombstone",
]
