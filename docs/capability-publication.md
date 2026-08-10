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
pins the descriptor digest, projected request/result schemas, and a golden
repository-plus-issue exchange whose resource provenance uses OpenSaddle's
normative `ResourceRef` and `SourceVersion` shape. Consumers can load and verify
the assets through `krail.provider.resources` without importing the `rail`
runtime.

The projection is intentionally fail-closed: it requires caller-supplied direct
source bindings and an explicit successful authorization decision, rejects
derived or truncated evidence, and does not reinterpret KRAIL's query-based
`retrieve_evidence` input as OpenSaddle's exact-ref operation.
