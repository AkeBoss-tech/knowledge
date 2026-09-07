"""Optional hosted persistence adapters.

Importing this package never imports a database or cloud SDK.  The Postgres
driver is loaded only when :class:`PostgresMetadataStore` is constructed.
"""

from rail.hosted.access import (
    AccessClaims,
    AccessContextAuthority,
    AccessDenied,
    AuditEvent,
    AuditUnavailable,
    AuthorizationSummary,
    CapturePage,
    CursorInvalid,
    GovernedHostedRepository,
    InvalidAccessContext,
    MemoryAuditLedger,
    MemoryRevocationRegistry,
    PacketRequestBinding,
    SignedAccessContext,
    SignedPacketRequestBinding,
)
from rail.hosted.migration import Migration, migrations
from rail.hosted.models import (
    CaptureRecord,
    DataClassification,
    HostedRecord,
    ProjectionRecord,
)
from rail.hosted.object_store import (
    FileObjectStore,
    ImmutableObjectStore,
    MemoryObjectStore,
    S3ObjectStore,
)
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
    "AccessClaims",
    "AccessContextAuthority",
    "AccessDenied",
    "AuditEvent",
    "AuditUnavailable",
    "AuthorizationSummary",
    "CapturePage",
    "CaptureRecord",
    "ConcurrencyConflict",
    "CursorInvalid",
    "DataClassification",
    "FileObjectStore",
    "GovernedHostedRepository",
    "HostedRecord",
    "HostedRepository",
    "IdempotencyConflict",
    "ImmutableObjectStore",
    "IntegrityFailure",
    "InvalidAccessContext",
    "JsonMetadataStore",
    "MemoryAuditLedger",
    "MemoryMetadataStore",
    "MemoryObjectStore",
    "MemoryRevocationRegistry",
    "PacketRequestBinding",
    "Migration",
    "PostgresMetadataStore",
    "ProjectionRecord",
    "S3ObjectStore",
    "SignedAccessContext",
    "SignedPacketRequestBinding",
    "migrations",
]
