"""Local and optional Postgres persistence for semantic records."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock, local
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from rail.hosted.repository import ConcurrencyConflict
from rail.semantic.models import (
    Alias,
    Conflict,
    Entity,
    EntityMerge,
    EvidenceProvenance,
    Fact,
    SemanticType,
)

SemanticKind = Literal[
    "type",
    "entity",
    "fact",
    "alias",
    "conflict",
    "entity_merge",
    "semantic_pack",
    "pack_evaluation",
    "ontology_package",
    "ontology_package_version",
    "ontology_change_set",
]


class SemanticRow(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}
    tenant_id: str
    project_id: str
    record_kind: SemanticKind
    record_id: str
    revision: int
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SemanticStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...
    def get(
        self, tenant_id: str, project_id: str, kind: SemanticKind, record_id: str
    ) -> SemanticRow | None: ...
    def put(self, row: SemanticRow, *, expected_revision: int) -> None: ...
    def list(
        self, tenant_id: str, project_id: str, *, kind: SemanticKind | None = None
    ) -> list[SemanticRow]: ...


class MemorySemanticStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], SemanticRow] = {}
        self._lock = RLock()
        self._snapshot: dict[tuple[str, str, str, str], SemanticRow] | None = None

    @staticmethod
    def _key(
        tenant_id: str, project_id: str, kind: SemanticKind, record_id: str
    ) -> tuple[str, str, str, str]:
        return tenant_id, project_id, kind, record_id

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._snapshot is not None:
                raise RuntimeError("nested semantic transactions are not supported")
            self._snapshot = copy.deepcopy(self._rows)
            try:
                yield
            except Exception:
                self._rows = self._snapshot
                raise
            finally:
                self._snapshot = None

    def get(
        self, tenant_id: str, project_id: str, kind: SemanticKind, record_id: str
    ) -> SemanticRow | None:
        return self._rows.get(self._key(tenant_id, project_id, kind, record_id))

    def put(self, row: SemanticRow, *, expected_revision: int) -> None:
        key = self._key(row.tenant_id, row.project_id, row.record_kind, row.record_id)
        current = self._rows.get(key)
        actual = current.revision if current else 0
        if actual != expected_revision or row.revision != expected_revision + 1:
            raise ConcurrencyConflict(
                f"expected revision {expected_revision}, found {actual}"
            )
        self._rows[key] = row

    def list(
        self, tenant_id: str, project_id: str, *, kind: SemanticKind | None = None
    ) -> list[SemanticRow]:
        return sorted(
            (
                row
                for row in self._rows.values()
                if row.tenant_id == tenant_id
                and row.project_id == project_id
                and (kind is None or row.record_kind == kind)
            ),
            key=lambda row: (row.record_kind, row.record_id),
        )


class JsonSemanticStore(MemorySemanticStore):
    """Atomic local semantic store with the same CAS semantics as Postgres."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        super().__init__()
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("schema_version") != "krail.semantic-store.v1":
                raise ValueError("unsupported semantic store schema")
            for item in value.get("records", []):
                row = SemanticRow.model_validate(item)
                self._rows[
                    self._key(
                        row.tenant_id, row.project_id, row.record_kind, row.record_id
                    )
                ] = row

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with super().transaction():
            yield
            self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "schema_version": "krail.semantic-store.v1",
            "records": [
                row.model_dump(mode="json")
                for row in sorted(
                    self._rows.values(),
                    key=lambda row: (
                        row.tenant_id,
                        row.project_id,
                        row.record_kind,
                        row.record_id,
                    ),
                )
            ],
        }
        fd, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    value,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)


