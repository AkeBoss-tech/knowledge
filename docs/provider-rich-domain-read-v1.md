# Rich-domain read capability proposal

Status: proposed contract for issue #22. It is not implemented as a provider
runtime and does not change `krail.provider.v1`.

## Decision

Use a separate opt-in contract, `krail.rich-domain-read.v1`, at version 1.0.0.
Do not create `provider.v2` for this scope. The existing provider v1 contract
remains importable and semantically unchanged: its text/evidence resource
payloads stay bounded, and it advertises neither rich-domain operations nor
binary transfer.

The separate contract is read-only and capability-negotiated. It shares the
existing immutable capability-descriptor pattern (`krail.capability-descriptor.v1`
and digest-pinned negotiation) but has its own JSON Schema because rich-domain
time, geometry, and asset metadata do not belong in the v1 request shapes.
Unknown contract identifiers or operations fail explicitly. A consumer cannot
infer this capability from an installed package version, a provider-v1 result,
or the presence of a local storage engine.

## Scope

The proposed operations are deliberately narrow:

| Operation | Reads | Bounded result | Excluded |
| --- | --- | --- | --- |
| `negotiate` | descriptor compatibility and optional expected descriptor digest | at most three advertised read operations | implicit version fallback |
| `asset_metadata` | immutable asset identity and availability | one metadata record, no content field | bytes, paths, presigned URLs, object-store handles |
| `temporal_query` | records for one entity at valid-time and known-time | page limit 1..100 | mutation, subscriptions, unbounded history |
| `spatial_query` | objects in one explicit world/frame/revision/bounds/time scope | page limit 1..100 | transform calculation, implicit frame conversion, live planning state |

Streaming/subscription and mutation/event-ingest are intentionally separate
future contracts. A successful query never authorizes source ingestion,
retention changes, index rebuilds, or robot control.

## Exact references, assets, and retention

Every resource is an `ExactRef`: authority, resource type, resource ID,
immutable version, and SHA-256 digest. A digest identifies bytes or a canonical
serialized value; it neither proves availability nor names a storage location.

`asset_metadata` returns a media type, byte count, and one truthful retention
state:

- `available`: an authorized implementation can currently serve the immutable
  external asset through a separately negotiated asset-fetch mechanism;
- `rebuildable`: bytes are not necessarily locally available but an authority
  declares a rebuild path; callers must not treat it as immediately fetchable;
- `missing`: the referenced bytes are unavailable.

The capability deliberately does not define asset fetch. A future bounded asset
delivery contract must authorize the exact asset and specify transport, byte
range, response limits, and error behavior separately. No SQLite, DuckDB,
filesystem path, ORM object, MCAP handle, or object-store credential crosses
this contract.

## Bitemporal and spatial semantics

Temporal reads require both:

- `valid_at`: when state was effective in the represented world; and
- `known_at`: when the reader is allowed to treat the record as known.

Each returned temporal record carries exact record and lineage refs plus
`valid_from`, optional `valid_to`, and `recorded_at`. This supports a company
ownership state effective Tuesday but learned Thursday without collapsing the
two times.

Spatial reads require an exact world ref, `frame_id`, `map_revision`, the
constant unit `metres`, and three-dimensional minimum/maximum bounds. The
contract does not define transformations; a missing field fails validation,
while an unavailable frame, mismatched map revision, or stale source returns an
explicit bounded abstention with no candidates. Bounds are
query constraints, not an assertion that all geometry bytes were fetched.

## Authorization and lineage

Every request names a `signed_context_ref` and `authorization_digest`. The
transport authenticates and verifies that context against the current authority
and revocation state before candidate enumeration. The schema intentionally
does not carry a raw signature, bearer token, credentials, or a list of denied
resources.

Every response includes an `authorization` snapshot digest/time and every
material result includes lineage with a derived ref and one or more exact source
refs. Implementations must re-check the complete dependency chain immediately
before disclosure. They must not reveal unauthorized candidate identities,
counts, metadata, or lineage through omissions, cursors, or errors. A cached
result is invalid once its authorization context or a dependency changes.

Canonical authority stays outside this read contract:

- the source/system owner retains authority for original assets and observations;
- KRAIL owns only the durable records and declared projections it writes through
  existing signed writer paths;
- an index is a rebuildable projection, never an implicit canonical authority;
- retention and rebuildability must be stated by the owning authority.

## Negotiation and compatibility

Providers publish an immutable descriptor with the existing descriptor digest
mechanism. A rich-domain consumer requests `krail.rich-domain-read.v1`, a
supported semantic version, the operation set it needs, and optionally
`expected_descriptor_digest`. The provider returns its exact descriptor digest,
the intersected operation list, and a bounded diagnostic. Mismatched major
versions, unknown operation IDs, or an expected-digest mismatch are
incompatible. This is normative design behavior only: no provider runtime or
production negotiation implementation is introduced by this proposal.

Temporal and spatial result pages include a `PageBinding` containing the digest
of the canonical query (without cursor), an exact snapshot ref, and the current
authorization digest. A cursor is opaque and must be accepted only when its
server-side binding matches those three values and the requested operation/page
limit. Reusing a cursor across a query, snapshot, principal/scope, or
authorization revision fails with the bounded `cursor_invalid` error. The schema
defines this response shape; token signing and validation remain runtime work.

All errors use one bounded `ErrorResponse`. `unauthorized` intentionally does
not carry an authorization snapshot, lineage, candidate list, or omission count.
`unavailable` is the truthful asset/source availability response; it is distinct
from an `asset_metadata` retention state because no metadata is disclosed when
the source itself cannot be read.

| Surface | Compatibility decision |
| --- | --- |
| `krail.provider.v1` Python models and OpenSaddle wire protocol | Unchanged. No new v1 operation, field, capability enum, or binary payload. |
| `krail.capability-descriptor.v1` | Reused as the descriptor/negotiation publication pattern; publication does not grant access. |
| `krail.rich-domain-read.v1` | New optional, read-only contract. Consumers request it explicitly. |
| provider v2 | Deferred. It is unnecessary until multiple rich contracts need a coordinated incompatible provider-wide change. |

## Executable wire examples

The normative proposed schema is
[schema.json](contracts/krail.rich-domain-read.v1/schema.json). Its fixtures are
[valid.json](contracts/krail.rich-domain-read.v1/fixtures/valid.json) and
[invalid.json](contracts/krail.rich-domain-read.v1/fixtures/invalid.json).
`packages/rail-py/tests/test_rich_domain_read_contract.py` validates them with
Draft 2020-12 JSON Schema.

The valid fixtures include:

- a robotics immutable image asset reference;
- a robotics spatial request whose frame, map revision, unit, bounds, valid
  time, and known time are all explicit; and
- a company temporal request and result querying a service ownership record
  effective on Tuesday and recorded Thursday; and
- an explicit robotics spatial abstention with an empty candidate list and
  page binding.

Invalid fixtures prove that a spatial request cannot omit frame/revision/units,
an asset metadata request cannot embed content or a storage path, exact refs
reject extra fields, temporal timestamps/page limits are validated, an abstained
spatial response cannot disclose candidates, and a `krail.provider.v1` message
cannot be treated as a rich-domain request.

## Deferred implementation work

This design does not close #20 or #22. It intentionally leaves live ROS/MoveIt
state, tf2 transport, perception ingestion, binary storage/fetch, vector/ANN
search, spatial indexes, benchmarks, subscriptions, and write ingest contracts
unimplemented. The first #20 implementation after contract review should be a
rebuildable local current-state/spatial projection with restart/rebuild and
bounded-local-movement proof, retaining external asset references.
