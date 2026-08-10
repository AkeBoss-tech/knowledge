# Governed semantic graph operations

KRAIL Phase 5 exposes a fixed read vocabulary over the evidence-backed semantic
kernel. It is not SQL, SPARQL, Cypher, or an arbitrary graph-query endpoint.
The published `krail.semantic-operations` capability contains only:

- `resolve_entity`, `get_entity`, and `traverse_relationships`;
- `compare_observations`, `explain_conflict`, and
  `assemble_cross_source_evidence`;
- reviewed/published ontology and trusted semantic-pack projections; and
- policy-bound ontology proposal history.

Every request carries an exact tenant/project/subject scope, allowed source
authorities and resource types, a live policy digest, and a self-verifying scope
digest. Records are rechecked individually. A record whose provenance is not
fully visible is omitted whole; responses disclose only a coarse omission
reason and never hidden identifiers or counts.

`SemanticOperationsService` requires an injected live scope authorizer and
invokes it on every operation. Customer-hosted composition should verify the
Phase 4 signed access context, expiry, capability binding, and revocation there;
local mode explicitly composes the operating-system user's project authority.

Pagination cursors are opaque authenticated envelopes bound to operation,
request shape, authority scope, policy, and an exact semantic-store snapshot.
A changed snapshot produces an explicit `cursor-stale` gap. Depth, node, edge,
item, byte, and time ceilings are contract bounds, with typed gaps and truthful
truncation whenever a result is partial.

The software-change conformance vertical is repository → issue → change → pull
request → CI check. Every entity identifier is authority-qualified and every
fact, alias, conflict, pack, and evidence statement retains exact `ResourceRef`
provenance. Ontology induction remains propose → review → explicit publish;
these graph operations cannot publish a package, mutate semantic state, execute
an agent, or invoke an external provider.

Python consumers use `krail.provider.semantic` request/result models and
`rail.semantic.SemanticOperationsService`. The local provider exposes the same
models through `provider semantic ...`; MCP exposes
`provider_semantic_operation`. All three routes consume the same application
service and capability descriptor.
