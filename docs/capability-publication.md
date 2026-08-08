# Context Brief Capability Publication

KRAIL publishes the provider-issued `krail.context-brief` capability at semantic
version `1.0.0`. Its immutable descriptor covers the `retrieve_evidence` and
`context_brief` read operations, embeds their strict input/output JSON schemas,
declares payload and cardinality bounds, records semantic-processing versions,
and is addressed by a SHA-256 digest of its canonical content.

Consumers may publish the descriptor directly or negotiate a same-major version
with an optional exact descriptor-digest pin. A successful negotiation means
only that the public request/result contract is compatible. Descriptor
availability does not grant authorization: registration, policy, approval,
credentials, and execution remain outside KRAIL and outside this descriptor.

For local Python callers, use `project.capability_descriptor()`,
`project.negotiate_capability(request)`, and
`project.provider.context_brief(request)`. Equivalent local CLI routes are
`krail --local provider capability` and
`krail --local provider context-brief '<request-json>'`. MCP exposes the same
application seam as `provider_capability` and `provider_context_brief`.

KRAIL remains standalone: publication and Context Brief assembly require no
OpenSaddle import, hosted service, external action, credential, or approval
system.
