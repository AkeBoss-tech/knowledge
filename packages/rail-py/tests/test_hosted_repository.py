from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import rail.hosted.repository as hosted_repository

from rail.hosted import (
    ConcurrencyConflict,
    FileObjectStore,
    HostedRepository,
    IdempotencyConflict,
    IntegrityFailure,
    JsonMetadataStore,
    MemoryMetadataStore,
    MemoryObjectStore,
    PostgresMetadataStore,
    migrations,
)
from rail.hosted.models import HostedRecord


NOW = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)


def repository(metadata=None, objects=None, *, tenant="tenant-a", project="project-a"):
    return HostedRepository(metadata or MemoryMetadataStore(), objects or MemoryObjectStore(), tenant_id=tenant, project_id=project)


def test_capture_is_immutable_idempotent_and_optimistically_versioned():
    repo = repository()
    first = repo.capture("source-1", b"one", media_type="text/plain", created_at=NOW, idempotency_key="request-1")
    assert repo.capture("source-1", b"one", media_type="text/plain", created_at=NOW, idempotency_key="request-1") == first
    assert repo.read_capture("source-1") == (first, b"one")
    with pytest.raises(IdempotencyConflict):
        repo.capture("source-1", b"two", media_type="text/plain", created_at=NOW, idempotency_key="request-1")
    with pytest.raises(ConcurrencyConflict):
        repo.capture("source-1", b"two", media_type="text/plain", created_at=NOW, expected_revision=0, idempotency_key="request-2")
    second = repo.capture("source-1", b"two", media_type="text/plain", created_at=NOW, expected_revision=1, idempotency_key="request-2")
    assert second.revision == 2
    assert second.resource_ref.authority.startswith("krail+hosted://capture-authority/")
    assert second.resource_ref.resource_id == "source-1"
    assert second.resource_ref.digest == second.content_digest


def test_capture_size_is_bounded_before_object_publication(monkeypatch):
    objects = MemoryObjectStore()
    repo = repository(objects=objects)
    monkeypatch.setattr(hosted_repository, "MAX_CAPTURE_BYTES", 3)
    with pytest.raises(ValueError, match="capture exceeds"):
        repo.capture("large", b"four", media_type="text/plain", created_at=NOW, idempotency_key="large")
    assert objects.objects == {}


def test_tenant_and_project_are_hard_metadata_partitions():
    metadata, objects = MemoryMetadataStore(), MemoryObjectStore()
    a = repository(metadata, objects)
    b = repository(metadata, objects, tenant="tenant-b")
    a.capture("same", b"a", media_type="text/plain", created_at=NOW, idempotency_key="same")
    b.capture("same", b"b", media_type="text/plain", created_at=NOW, idempotency_key="same")
    assert a.read_capture("same")[1] == b"a"
    assert b.read_capture("same")[1] == b"b"
    assert a.read_capture("same")[0].object_key != b.read_capture("same")[0].object_key


def test_projection_is_rebuildable_and_not_in_backup():
    repo = repository()
    repo.capture("a", b"a", media_type="text/plain", created_at=NOW, idempotency_key="a")
    first = repo.rebuild_projection("counts", lambda rows: {"count": len(rows)}, rebuilt_at=NOW)
    second = repo.rebuild_projection("counts", lambda rows: {"count": len(rows)}, rebuilt_at=NOW + timedelta(seconds=1))
    assert first.projection_digest == second.projection_digest
    assert second.revision == 2
    assert all(row.record_kind != "projection" for row in repo.backup(created_at=NOW).records)


def test_backup_restore_verifies_bundle_and_objects():
    source = repository()
    source.capture("a", b"hello", media_type="text/plain", created_at=NOW, idempotency_key="a")
    source.capture("a", b"updated", media_type="text/plain", created_at=NOW, expected_revision=1, idempotency_key="b")
    bundle = source.backup(created_at=NOW)
    target = repository()
    target.restore(bundle)
    assert target.read_capture("a")[1] == b"updated"
    target.restore(bundle)
    tampered = bundle.model_copy(update={"objects": {next(iter(bundle.objects)): "aGVsbG8="}})
    with pytest.raises(IntegrityFailure):
        repository().restore(tampered)


def test_capture_read_rejects_corrupted_object():
    objects = MemoryObjectStore()
    repo = repository(objects=objects)
    capture = repo.capture("a", b"original", media_type="text/plain", created_at=NOW, idempotency_key="a")
    objects.objects[capture.object_key] = b"corrupt"
    with pytest.raises(IntegrityFailure):
        repo.read_capture("a")


