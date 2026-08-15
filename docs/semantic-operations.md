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
authorities, resource types, classifications, and exact versioned `ResourceRef`
grants, plus a live policy digest and a self-verifying scope digest. Records are
rechecked individually. Sharing an authority is not sufficient. A record whose
provenance is not fully visible is omitted whole; responses disclose only a
coarse omission reason and never hidden identifiers or counts.

`SemanticOperationsService` requires an injected live scope authorizer and
invokes it on every operation. Customer-hosted composition should verify the
Phase 4 signed access context, expiry, capability binding, and revocation there;
local mode explicitly composes the operating-system user's project authority.

Pagination cursors are opaque authenticated envelopes bound to operation,
request shape, authority scope, policy, and the caller's permission-shaped
snapshot. Changes to hidden records do not alter visible cursors; changes to
visible records produce an explicit `cursor-stale` gap. Depth, node, edge, item,
serialized-response byte, and elapsed wall-clock ceilings are contract bounds,
with typed gaps and truthful truncation whenever a result is partial.

Local composition derives its subject from the operating-system UID, ignoring
caller-controlled `KRAIL_ACTOR` values. Its current policy digest binds that OS
identity, the absolute project, and one captured `rail.yaml` byte snapshot; the
authorization decision is evaluated from that same snapshot. Each requested
source grant is likewise checked using one captured file byte snapshot for its
exact content version/digest and path/frontmatter classification. A replacement
can therefore be observed before or after authorization, never as a mixed
identity/classification or digest/policy state. Hosted mode continues to use the
signed Phase 4 access context instead.

The serialized-byte ceiling covers the actual compact semantic result emitted
by Python, provider CLI, and MCP (including the CLI newline). Snapshot reads use
bounded database batches and stop shaping at the wall-clock deadline; final
model validation and transport serialization are included in the time check.

The software-change conformance vertical is repository → issue → change → pull
request → CI check. Every entity identifier is authority-qualified and every
fact, alias, conflict, pack, and evidence statement retains exact `ResourceRef`
provenance. Graph projections also bind the exact semantic-type revision and
the trusted reviewed pack version, content digest, revision, and review digest.
Ontology proposal history additionally requires the live subject and policy,
the exact package/change-set binding, and an authorized package-version source;
unbound drafts are not projected. Ontology induction remains propose → review → explicit publish;
these graph operations cannot publish a package, mutate semantic state, execute
an agent, or invoke an external provider.

Python consumers use `krail.provider.semantic` request/result models and
`rail.semantic.SemanticOperationsService`. The local provider exposes the same
models through `provider semantic ...`; MCP exposes
`provider_semantic_operation`. All three routes consume the same application
service and capability descriptor.
