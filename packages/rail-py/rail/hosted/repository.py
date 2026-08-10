"""Optional hosted metadata repositories and the capture unit of work.

The domain service depends only on ``MetadataStore`` and
``ImmutableObjectStore``.  Postgres and object-store SDKs remain replaceable
adapters; the local JSON implementation has the same observable semantics.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock, local
from typing import Any, Protocol

from krail.provider.v1 import ResourceRef
from rail.hosted.models import (
    BackupBundle,
    CaptureRecord,
    HostedRecord,
    ProjectionRecord,
)
from rail.hosted.object_store import ImmutableObjectStore


class ConcurrencyConflict(RuntimeError): pass
class IdempotencyConflict(RuntimeError): pass
class IntegrityFailure(RuntimeError): pass


MAX_CAPTURE_BYTES = 64 * 1024 * 1024
MAX_BACKUP_RECORDS = 10_000
MAX_BACKUP_BYTES = 512 * 1024 * 1024


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _scope_digest(tenant_id: str, project_id: str) -> str:
    return hashlib.sha256(_canonical({"tenant_id": tenant_id, "project_id": project_id})).hexdigest()


def _object_key(tenant_id: str, project_id: str, digest: str) -> str:
    hexdigest = digest.removeprefix("sha256:")
    return f"scopes/{_scope_digest(tenant_id, project_id)}/captures/sha256/{hexdigest[:2]}/{hexdigest}"


class MetadataStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...
    def get(self, tenant_id: str, project_id: str, kind: str, record_id: str) -> HostedRecord | None: ...
    def put(self, record: HostedRecord, *, expected_revision: int) -> None: ...
    def restore(self, record: HostedRecord) -> None: ...
    def list(self, tenant_id: str, project_id: str, *, kind: str | None = None) -> list[HostedRecord]: ...


class MemoryMetadataStore:
    """Transactional deterministic fake used as the semantic reference."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str, str], HostedRecord] = {}
        self._lock = RLock()
        self._snapshot: dict[tuple[str, str, str, str], HostedRecord] | None = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._snapshot is not None:
                raise RuntimeError("nested metadata transactions are not supported")
            self._snapshot = copy.deepcopy(self._records)
            try:
                yield
            except Exception:
                self._records = self._snapshot
                raise
            finally:
                self._snapshot = None

    @staticmethod
    def _key(tenant_id: str, project_id: str, kind: str, record_id: str) -> tuple[str, str, str, str]:
        return tenant_id, project_id, kind, record_id

    def get(self, tenant_id: str, project_id: str, kind: str, record_id: str) -> HostedRecord | None:
        return self._records.get(self._key(tenant_id, project_id, kind, record_id))

    def put(self, record: HostedRecord, *, expected_revision: int) -> None:
        key = self._key(record.tenant_id, record.project_id, record.record_kind, record.record_id)
        current = self._records.get(key)
        actual = current.revision if current else 0
        if actual != expected_revision or record.revision != expected_revision + 1:
            raise ConcurrencyConflict(f"expected revision {expected_revision}, found {actual}")
        self._records[key] = record

    def restore(self, record: HostedRecord) -> None:
        key = self._key(record.tenant_id, record.project_id, record.record_kind, record.record_id)
        current = self._records.get(key)
        if current is not None and current != record:
            raise ConcurrencyConflict("restore would overwrite different metadata")
        self._records[key] = record

    def list(self, tenant_id: str, project_id: str, *, kind: str | None = None) -> list[HostedRecord]:
        rows = [r for r in self._records.values() if r.tenant_id == tenant_id and r.project_id == project_id and (kind is None or r.record_kind == kind)]
        return sorted(rows, key=lambda row: (row.record_kind, row.record_id))