def test_retention_erases_bytes_but_preserves_non_sensitive_tombstone():
    objects = MemoryObjectStore()
    repo = repository(objects=objects)
    capture = repo.capture("expired", b"private", media_type="text/plain", created_at=NOW, retention_until=NOW + timedelta(days=1), idempotency_key="expired")
    assert repo.enforce_retention(as_of=NOW) == []
    assert repo.enforce_retention(as_of=NOW + timedelta(days=2)) == ["expired"]
    assert not objects.contains(capture.object_key)
    with pytest.raises(KeyError):
        repo.read_capture("expired")


def test_shared_content_is_deleted_only_after_last_reference_is_erased():
    objects = MemoryObjectStore()
    repo = repository(objects=objects)
    one = repo.capture("one", b"shared", media_type="text/plain", created_at=NOW, idempotency_key="one")
    two = repo.capture("two", b"shared", media_type="text/plain", created_at=NOW, idempotency_key="two")
    repo.erase("one", erased_at=NOW, reason="request", expected_revision=1)
    assert objects.contains(one.object_key)
    repo.erase("two", erased_at=NOW, reason="request", expected_revision=1)
    assert not objects.contains(two.object_key)


def test_update_then_erase_deletes_every_historical_object_revision():
    objects = MemoryObjectStore()
    repo = repository(objects=objects)
    first = repo.capture("capture", b"v1", media_type="text/plain", created_at=NOW, idempotency_key="v1")
    second = repo.capture("capture", b"v2", media_type="text/plain", created_at=NOW, expected_revision=1, idempotency_key="v2")
    assert objects.contains(first.object_key) and objects.contains(second.object_key)
    repo.erase("capture", erased_at=NOW, reason="request", expected_revision=2)
    assert not objects.contains(first.object_key)
    assert not objects.contains(second.object_key)
    repo.erase("capture", erased_at=NOW, reason="request", expected_revision=2)
    with pytest.raises(ConcurrencyConflict):
        repo.erase("capture", erased_at=NOW, reason="stale", expected_revision=1)


def test_historical_shared_object_survives_until_last_capture_is_erased():
    objects = MemoryObjectStore()
    repo = repository(objects=objects)
    shared = repo.capture("one", b"shared", media_type="text/plain", created_at=NOW, idempotency_key="one-v1")
    repo.capture("one", b"one-v2", media_type="text/plain", created_at=NOW, expected_revision=1, idempotency_key="one-v2")
    repo.capture("two", b"shared", media_type="text/plain", created_at=NOW, idempotency_key="two-v1")
    repo.erase("one", erased_at=NOW, reason="request", expected_revision=2)
    assert objects.contains(shared.object_key)
    repo.erase("two", erased_at=NOW, reason="request", expected_revision=1)
    assert not objects.contains(shared.object_key)


def test_backup_restore_preserves_revision_reachability():
    source = repository()
    first = source.capture("capture", b"v1", media_type="text/plain", created_at=NOW, idempotency_key="v1")
    second = source.capture("capture", b"v2", media_type="text/plain", created_at=NOW, expected_revision=1, idempotency_key="v2")
    bundle = source.backup(created_at=NOW)
    assert {first.object_key, second.object_key} == set(bundle.objects)
    metadata, objects = MemoryMetadataStore(), MemoryObjectStore()
    target = repository(metadata, objects)
    target.restore(bundle)
    target.erase("capture", erased_at=NOW, reason="request", expected_revision=2)
    assert objects.objects == {}


def test_atomic_json_and_file_adapters_match_memory_semantics(tmp_path):
    repo = repository(JsonMetadataStore(tmp_path / "metadata.json"), FileObjectStore(tmp_path / "objects"))
    record = repo.capture("a", b"hello", media_type="text/plain", created_at=NOW, idempotency_key="a")
    reopened = repository(JsonMetadataStore(tmp_path / "metadata.json"), FileObjectStore(tmp_path / "objects"))
    assert reopened.read_capture("a") == (record, b"hello")
    parsed = json.loads((tmp_path / "metadata.json").read_text())
    assert parsed["schema_version"] == "krail.hosted-metadata.v1"


class FakeCursor:
    def __init__(self, connection): self.connection, self.rowcount, self._row = connection, 0, None
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params):
        self.connection.statements.append((sql, params))
        self.rowcount = 1
    def fetchone(self): return self._row
    def fetchall(self): return []


class FakeConnection:
    def __init__(self): self.statements, self.committed, self.rolled_back = [], False, False
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return FakeCursor(self)
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True