class PostgresSemanticStore:
    """Optional DB-API adapter for migration-managed semantic records."""

    def __init__(self, dsn: str, *, connect: Callable[..., Any] | None = None) -> None:
        if connect is None:
            try:
                from psycopg import connect as psycopg_connect
            except ImportError as exc:
                raise RuntimeError(
                    "install krail[hosted] to use PostgresSemanticStore"
                ) from exc
            connect = psycopg_connect
        self.dsn = dsn
        self._connect = connect
        self._state = local()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if getattr(self._state, "connection", None) is not None:
            raise RuntimeError("nested semantic transactions are not supported")
        with self._connect(self.dsn) as connection:
            self._state.connection = connection
            try:
                yield
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                self._state.connection = None

    def _connection(self):
        connection = getattr(self._state, "connection", None)
        if connection is None:
            raise RuntimeError("Postgres semantic operations require transaction()")
        return connection

    @staticmethod
    def _decode(value: Any) -> SemanticRow | None:
        if value is None:
            return None
        payload = value[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return SemanticRow.model_validate(payload)

    def get(
        self, tenant_id: str, project_id: str, kind: SemanticKind, record_id: str
    ) -> SemanticRow | None:
        with self._connection().cursor() as cursor:
            cursor.execute(
                "SELECT record FROM krail_semantic_record WHERE tenant_id=%s "
                "AND project_id=%s AND record_kind=%s AND record_id=%s",
                (tenant_id, project_id, kind, record_id),
            )
            return self._decode(cursor.fetchone())

    def put(self, row: SemanticRow, *, expected_revision: int) -> None:
        value = json.dumps(row.model_dump(mode="json"))
        with self._connection().cursor() as cursor:
            if expected_revision == 0:
                cursor.execute(
                    "INSERT INTO krail_semantic_record "
                    "(tenant_id,project_id,record_kind,record_id,revision,record) "
                    "VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                    (
                        row.tenant_id,
                        row.project_id,
                        row.record_kind,
                        row.record_id,
                        row.revision,
                        value,
                    ),
                )
            else:
                cursor.execute(
                    "UPDATE krail_semantic_record SET revision=%s,record=%s::jsonb,"
                    "updated_at=now() WHERE tenant_id=%s AND project_id=%s "
                    "AND record_kind=%s AND record_id=%s AND revision=%s",
                    (
                        row.revision,
                        value,
                        row.tenant_id,
                        row.project_id,
                        row.record_kind,
                        row.record_id,
                        expected_revision,
                    ),
                )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict(
                    f"expected revision {expected_revision} was stale"
                )

    def list(
        self, tenant_id: str, project_id: str, *, kind: SemanticKind | None = None
    ) -> list[SemanticRow]:
        sql = (
            "SELECT record FROM krail_semantic_record "
            "WHERE tenant_id=%s AND project_id=%s"
        )
        params: tuple[Any, ...] = (tenant_id, project_id)
        if kind is not None:
            sql += " AND record_kind=%s"
            params += (kind,)
        sql += " ORDER BY record_kind,record_id"
        with self._connection().cursor() as cursor:
            cursor.execute(sql, params)
            return [self._decode(row) for row in cursor.fetchall()]


MODEL_BY_KIND: dict[SemanticKind, type[BaseModel]] = {
    "type": SemanticType,
    "entity": Entity,
    "fact": Fact,
    "alias": Alias,
    "conflict": Conflict,
    "entity_merge": EntityMerge,
}


