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
optimistic aggregate revisions, command-digest-bound idempotency keys, and an
immutable revision-reference ledger so every historical object remains
reachable for backup and eventual erasure.
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

The metadata transaction commits current capture metadata, its immutable
revision reference, and its idempotency record atomically. Object bytes are
uploaded first under their SHA-256-derived key.
That makes retry safe; a database failure can leave only an unreferenced,
content-addressed object, which an operator may remove with an inventory-based
garbage collector.

## Backup, restore, and rebuild

`HostedRepository.backup()` creates a digest-protected, tenant/project-bound
bundle containing durable capture/revision/idempotency metadata and referenced object
bytes. It excludes projections. `restore()` verifies the bundle digest and every
object key before inserting records and refuses to overwrite divergent state.
After restore, call `rebuild_projection()` for each configured projection.

Production deployment still needs customer choices for Postgres provisioning,
object-store client and encryption policy (`krail[hosted-s3]` supplies the
optional boto3 adapter), backup destination/schedule, key
management, monitoring, and migration execution. Those operational choices are
outside this repository adapter and are not silently defaulted.

## External authorization and delegation

`GovernedHostedRepository` is the fail-closed composition boundary for a
customer-hosted control plane. KRAIL does not mint user identity, infer roles,
or treat capability publication as permission. The control plane supplies a
signed `krail.signed-access-context.v1` envelope whose claims bind:

- issuer, tenant, project, subject, delegator, delegation ID, and optional
  parent-delegation digest;
- exact capability ID, version, immutable descriptor digest, and policy digest;
- allowed repository actions, source IDs, and data classifications;
- issue/not-before/expiry times and a replay-resistant nonce.

The reference `AccessContextAuthority` uses HMAC-SHA256 for deterministic local
parity and tests. Customer deployments should resolve its verification keys
outside project files or replace it at the composition root with their
control-plane verifier. Every repository operation re-verifies signature,
validity, capability identity, tenant/project scope, and revocation before it
reads object bytes or mutates metadata. Revocation is therefore not bypassed by
the bounded decision cache.

Capture metadata carries an exact source ID and one of `public`, `internal`,
`confidential`, or `restricted`. Erasure removes both fields from current and
historical revision records along with content identity and object metadata;
the Phase 4 revision-ledger object reachability and final-reference deletion
rules remain unchanged.

Legacy active records that predate these fields are quarantined as source
`__legacy_unmapped__` with classification `restricted`. KRAIL does not invent a
real source or weaken their classification; an authoritative deployment
migration must explicitly map and rewrite them.

## Non-leaking projections and audit

Authorized listing filters source and classification before presentation.
Capped authorization scans are pushed into the metadata adapter as
source/classification/state predicates plus `LIMIT`, so hidden population
cannot trigger a distinct caller-visible bound failure or unbounded row
materialization. Omission metadata is always coarse rather than querying a
hidden count.
Cursors are opaque HMAC-bound values tied to the signed context, tenant/project
scope, and the exact visible snapshot. Policy omissions report only
`count_precision: undisclosed`, `omitted_count: 0`, and a coarse reason. Hidden
record IDs, types, labels, counts, and per-item denial reasons are never placed
in cursor or response metadata.

Authorization audit is required before an allowed effect. Audit outage or hash
chain corruption fails closed. `krail.access-audit.v1` events contain only
digests of context, scope, subject, and target plus the action, coarse decision
reason, sequence, and previous-event digest. They contain no source content,
tenant/project names, capture IDs, delegation subjects, or erasure reasons.

Full backup, restore, projection rebuild, and retention enforcement require an
unrestricted source/classification grant in addition to their exact action.
Backup creation verifies every referenced object against immutable size,
digest, and scoped key metadata. Restore validates the bundle digest, every
record/payload scope, the exact referenced-object inventory, and all object
bytes before publishing any object. Metadata insertion remains transactional;
an injected database failure can leave only verified, content-addressed,
unreferenced objects suitable for inventory-based collection.

## Deployment residuals

The repository deliberately does not select an IdP/token format, key manager,
revocation service, durable SIEM/audit sink, Postgres topology, object-store
encryption policy, backup schedule/destination, or SLO. A production
composition must supply and test those adapters. The in-memory revocation and
audit implementations are deterministic semantic fakes, not production
durability mechanisms.