def test_postgres_adapter_uses_scoped_compare_and_swap():
    connection = FakeConnection()
    store = PostgresMetadataStore("postgresql://unused", connect=lambda dsn: connection)
    repo = HostedRepository(store, MemoryObjectStore(), tenant_id="tenant", project_id="project")
    repo.capture("capture", b"data", media_type="text/plain", created_at=NOW, idempotency_key="request")
    statements = "\n".join(sql for sql, _ in connection.statements)
    assert "tenant_id=%s AND project_id=%s" in statements
    assert "ON CONFLICT DO NOTHING" in statements
    assert connection.committed and not connection.rolled_back


def test_postgres_authorized_capture_query_pushes_policy_and_limit_to_sql():
    connection = FakeConnection()
    store = PostgresMetadataStore(
        "postgresql://unused", connect=lambda dsn: connection
    )
    with store.transaction():
        assert (
            store.list_authorized_captures(
                "tenant",
                "project",
                source_ids=("github",),
                classifications=("internal",),
                limit=51,
            )
            == []
        )

    sql, params = connection.statements[-1]
    assert "record_kind='capture'" in sql
    assert "record->'payload'->>'state'" in sql
    assert "record->'payload'->>'source_id'" in sql
    assert "record->'payload'->>'classification'" in sql
    assert "ORDER BY record_id LIMIT %s" in sql
    assert params == ("tenant", "project", ["github"], ["internal"], 51)


def test_postgres_authorized_capture_query_projects_nested_payload_policy():
    source = repository(tenant="tenant", project="project")
    expected = source.capture(
        "github-internal",
        b"visible",
        source_id="github",
        classification="internal",
        media_type="text/plain",
        created_at=NOW,
        idempotency_key="visible",
    )
    source.capture(
        "slack-internal",
        b"hidden-source",
        source_id="slack",
        classification="internal",
        media_type="text/plain",
        created_at=NOW,
        idempotency_key="hidden-source",
    )
    source.capture(
        "github-restricted",
        b"hidden-classification",
        source_id="github",
        classification="restricted",
        media_type="text/plain",
        created_at=NOW,
        idempotency_key="hidden-classification",
    )
    records = source.metadata.list("tenant", "project", kind="capture")

    class PolicyCursor(FakeCursor):
        def execute(self, sql, params):
            super().execute(sql, params)
            assert "record->'payload'->>'source_id'" in sql
            assert "record->'payload'->>'classification'" in sql
            tenant, project, sources, classifications, limit = params
            matches = [
                row
                for row in self.connection.records
                if row.tenant_id == tenant
                and row.project_id == project
                and row.record_kind == "capture"
                and row.payload.get("state", "active") == "active"
                and row.payload.get("source_id", "__legacy_unmapped__") in sources
                and row.payload.get("classification", "restricted")
                in classifications
            ]
            self._rows = [
                (row.model_dump(mode="json"),)
                for row in sorted(matches, key=lambda row: row.record_id)[:limit]
            ]

        def fetchall(self):
            return self._rows

    class PolicyConnection(FakeConnection):
        def __init__(self, values):
            super().__init__()
            self.records = values

        def cursor(self):
            return PolicyCursor(self)

    connection = PolicyConnection(records)
    store = PostgresMetadataStore(
        "postgresql://unused", connect=lambda dsn: connection
    )
    with store.transaction():
        projected = store.list_authorized_captures(
            "tenant",
            "project",
            source_ids=("github",),
            classifications=("internal",),
            limit=2,
        )

    assert [row.record_id for row in projected] == [expected.capture_id]


def test_memory_transaction_rolls_back_all_metadata_rows():
    store = MemoryMetadataStore()
    row = HostedRecord(tenant_id="tenant", project_id="project", record_kind="idempotency", record_id="one", revision=1, payload={}, created_at=NOW, updated_at=NOW)
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put(row, expected_revision=0)
            raise RuntimeError("fail")
    assert store.list("tenant", "project") == []


def test_packaged_migration_has_digest_addressed_rollback():
    plan = migrations()
    assert [item.version for item in plan] == ["0001_hosted_records"]
    migration = plan[0]
    assert migration.up_digest.startswith("sha256:")
    assert migration.down_digest.startswith("sha256:")
    assert "PRIMARY KEY (tenant_id, project_id, record_kind, record_id)" in migration.up_sql
    assert "capture_revision" in migration.up_sql
    assert "DROP TABLE IF EXISTS krail_hosted_record" in migration.down_sql