class JsonMetadataStore(MemoryMetadataStore):
    """Atomic local metadata adapter for parity and offline operation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        super().__init__()
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != "krail.hosted-metadata.v1":
                raise ValueError("unsupported hosted metadata schema")
            for item in payload.get("records", []):
                record = HostedRecord.model_validate(item)
                self._records[self._key(record.tenant_id, record.project_id, record.record_kind, record.record_id)] = record

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with super().transaction():
            yield
            self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": "krail.hosted-metadata.v1", "records": [r.model_dump(mode="json") for r in self.list_all()]}
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def list_all(self) -> list[HostedRecord]:
        return sorted(self._records.values(), key=lambda row: (row.tenant_id, row.project_id, row.record_kind, row.record_id))


class PostgresMetadataStore:
    """DB-API adapter for the migration-managed ``krail_hosted_record`` table."""

    def __init__(self, dsn: str, *, connect: Callable[..., Any] | None = None) -> None:
        if connect is None:
            try:
                from psycopg import connect as psycopg_connect
            except ImportError as exc:
                raise RuntimeError("install krail[hosted] to use PostgresMetadataStore") from exc
            connect = psycopg_connect
        self.dsn = dsn
        self._connect = connect
        self._state = local()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if getattr(self._state, "connection", None) is not None:
            raise RuntimeError("nested metadata transactions are not supported")
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
            raise RuntimeError("Postgres metadata operations require transaction()")
        return connection

    @staticmethod
    def _decode(row: Any) -> HostedRecord | None:
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return HostedRecord.model_validate(payload)

    def get(self, tenant_id: str, project_id: str, kind: str, record_id: str) -> HostedRecord | None:
        with self._connection().cursor() as cursor:
            cursor.execute("SELECT record FROM krail_hosted_record WHERE tenant_id=%s AND project_id=%s AND record_kind=%s AND record_id=%s", (tenant_id, project_id, kind, record_id))
            return self._decode(cursor.fetchone())

    def put(self, record: HostedRecord, *, expected_revision: int) -> None:
        value = record.model_dump(mode="json")
        with self._connection().cursor() as cursor:
            if expected_revision == 0:
                cursor.execute("INSERT INTO krail_hosted_record (tenant_id,project_id,record_kind,record_id,revision,record) VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING", (record.tenant_id, record.project_id, record.record_kind, record.record_id, record.revision, json.dumps(value)))
            else:
                cursor.execute("UPDATE krail_hosted_record SET revision=%s, record=%s::jsonb, updated_at=now() WHERE tenant_id=%s AND project_id=%s AND record_kind=%s AND record_id=%s AND revision=%s", (record.revision, json.dumps(value), record.tenant_id, record.project_id, record.record_kind, record.record_id, expected_revision))
            if cursor.rowcount != 1:
                raise ConcurrencyConflict(f"expected revision {expected_revision} was stale")

    def restore(self, record: HostedRecord) -> None:
        value = record.model_dump(mode="json")
        with self._connection().cursor() as cursor:
            cursor.execute("INSERT INTO krail_hosted_record (tenant_id,project_id,record_kind,record_id,revision,record) VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING", (record.tenant_id, record.project_id, record.record_kind, record.record_id, record.revision, json.dumps(value)))
            if cursor.rowcount == 0:
                current = self.get(record.tenant_id, record.project_id, record.record_kind, record.record_id)
                if current != record:
                    raise ConcurrencyConflict("restore would overwrite different metadata")

    def list(self, tenant_id: str, project_id: str, *, kind: str | None = None) -> list[HostedRecord]:
        sql = "SELECT record FROM krail_hosted_record WHERE tenant_id=%s AND project_id=%s"
        params: tuple[Any, ...] = (tenant_id, project_id)
        if kind is not None:
            sql += " AND record_kind=%s"
            params += (kind,)
        sql += " ORDER BY record_kind,record_id"
        with self._connection().cursor() as cursor:
            cursor.execute(sql, params)
            return [self._decode(row) for row in cursor.fetchall()]


class HostedRepository:
    """Tenant/project-scoped unit of work for immutable captures."""

    def __init__(self, metadata: MetadataStore, objects: ImmutableObjectStore, *, tenant_id: str, project_id: str) -> None:
        if not tenant_id.strip() or not project_id.strip():
            raise ValueError("tenant_id and project_id are required")
        self.metadata, self.objects = metadata, objects
        self.tenant_id, self.project_id = tenant_id, project_id

    def _resource_ref(self, capture_id: str, content_digest: str) -> ResourceRef:
        authority = f"krail+hosted://capture-authority/{_scope_digest(self.tenant_id, self.project_id)}"
        return ResourceRef(authority=authority, resource_type="capture", resource_id=capture_id, version=f"content:{content_digest.removeprefix('sha256:')}", digest=content_digest)

    @staticmethod
    def _revision_record_id(capture_id: str, revision: int) -> str:
        identity = hashlib.sha256(capture_id.encode("utf-8")).hexdigest()
        return f"{identity}:{revision:020d}"

    def capture(self, capture_id: str, content: bytes, *, media_type: str, created_at: datetime, retention_until: datetime | None = None, expected_revision: int = 0, idempotency_key: str, source_id: str = "default", classification: str = "internal") -> CaptureRecord:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if len(content) > MAX_CAPTURE_BYTES:
            raise ValueError(f"capture exceeds {MAX_CAPTURE_BYTES} bytes")
        content_digest = _digest(content)
        command_digest = _digest(_canonical({"capture_id": capture_id, "content_digest": content_digest, "media_type": media_type, "retention_until": retention_until.isoformat() if retention_until else None, "expected_revision": expected_revision, "source_id": source_id, "classification": classification}))
        object_key = _object_key(self.tenant_id, self.project_id, content_digest)
        self.objects.put_if_absent(object_key, content)
        with self.metadata.transaction():
            prior = self.metadata.get(self.tenant_id, self.project_id, "idempotency", idempotency_key)
            if prior:
                if prior.payload["command_digest"] != command_digest:
                    raise IdempotencyConflict("idempotency key was used for a different command")
                stored = self.metadata.get(self.tenant_id, self.project_id, "capture", prior.payload["capture_id"])
                if stored is None:
                    raise IntegrityFailure("idempotency record references a missing capture")
                return CaptureRecord.model_validate(stored.payload)
            current = self.metadata.get(self.tenant_id, self.project_id, "capture", capture_id)
            actual = current.revision if current else 0
            if actual != expected_revision:
                raise ConcurrencyConflict(f"expected revision {expected_revision}, found {actual}")
            capture = CaptureRecord(tenant_id=self.tenant_id, project_id=self.project_id, capture_id=capture_id, source_id=source_id, classification=classification, resource_ref=self._resource_ref(capture_id, content_digest), revision=actual + 1, content_digest=content_digest, object_key=object_key, media_type=media_type, byte_size=len(content), created_at=created_at, retention_until=retention_until)
            row = HostedRecord(tenant_id=self.tenant_id, project_id=self.project_id, record_kind="capture", record_id=capture_id, revision=capture.revision, payload=capture.model_dump(mode="json"), created_at=current.created_at if current else created_at, updated_at=created_at)
            self.metadata.put(row, expected_revision=actual)
            revision_row = HostedRecord(tenant_id=self.tenant_id, project_id=self.project_id, record_kind="capture_revision", record_id=self._revision_record_id(capture_id, capture.revision), revision=1, payload=capture.model_dump(mode="json"), created_at=created_at, updated_at=created_at)
            self.metadata.put(revision_row, expected_revision=0)
            idem = HostedRecord(tenant_id=self.tenant_id, project_id=self.project_id, record_kind="idempotency", record_id=idempotency_key, revision=1, payload={"command_digest": command_digest, "capture_id": capture_id}, created_at=created_at, updated_at=created_at)
            self.metadata.put(idem, expected_revision=0)
            return capture

    def read_capture(self, capture_id: str) -> tuple[CaptureRecord, bytes]:
        capture = self.capture_metadata(capture_id)
        return self.read_capture_record(capture)

    def read_capture_record(
        self, capture: CaptureRecord
    ) -> tuple[CaptureRecord, bytes]:
        """Read bytes for an exact, previously resolved capture revision.

        Callers that authorize from capture metadata must use this method so a
        concurrent update cannot replace the authorized revision between the
        policy decision and the object read.
        """
        if (
            capture.tenant_id != self.tenant_id
            or capture.project_id != self.project_id
        ):
            raise IntegrityFailure("capture metadata belongs to a different scope")
        content = self.objects.get(capture.object_key)
        if len(content) != capture.byte_size or _digest(content) != capture.content_digest:
            raise IntegrityFailure("capture object does not match immutable metadata")
        return capture, content

    def capture_metadata(self, capture_id: str) -> CaptureRecord:
        """Read exact metadata without fetching object bytes for policy preflight."""
        with self.metadata.transaction():
            row = self.metadata.get(self.tenant_id, self.project_id, "capture", capture_id)
        if row is None:
            raise KeyError(capture_id)
        if row.payload.get("state") == "erased":
            raise KeyError(capture_id)
        return CaptureRecord.model_validate(row.payload)

    def capture_records(self) -> list[CaptureRecord]:
        """Return current active metadata in deterministic order, never object bytes."""
        with self.metadata.transaction():
            rows = self.metadata.list(self.tenant_id, self.project_id, kind="capture")
        return [
            CaptureRecord.model_validate(row.payload)
            for row in rows
            if row.payload.get("state", "active") == "active"
        ]

    def rebuild_projection(self, projection_id: str, builder: Callable[[list[CaptureRecord]], dict[str, Any]], *, rebuilt_at: datetime, projection_kind: str = "search") -> ProjectionRecord:
        with self.metadata.transaction():
            rows = self.metadata.list(self.tenant_id, self.project_id, kind="capture")
            captures = [CaptureRecord.model_validate(row.payload) for row in rows if row.payload.get("state", "active") == "active"]
            source_digest = _digest(_canonical([capture.model_dump(mode="json") for capture in captures]))
            value = builder(captures)
            projection_digest = _digest(_canonical(value))
            current = self.metadata.get(self.tenant_id, self.project_id, "projection", projection_id)
            revision = (current.revision if current else 0) + 1
            projection = ProjectionRecord(tenant_id=self.tenant_id, project_id=self.project_id, projection_id=projection_id, projection_kind=projection_kind, revision=revision, source_digest=source_digest, projection_digest=projection_digest, value=value, rebuilt_at=rebuilt_at)
            row = HostedRecord(tenant_id=self.tenant_id, project_id=self.project_id, record_kind="projection", record_id=projection_id, revision=revision, payload=projection.model_dump(mode="json"), created_at=current.created_at if current else rebuilt_at, updated_at=rebuilt_at)
            self.metadata.put(row, expected_revision=revision - 1)
            return projection

    def erase(self, capture_id: str, *, erased_at: datetime, reason: str, expected_revision: int) -> None:
        with self.metadata.transaction():
            current = self.metadata.get(self.tenant_id, self.project_id, "capture", capture_id)
            if current is None:
                return
            capture = CaptureRecord.model_validate(current.payload)
            if capture.state == "erased":
                if expected_revision in {current.revision, current.revision - 1}:
                    return
                raise ConcurrencyConflict(f"expected revision {expected_revision}, found erased revision {current.revision}")
            if current.revision != expected_revision:
                raise ConcurrencyConflict(f"expected revision {expected_revision}, found {current.revision}")
            revision_rows = [row for row in self.metadata.list(self.tenant_id, self.project_id, kind="capture_revision") if row.payload.get("capture_id") == capture_id and row.payload.get("state", "active") != "erased"]
            object_keys = {capture.object_key} | {row.payload["object_key"] for row in revision_rows}
            tombstone_capture = CaptureRecord(tenant_id=self.tenant_id, project_id=self.project_id, capture_id=capture_id, source_id=None, classification=None, resource_ref=None, revision=current.revision + 1, content_digest=None, object_key=None, media_type=None, byte_size=None, created_at=capture.created_at, retention_until=None, state="erased", erased_at=erased_at, erasure_reason_digest=_digest(reason.encode()))
            tombstone = HostedRecord(tenant_id=self.tenant_id, project_id=self.project_id, record_kind="capture", record_id=capture_id, revision=current.revision + 1, payload=tombstone_capture.model_dump(mode="json"), created_at=current.created_at, updated_at=erased_at)
            self.metadata.put(tombstone, expected_revision=current.revision)
            for revision_row in revision_rows:
                revision_capture = CaptureRecord.model_validate(revision_row.payload)
                erased_revision = CaptureRecord(tenant_id=self.tenant_id, project_id=self.project_id, capture_id=capture_id, source_id=None, classification=None, resource_ref=None, revision=revision_capture.revision, content_digest=None, object_key=None, media_type=None, byte_size=None, created_at=revision_capture.created_at, retention_until=None, state="erased", erased_at=erased_at, erasure_reason_digest=_digest(reason.encode()))
                row = HostedRecord(tenant_id=self.tenant_id, project_id=self.project_id, record_kind="capture_revision", record_id=revision_row.record_id, revision=revision_row.revision + 1, payload=erased_revision.model_dump(mode="json"), created_at=revision_row.created_at, updated_at=erased_at)
                self.metadata.put(row, expected_revision=revision_row.revision)
            active_revision_rows = [row for row in self.metadata.list(self.tenant_id, self.project_id, kind="capture_revision") if row.payload.get("state", "active") != "erased"]
            referenced_keys = {row.payload.get("object_key") for row in active_revision_rows}
            active_current_rows = [row for row in self.metadata.list(self.tenant_id, self.project_id, kind="capture") if row.record_id != capture_id and row.payload.get("state", "active") != "erased"]
            referenced_keys.update(row.payload.get("object_key") for row in active_current_rows)
        for object_key in object_keys - referenced_keys:
            self.objects.delete(object_key)

    def enforce_retention(self, *, as_of: datetime) -> list[str]:
        with self.metadata.transaction():
            rows = self.metadata.list(self.tenant_id, self.project_id, kind="capture")
        expired = [CaptureRecord.model_validate(row.payload) for row in rows if row.payload.get("state", "active") == "active" and row.payload.get("retention_until") is not None and CaptureRecord.model_validate(row.payload).retention_until <= as_of]
        erased: list[str] = []
        for capture in expired:
            self.erase(capture.capture_id, erased_at=as_of, reason="retention-expired", expected_revision=capture.revision)
            erased.append(capture.capture_id)
        return erased

    def backup(self, *, created_at: datetime) -> BackupBundle:
        with self.metadata.transaction():
            records = [row for row in self.metadata.list(self.tenant_id, self.project_id) if row.record_kind in {"capture", "capture_revision", "idempotency"}]
        if len(records) > MAX_BACKUP_RECORDS:
            raise ValueError(f"backup exceeds {MAX_BACKUP_RECORDS} records")
        objects: dict[str, str] = {}
        total_bytes = 0
        for row in records:
            key = row.payload.get("object_key")
            if key:
                value = self.objects.get(key)
                expected_digest = row.payload.get("content_digest")
                expected_size = row.payload.get("byte_size")
                if (
                    not isinstance(expected_digest, str)
                    or _object_key(self.tenant_id, self.project_id, expected_digest) != key
                    or _digest(value) != expected_digest
                    or len(value) != expected_size
                ):
                    raise IntegrityFailure("backup source object does not match immutable metadata")
                total_bytes += len(value)
                if total_bytes > MAX_BACKUP_BYTES:
                    raise ValueError(f"backup exceeds {MAX_BACKUP_BYTES} object bytes")
                objects[key] = base64.b64encode(value).decode("ascii")
        created_at_json = created_at.isoformat().replace("+00:00", "Z")
        body = {"schema_version": "krail.hosted-backup.v1", "tenant_id": self.tenant_id, "project_id": self.project_id, "created_at": created_at_json, "records": [row.model_dump(mode="json") for row in records], "objects": objects}
        return BackupBundle(**body, bundle_digest=_digest(_canonical(body)))

    def restore(self, bundle: BackupBundle) -> None:
        body = bundle.model_dump(mode="json", exclude={"bundle_digest"})
        if _digest(_canonical(body)) != bundle.bundle_digest:
            raise IntegrityFailure("backup bundle digest mismatch")
        if (bundle.tenant_id, bundle.project_id) != (self.tenant_id, self.project_id):
            raise ValueError("backup authority does not match repository scope")
        if len(bundle.records) > MAX_BACKUP_RECORDS:
            raise ValueError(f"backup exceeds {MAX_BACKUP_RECORDS} records")
        referenced_keys: set[str] = set()
        for row in bundle.records:
            if (row.tenant_id, row.project_id) != (self.tenant_id, self.project_id):
                raise IntegrityFailure("backup record escapes repository scope")
            payload_scope = (row.payload.get("tenant_id"), row.payload.get("project_id"))
            if any(value is not None for value in payload_scope) and payload_scope != (
                self.tenant_id,
                self.project_id,
            ):
                raise IntegrityFailure("backup payload escapes repository scope")
            if row.record_kind in {"capture", "capture_revision"}:
                capture = CaptureRecord.model_validate(row.payload)
                if capture.object_key is not None:
                    referenced_keys.add(capture.object_key)
        if set(bundle.objects) != referenced_keys:
            raise IntegrityFailure("backup object inventory does not match durable references")
        total_bytes = 0
        decoded_objects: dict[str, bytes] = {}
        for key, encoded in bundle.objects.items():
            try:
                value = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise IntegrityFailure("backup object encoding is invalid") from exc
            total_bytes += len(value)
            if total_bytes > MAX_BACKUP_BYTES:
                raise ValueError(f"backup exceeds {MAX_BACKUP_BYTES} object bytes")
            if _object_key(self.tenant_id, self.project_id, _digest(value)) != key:
                raise IntegrityFailure("backup object key does not match content")
            decoded_objects[key] = value
        for row in bundle.records:
            if row.record_kind in {"capture", "capture_revision"}:
                capture = CaptureRecord.model_validate(row.payload)
                if capture.object_key is not None:
                    value = decoded_objects[capture.object_key]
                    if _digest(value) != capture.content_digest or len(value) != capture.byte_size:
                        raise IntegrityFailure("backup object does not match capture metadata")
        for key, value in decoded_objects.items():
            self.objects.put_if_absent(key, value)
        with self.metadata.transaction():
            for row in bundle.records:
                current = self.metadata.get(self.tenant_id, self.project_id, row.record_kind, row.record_id)
                if current is None:
                    self.metadata.restore(row)
                elif current != row:
                    raise ConcurrencyConflict("restore would overwrite different metadata")
