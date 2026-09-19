# Bounded asset-read capability proposal

Status: proposed companion contract for issue #22. This does not implement an
asset service, provider runtime, storage adapter, or provider-v1 change.

`krail.asset-read.v1` is a separately negotiated, read-only capability for one
exact immutable asset and one bounded byte range. It completes the rich-domain
design requirement that binary assets use verified immutable references and
bounded fetch semantics without embedding unbounded binary data in provider v1
or exposing storage internals.

## Wire rule

`read_asset` requires an exact authorized `asset_ref`, signed-context reference,
authorization digest, and `{start, length}` byte range. Length is 1..65,536.
Responses encode at most that many bytes as base64 and include:

- the requested exact asset ref and returned range;
- `chunk_digest`, the SHA-256 digest of decoded returned bytes;
- `complete`, which is true only when the response covers the entire immutable
  asset; and
- `total_bytes` when the authority can truthfully disclose it.

For a complete response, the implementation must verify decoded bytes against
`asset_ref.digest` as well as reporting `chunk_digest`. For a partial response,
the asset digest identifies the whole immutable asset while `chunk_digest`
verifies only the returned range. A self-reported partial chunk hash does not by
itself prove those bytes belong to that whole asset; the authorized source must
prove the immutable asset/version and range before returning it. The schema
cannot prove either hash relation; fixture conformance verifies the decoded
base64/count/range/chunk and complete-asset rules, while source proof remains a
mandatory runtime obligation.

Zero-byte assets are valid. A caller requests range `{start: 0, length: 1}`;
the successful complete result has `returned_bytes: 0`, an empty base64 string,
the SHA-256 digest of empty bytes for both asset and chunk, and `total_bytes: 0`.

The only availability/error outcomes are bounded `unauthorized`, `not_found`,
`unavailable`, `range_not_satisfiable`, `integrity_mismatch`, and
`invalid_request`. Unauthorized results disclose no authorization snapshot,
asset metadata, range availability, bytes, lineage, path, URL, credential, or
storage-handle detail.

## Authority, writer, rebuildability, and retention

| Ref/type | Canonical authority and writer | Rebuildability and retention | Read exposure |
| --- | --- | --- | --- |
| asset ref | External source authority owns bytes and immutable version/digest; KRAIL is not the writer | `available`, `rebuildable`, or `missing` remain metadata from `asset_metadata`; a digest alone never proves retention | `read_asset` only after live exact-ref authorization |
| signed-context ref | Access authority issues/signs it; caller cannot manufacture authorization from the ref | Short-lived/revocable authority record, not an asset cache | Request-only reference; raw signature/credentials stay in transport |
| authorization snapshot | Live access authority evaluates it | Never a durable grant or cache key after expiry/revocation | Successful result may disclose digest/time only |
| query snapshot ref | Domain projection writer creates it through its signed writer path | Rebuildable projection; snapshot must identify its canonical inputs | Used for page binding; does not expose a store handle |

Asset read is not a substitute for an asset-retention policy. It does not grant
write, deletion, rebuild, indexing, ROS transport, perception ingest, or robot
control authority.

The normative schema and executable fixtures are
[schema.json](contracts/krail.asset-read.v1/schema.json),
[valid.json](contracts/krail.asset-read.v1/fixtures/valid.json), and
[invalid.json](contracts/krail.asset-read.v1/fixtures/invalid.json). Semantic
fixtures and the pure validator are
[semantic-invalid.json](contracts/krail.asset-read.v1/fixtures/semantic-invalid.json)
and [conformance.py](contracts/krail.asset-read.v1/conformance.py).