class SemanticRepository:
    def __init__(
        self, store: SemanticStore, *, tenant_id: str, project_id: str
    ) -> None:
        if not tenant_id.strip() or not project_id.strip():
            raise ValueError("tenant_id and project_id are required")
        self.store = store
        self.tenant_id = tenant_id
        self.project_id = project_id

    def _save(
        self,
        kind: SemanticKind,
        record_id: str,
        value: BaseModel,
        *,
        expected_revision: int,
        at: datetime,
    ) -> BaseModel:
        self._save_many(((kind, record_id, value, expected_revision),), at=at)
        return value

    def _save_many(
        self,
        items: tuple[tuple[SemanticKind, str, BaseModel, int], ...],
        *,
        at: datetime,
    ) -> None:
        """Atomically persist a bounded set of exact semantic revisions."""
        with self.store.transaction():
            for kind, record_id, value, expected_revision in items:
                if (
                    getattr(value, "tenant_id", self.tenant_id) != self.tenant_id
                    or getattr(value, "project_id", self.project_id)
                    != self.project_id
                ):
                    raise ValueError("semantic record belongs to a different scope")
                revision = getattr(value, "revision", expected_revision + 1)
                if revision != expected_revision + 1:
                    raise ConcurrencyConflict(
                        "semantic record revision does not advance CAS"
                    )
                current = self.store.get(
                    self.tenant_id, self.project_id, kind, record_id
                )
                row = SemanticRow(
                    tenant_id=self.tenant_id,
                    project_id=self.project_id,
                    record_kind=kind,
                    record_id=record_id,
                    revision=revision,
                    payload=value.model_dump(mode="json"),
                    created_at=current.created_at if current else at,
                    updated_at=at,
                )
                self.store.put(row, expected_revision=expected_revision)

    def put_type(
        self, value: SemanticType, *, expected_revision: int, at: datetime
    ) -> SemanticType:
        return self._save(
            "type", value.type_id, value, expected_revision=expected_revision, at=at
        )

    def put_entity(
        self, value: Entity, *, expected_revision: int, at: datetime
    ) -> Entity:
        return self._save(
            "entity",
            value.entity_id,
            value,
            expected_revision=expected_revision,
            at=at,
        )

    def put_fact(
        self, value: Fact, *, expected_revision: int, at: datetime
    ) -> Fact:
        with self.store.transaction():
            if self.store.get(
                self.tenant_id, self.project_id, "entity", value.subject_entity_id
            ) is None:
                raise ValueError("fact subject entity does not exist")
            if value.object.entity_id and self.store.get(
                self.tenant_id, self.project_id, "entity", value.object.entity_id
            ) is None:
                raise ValueError("fact object entity does not exist")
        return self._save(
            "fact", value.fact_id, value, expected_revision=expected_revision, at=at
        )

    def put_alias(
        self, value: Alias, *, expected_revision: int, at: datetime
    ) -> Alias:
        with self.store.transaction():
            if self.store.get(
                self.tenant_id, self.project_id, "entity", value.entity_id
            ) is None:
                raise ValueError("alias entity does not exist")
        return self._save(
            "alias", value.alias_id, value, expected_revision=expected_revision, at=at
        )

    def put_conflict(
        self, value: Conflict, *, expected_revision: int, at: datetime
    ) -> Conflict:
        with self.store.transaction():
            for fact_id in value.fact_ids:
                if self.store.get(
                    self.tenant_id, self.project_id, "fact", fact_id
                ) is None:
                    raise ValueError("conflict fact does not exist")
        return self._save(
            "conflict",
            value.conflict_id,
            value,
            expected_revision=expected_revision,
            at=at,
        )

    def get(self, kind: SemanticKind, record_id: str) -> BaseModel:
        with self.store.transaction():
            row = self.store.get(self.tenant_id, self.project_id, kind, record_id)
        if row is None:
            raise KeyError(record_id)
        model = MODEL_BY_KIND.get(kind)
        if model is None:
            raise ValueError(f"record kind {kind} requires its owning service")
        return model.model_validate(row.payload)

    def merge_entities(
        self,
        *,
        merge_id: str,
        source_entity_id: str,
        target_entity_id: str,
        provenance: EvidenceProvenance,
        applied_at: datetime,
    ) -> EntityMerge:
        with self.store.transaction():
            source_row = self.store.get(
                self.tenant_id, self.project_id, "entity", source_entity_id
            )
            target_row = self.store.get(
                self.tenant_id, self.project_id, "entity", target_entity_id
            )
            if source_row is None or target_row is None:
                raise KeyError("merge entities must exist")
            source, target = (
                Entity.model_validate(source_row.payload),
                Entity.model_validate(target_row.payload),
            )
            if source.state != "active" or target.state != "active":
                raise ConcurrencyConflict("merge requires active entities")
            merged = source.model_copy(
                update={
                    "state": "merged",
                    "merged_into": target.entity_id,
                    "revision": source.revision + 1,
                }
            )
            event = EntityMerge(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                merge_id=merge_id,
                source_entity_id=source.entity_id,
                target_entity_id=target.entity_id,
                source_revision=source.revision,
                target_revision=target.revision,
                provenance=provenance,
                applied_at=applied_at,
                revision=1,
            )
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id,
                    project_id=self.project_id,
                    record_kind="entity",
                    record_id=source.entity_id,
                    revision=merged.revision,
                    payload=merged.model_dump(mode="json"),
                    created_at=source_row.created_at,
                    updated_at=applied_at,
                ),
                expected_revision=source.revision,
            )
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id,
                    project_id=self.project_id,
                    record_kind="entity_merge",
                    record_id=merge_id,
                    revision=1,
                    payload=event.model_dump(mode="json"),
                    created_at=applied_at,
                    updated_at=applied_at,
                ),
                expected_revision=0,
            )
        return event

    def split_merge(self, merge_id: str, *, reversed_at: datetime) -> EntityMerge:
        with self.store.transaction():
            merge_row = self.store.get(
                self.tenant_id, self.project_id, "entity_merge", merge_id
            )
            if merge_row is None:
                raise KeyError(merge_id)
            merge = EntityMerge.model_validate(merge_row.payload)
            if merge.state == "reversed":
                return merge
            source_row = self.store.get(
                self.tenant_id, self.project_id, "entity", merge.source_entity_id
            )
            if source_row is None:
                raise KeyError(merge.source_entity_id)
            source = Entity.model_validate(source_row.payload)
            if source.state != "merged" or source.merged_into != merge.target_entity_id:
                raise ConcurrencyConflict("merge source no longer matches merge event")
            restored = source.model_copy(
                update={
                    "state": "active",
                    "merged_into": None,
                    "revision": source.revision + 1,
                }
            )
            reversed_merge = merge.model_copy(
                update={
                    "state": "reversed",
                    "reversed_at": reversed_at,
                    "revision": merge.revision + 1,
                }
            )
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id,
                    project_id=self.project_id,
                    record_kind="entity",
                    record_id=source.entity_id,
                    revision=restored.revision,
                    payload=restored.model_dump(mode="json"),
                    created_at=source_row.created_at,
                    updated_at=reversed_at,
                ),
                expected_revision=source.revision,
            )
            self.store.put(
                SemanticRow(
                    tenant_id=self.tenant_id,
                    project_id=self.project_id,
                    record_kind="entity_merge",
                    record_id=merge_id,
                    revision=reversed_merge.revision,
                    payload=reversed_merge.model_dump(mode="json"),
                    created_at=merge_row.created_at,
                    updated_at=reversed_at,
                ),
                expected_revision=merge.revision,
            )
        return reversed_merge
