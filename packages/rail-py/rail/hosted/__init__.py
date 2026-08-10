"""Optional hosted persistence adapters.

Importing this package never imports a database or cloud SDK.  The Postgres
driver is loaded only when :class:`PostgresMetadataStore` is constructed.
"""

from rail.hosted.models import CaptureRecord, HostedRecord, ProjectionRecord
from rail.hosted.migration import Migration, migrations
from rail.hosted.object_store import FileObjectStore, ImmutableObjectStore, MemoryObjectStore, S3ObjectStore
from rail.hosted.repository import (
    ConcurrencyConflict,
    HostedRepository,
    IdempotencyConflict,
    IntegrityFailure,
    JsonMetadataStore,
    MemoryMetadataStore,
    PostgresMetadataStore,
)

__all__ = [
    "CaptureRecord",
    "ConcurrencyConflict",
    "FileObjectStore",
    "HostedRecord",
    "HostedRepository",
    "IdempotencyConflict",
    "ImmutableObjectStore",
    "IntegrityFailure",
    "JsonMetadataStore",
    "MemoryMetadataStore",
    "PostgresMetadataStore",
    "ProjectionRecord",
    "Migration",
    "migrations",
    "S3ObjectStore",
]
