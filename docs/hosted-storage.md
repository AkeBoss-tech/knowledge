# Optional hosted storage

KRAIL remains local-first. Installing or importing the default package does not
connect to a database, object store, or external service. Phase 4 adds an
optional storage composition for customer-hosted deployments:

```text
HostedRepository
  -> MetadataStore (Postgres in production; atomic JSON locally)
  -> ImmutableObjectStore (customer object storage; filesystem locally)
```

The repository is scoped by an opaque tenant ID and project ID. Metadata keys
always include both values. Captures use content-addressed immutable objects,
optimistic aggregate revisions, and command-digest-bound idempotency keys.
Projections are explicitly rebuildable and omitted from backups. Erasure leaves
only a non-sensitive tombstone and deletes bytes after the final scoped
reference disappears. Retention uses the same erasure path.

## Postgres

Install the optional driver with `pip install 'krail[hosted]'`, apply
`rail/hosted/migrations/0001_hosted_records.sql`, and construct
`PostgresMetadataStore` with a secret-resolved DSN. Rollback for a deployment
that has exported and verified its data is in
`0001_hosted_records.down.sql`. KRAIL does not read credentials from project
files and does not create cloud resources.

The metadata transaction commits capture metadata and its idempotency record
atomically. Object bytes are uploaded first under their SHA-256-derived key.
That makes retry safe; a database failure can leave only an unreferenced,
content-addressed object, which an operator may remove with an inventory-based
garbage collector.

## Backup, restore, and rebuild

`HostedRepository.backup()` creates a digest-protected, tenant/project-bound
bundle containing durable capture/idempotency metadata and referenced object
bytes. It excludes projections. `restore()` verifies the bundle digest and every
object key before inserting records and refuses to overwrite divergent state.
After restore, call `rebuild_projection()` for each configured projection.

Production deployment still needs customer choices for Postgres provisioning,
object-store client and encryption policy (`krail[hosted-s3]` supplies the
optional boto3 adapter), backup destination/schedule, key
management, monitoring, and migration execution. Those operational choices are
outside this repository adapter and are not silently defaulted.
