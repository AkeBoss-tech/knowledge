# `krail.provider.v1`

Status: Phase 1 OpenSaddle-owned integration contract.

This directory is the language-neutral boundary between OpenSaddle and a
KRAIL-compatible knowledge provider. It defines bounded read messages only.
It is not a KRAIL runtime API, storage model, query language, or permission
grant.

The canonical machine-readable contract is
[`schemas/protocol.schema.json`](schemas/protocol.schema.json). Deterministic
positive and negative examples are in
[`fixtures/conformance.json`](fixtures/conformance.json). A conforming provider
MUST validate the complete request or response against that schema and MUST
also enforce the semantic invariants below.

## Identity and provenance

`ResourceRef` is the only cross-boundary resource identity. The stable identity
is the tuple `(issuer, resource_id)`; `issuer` is an absolute, provider-owned
URI and `resource_id` is meaningful only within that issuer. A dereferenceable
snapshot is the larger tuple `(issuer, resource_id, resource_type, version,
digest)`. Consumers MUST NOT compare or cache resources by `resource_id` alone.

Every `ResourceRef` also contains `source`, an exact `SourceVersion` projection
of OpenSaddle's authoritative knowledge provenance: source ID, opaque origin,
source version, and SHA-256 digest. `EvidencePacket.results[].resource` and each
citation's `resource` are complete versioned refs, never bare IDs. For a direct
citation, the citation ref MUST be byte-for-byte equal after canonical JSON
serialization to its containing result ref. A derived result MAY cite another
exact ref, but MUST include that ref in `inputs` and declare `relation:
"derived_from"`.

`excerpt_digest` identifies the cited bytes or Unicode text selected by the
bounded locator. When `content` is present, its SHA-256 MUST equal
`excerpt_digest`. The contract does not require content to be returned.
Span offsets are bounded to the I-JSON safe-integer domain (at most
`9007199254740991`) so exact locators and RFC 8785 canonicalization remain
portable across languages.

## Operations

The only Phase 1 operations are:

- `negotiate`: health, supported contract versions, operations, and limits;
- `describe_types`: bounded discovery of provider resource types;
- `search`: bounded text search;
- `find`: bounded structured equality matching, not a query language;
- `get_resource`: retrieve one exact `ResourceRef`;
- `retrieve_evidence`: retrieve evidence for exact refs;
- `explain`: explain a result from exact input refs and evidence;
- `lineage`: traverse bounded version/provenance edges;
- `integrity`: verify exact refs and digests.

There are no writes, execution hooks, approvals, SQL/SPARQL, arbitrary query
expressions, or graph mutation operations. Transport bindings may map these
messages to local IPC, HTTP, or another request/response transport without
changing their meaning.

## Authorization shaping

Authorization remains with the authoritative provider/control-plane boundary.
Every successful collection response includes `authorization`. It can report a
bounded `omitted_count`, coarse `reason_codes`, and `count_precision` without
identifying hidden resources. It MUST NOT contain hidden resource IDs, labels,
types, excerpts, locators, digests, or per-item reasons. An entirely forbidden
direct lookup returns the typed `unauthorized` error and no resource payload;
`not_found` MUST NOT be used to reveal whether a hidden resource exists.

## Pagination and limits

Cursors are opaque, request-shape-bound strings. A provider MUST reject a cursor
created for a different operation, query, selector set, filter, principal, or
authorization snapshot with `cursor_invalid`. Providers MUST enforce the
advertised limits at or below the schema maxima. They MUST NOT silently accept
or truncate an over-limit request. Responses that cannot fit the negotiated
byte limit fail with `payload_too_large`; pagination never licenses an
unbounded response.

## Errors and versioning

Errors use the closed `ErrorCode` vocabulary and never place source content or
hidden identifiers in `message` or `details`. `retryable` is explicit.

`krail.provider.v1` is additive within major version 1: optional fields and new
enum values may be introduced only after capability negotiation, and existing
field meanings, required fields, bounds, and digest rules cannot be weakened.
A new required field, changed meaning, relaxed identity/provenance rule, or
incompatible enum behavior requires `krail.provider.v2`. Consumers MUST ignore
unknown optional fields only when the negotiated capability declares them;
otherwise the closed schemas fail fast.

## Deterministic conformance

Fixtures use fixed timestamps, identifiers, cursors, ordering, and SHA-256
values. Implementations MUST preserve array order and use RFC 8785 JSON
Canonicalization Scheme semantics when a canonical comparison or fixture digest
is required. The fixture suite covers every operation, typed errors,
authorization omission metadata, malformed identifiers, missing provenance,
and schema bounds.
