# Context Brief Capability Publication

KRAIL publishes the provider-issued `krail.context-brief` capability at semantic
version `1.0.0`. Its immutable descriptor covers the `retrieve_evidence` and
`context_brief` read operations, embeds their strict input/output JSON schemas,
declares payload and cardinality bounds, records semantic-processing versions,
and is addressed by a SHA-256 digest of its canonical content.

Consumers may publish the descriptor directly or negotiate the exact advertised
semantic-version interval (`>=1.0.0,<2.0.0`, using SemVer precedence)
with an optional exact descriptor-digest pin. A successful negotiation means
only that the public request/result contract is compatible. Descriptor
availability does not grant authorization: registration, policy, approval,
credentials, and execution remain outside KRAIL and outside this descriptor.
Under SemVer precedence, `2.0.0-alpha` is below the exclusive `2.0.0` ceiling
and is therefore inside this interval; `1.0.0-alpha` is below its inclusive
floor and is not compatible.

For local Python callers, use `project.capability_descriptor()`,
`project.negotiate_capability(request)`, and
`project.provider.context_brief(request)`. Equivalent local CLI routes are
`krail --local provider capability` and
`krail --local provider context-brief '<request-json>'`. MCP exposes the same
application seam as `provider_capability` and `provider_context_brief`.

KRAIL remains standalone: publication and Context Brief assembly require no
OpenSaddle import, hosted service, external action, credential, or approval
system.

## OpenSaddle wire bundle

The installable package includes a language-neutral fixture at
`krail/resources/contracts/krail.context-brief.v1/bundle.json`. Its manifest
pins the `krail.context-brief.opensaddle-v1` wire descriptor digest, its
embedded projected request/result schemas, and a golden
repository-plus-issue exchange whose resource provenance uses OpenSaddle's
normative `ResourceRef` and `SourceVersion` shape. Consumers can load and verify
the assets through `krail.provider.resources` without importing the `rail`
runtime.

The wire capability has a distinct ID because its schemas are not the Python
provider model schemas. Its digest atomically binds the projected schemas, the
exact OpenSaddle provider-contract bundle, read-only effects, limits,
processing versions, compatibility range, authorization declaration, and the
source `krail.context-brief` descriptor. The internal descriptor is retained
only as named provenance and must never be negotiated as the wire exchange.

The projection is intentionally fail-closed: it requires caller-supplied direct
source bindings and an explicit successful authorization decision, rejects
derived or truncated evidence, and does not reinterpret KRAIL's query-based
`retrieve_evidence` input as OpenSaddle's exact-ref operation.

The direct-evidence profile preserves exact source identity, locator, content,
and input order. It accepts only enumerated UTF-8 textual media types
(`text/plain` and `text/markdown`). KRAIL media type and relevance are bound
through the opaque `evidence_id`, which is itself covered by the recomputable
wire `record_digest`, but they are not presentation fields in the normative
Citation. Wire content must therefore be treated as UTF-8, untrusted text, and
the projection is intentionally not fully reversible. Contiguous
citations for one resource retain their order; interleaved repeated resources,
duplicate evidence records, binary/unknown media, and any value whose semantics
cannot be represented are rejected instead of regrouped or silently dropped.
