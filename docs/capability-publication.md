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

## Phase 3 evidence capabilities

KRAIL also publishes two provider-issued immutable descriptors at semantic
version `1.0.0`:

- `krail.verification-evidence` exposes `assemble_verification_evidence`. It
  performs read-only, deterministic interpretation of caller-supplied bounded
  artifacts. It never runs the described commands, reads newer repository or CI
  state, or mutates an external system.
- `krail.outcome-evidence` exposes `ingest_outcome_evidence`. The accepted
  service deterministically returns a KRAIL semantic observation without
  persistence, so the v1 descriptor truthfully retains its existing read-only,
  no-external-effects declaration. Provider-confirmed commit, pull-request,
  review, CI, and check resources remain externally authoritative and pinned to
  the supplied exact versions.

Both descriptors embed strict request/result schemas, bounds, processing
versions, compatibility range, and their canonical SHA-256 descriptor digest.
Descriptor availability still grants no authorization. Neither capability
claims GitHub execution, connector writes, approvals, credentials, scheduling,
or OpenSaddle wire compatibility.

The shared `krail.capability-descriptor.v1` effect shape is intentionally not
extended. Richer local-effect metadata would require a separately versioned
descriptor schema and codec bundle so strict v1 consumers and the accepted
`krail.context-brief` descriptor digest remain stable.

Python callers use `project.assemble_verification_evidence(request)` and
`project.ingest_outcome_evidence(envelope)`. The strict outcome envelope carries
the new `OutcomeIngestRequest` and the optional exact prior
`OutcomeObservation`, so supersession has the same deterministic input on every
transport. The equivalent CLI
routes are `provider assemble-verification-evidence` and
`provider ingest-outcome-evidence`; MCP exposes
`provider_assemble_verification_evidence` and
`provider_ingest_outcome_evidence`. All routes delegate to the same application
services.
