# KRAIL Provider Contract v1

`krail.provider.v1` is the public, implementation-independent read boundary for
KRAIL knowledge. It is a set of strict Pydantic wire models in the `krail`
distribution, not a storage interface or a hosted service. Importing it loads
only Pydantic and the Python standard library.

```python
from krail.provider.v1 import ResourceRef, SearchRequest

request = SearchRequest(query="provider boundary", limit=10)
schema = SearchRequest.model_json_schema()
```

A provider may use repo files, an index, hydrated ontology artifacts, or another
local implementation internally. A consumer must rely only on these models and
must not infer those internals. OpenSaddle is one possible consumer, but KRAIL has
no OpenSaddle dependency and the contract contains no OpenSaddle-specific field.

The local implementation is reached from Python as `rail.local(path).provider`.
The CLI exposes the same application behavior under `krail --local provider`,
and MCP exposes the nine `provider_*` tools (negotiation plus the eight reads).
All three routes validate the same strict request/result models.

## Operations

Each request and result has a constant `contract: "krail.provider.v1"`, forbids
unknown fields, and can emit implementation-neutral JSON Schema with
`model_json_schema()`.

| Operation | Request | Result | Bound |
| --- | --- | --- | --- |
| negotiate | `ProviderInfoRequest` | `ProviderInfoResult` | eight named capabilities and a bounded diagnostic |
| describe types | `DescribeTypesRequest` | `DescribeTypesResult` | 256 descriptors |
| search | `SearchRequest` | `SearchResult` | 100 hits, bounded previews and cursor |
| find exact IDs | `FindRequest` | `FindResult` | 100 requested IDs / hits |
| get resource | `GetResourceRequest` | `GetResourceResult` | 1 MiB UTF-8 content |
| retrieve evidence | `RetrieveEvidenceRequest` | `RetrieveEvidenceResult` | packet limits below |
| explain | `ExplainRequest` | `ExplainResult` | 32 input refs/evidence items; 65,536-character explanation |
| lineage | `LineageRequest` | `LineageResult` | depth 16, 200 nodes, 400 edges |
| integrity | `IntegrityRequest` | `IntegrityResult` | 100 refs, 200 findings |

Search is ranked text/semantic discovery. Find is an exact, authority-local
identifier lookup. Neither operation promises or accepts SQL, SPARQL, graph
traversal syntax, query plans, storage paths, or runtime handles.

The local provider uses content-addressed immutable versions. Search cursors are
opaque, query-bound tokens and cannot page beyond the 100-result contract window.
Exact reads reject authority, version, or digest drift instead of returning newer
content. If no exact readable evidence exists, evidence and explanation fail
explicitly rather than constructing an untraced packet.

## Evidence bounds and traceability

An `EvidencePacket` contains 1–32 items. Each excerpt is 1–16,384 UTF-8 bytes,
and all excerpts together are at most 131,072 UTF-8 bytes. These are byte limits,
not Unicode character limits. The complete serialized packet is additionally
limited to 262,144 UTF-8 bytes, bounding reference and locator overhead. Requests
can ask for lower item/content-byte bounds and a provider sets `truncated=true`
when more evidence was available.

Every item includes a locator and a complete `ResourceRef`; authority, resource
identity, immutable version, and SHA-256 digest are all mandatory. Consequently,
an excerpt cannot enter a valid packet with only a URL, mutable path, or citation
label. The locator identifies the excerpt inside the exact referenced value; it
does not replace version or digest.

`ResourceRef.verifies()` checks bytes against the declared digest. A complete
`ResourcePayload` performs this check during validation; a payload marked
`truncated` retains the full-resource digest but cannot verify it from its prefix.

The executable fixture is
`packages/rail-py/tests/fixtures/provider_v1/evidence_packet.json`. The adjacent
invalid fixture demonstrates that an unqualified authority is rejected.

## Compatibility and versioning

- Additive documentation and new model classes may ship within v1.
- Existing fields may not change meaning, type, requiredness, default, or bound.
- Existing enum values and accepted identifier syntax may not be removed in v1.
- Because models reject unknown fields, adding a field to an existing wire model
  requires a new contract version unless all v1 consumers have negotiated it.
- Bug fixes may reject values that already violated a documented invariant.
- A breaking shape or semantic change creates `krail.provider.v2`; v1 remains
  importable during a declared migration window.
- Producers emit exactly one contract version per value. Consumers reject an
  unsupported `contract` value rather than guessing or silently coercing it.
- `ProviderInfoRequest.consumer_version` compares the consumer and installed
  provider major versions. `compatible=false` and a version-skew diagnostic are
  returned on mismatch; capabilities are never inferred from package version.

`ResourceRef` identity rules are normative in
[Storage Authority and Resource Identity](storage-authority.md). Provider v1 is
read-only: mutation, workflow execution, planning, and storage administration are
deliberately outside this contract.
