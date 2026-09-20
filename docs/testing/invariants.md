# Test invariants

## K21-DIRTY-HISTORY — recorded dirty intervals are durable

An external invalidation makes Company Knowledge abstain for every `known_at`
inside that invalidation's enqueue-to-clear interval. Recomputing the projection
or later re-enqueuing the same output must not rewrite the historical answer.

The highest public boundary regression is
`test_company_external_dirty_intervals_remain_historical_after_reenqueue` in
`packages/rail-py/tests/test_domain_extension_initial_proof.py`.
`test_company_upgrade_seeds_available_legacy_dirty_interval_before_reenqueue`
fault-injects a pre-ledger persisted store and proves the exact interval still
present in the legacy queue row survives its first post-upgrade transition.

## KCORE-REVIEW-TIME — review history tests use one recorded-time clock

The durable review test injects the same fixed clock into ingestion, review,
and restarted review services. Its temporal assertions therefore test
promotion, rejection, replay, and idempotence independently of the wall clock
on the day the suite runs.

## K25-SHARED-PACKET — one immutable packet remains authorization-bound

The published packet operation binds tenant, project, capability identity,
query, purpose, project scope, budgets, and exact resource grants into a
caller-authority signature. Packet creation and every later read reverify the
current signed context, signed request binding, exact grants, and current
source heads before returning any content.

`test_authorized_context_packet_service.py` proves narrow and wide users,
opaque omission, original-context expiry followed by a fresh wide grant,
fresh-grant narrowing, revocation, retained historical source bytes after the
current source head advances, tamper, restart, request substitution, full
canonical-context budget enforcement, and read-only capability publication.

## Shared packet integration fixture

- The local integration bootstrap composes the published packet operations on
  the real `Project.provider`; it does not substitute a summary helper.
- Every fixture evidence ref has an explicit tenant/project-scoped Core source
  identity, immutable version, digest, and classification binding.
- Advancing a caller-controlled current source head invalidates the cached
  packet even while the exact old resource remains retrievable for provenance.
- Revocation is controlled by the authority supplied by the caller and packet
  denial uses the metadata-free unavailable union.

## K25-PACKET-CACHE-RETENTION — removed local packets do not reappear

Only a current caller-owned signed context scoped to the configured packet
capability, tenant/project, and exact source with `retention.enforce` may
remove a local derived packet. A configured packet TTL denies access before a
maintenance pass. After a tombstone is durable, restoring stale cache bytes or
restarting cannot return the packet. A full tombstone journal fails closed and
does not report deletion while cache bytes survive.

`test_authorized_context_packet_service.py` fault-injects stale cache backup
restoration and a full durable journal at the packet service boundary.

## K29-REGISTERED-GIT-EVIDENCE — reviewed retained bytes stay canonical

An explicitly configured external Git root may contribute retained bytes only
after caller-owned signed capture and review decisions bind the exact Git ref,
capture ID, review ID, and blob digest. Retrieval rechecks the canonical source
provenance, current signed grants, and retained bytes; a narrower valid grant,
tampered bytes, altered canonical provenance, unsafe sidecar, or deadline
failure returns no evidence.

`test_registered_git_evidence.py` and the root provenance/deadline probes
cover these local bridge boundaries.
The root capture probes fault-inject delegation revocation and narrower
request-binding expiry after the lock is acquired; both must deny before any
sidecar, candidate, or manifest publication.

## K29-HISTORICAL-CAPTURE — offline archive inspection never grants authority

A trusted local restore caller may name one registered Git capture in a stopped,
verified archive and obtain its bounded retained bytes only when the fixed
manifest record, exact sidecar path, and SHA-256 digest agree. The reader rejects
linked or oversized files, unsafe IDs and paths, malformed provenance, and a
different caller-supplied expected digest without writing to the archive. An
old review ID remains historical metadata: the result explicitly attests no
current source authority or fresh semantic review. Core must independently
verify the snapshot and selected current Git blob before enrollment.

`test_historical_registered_git_evidence.py` creates a real capture through the
signed public bridge, revokes the old grant, then checks successful offline
inspection and archive mutation/escape denials at the public verifier boundary.

## K24-EXPLICIT-BELIEF-INFERENCE — declared uncertainty remains deterministic

Belief values carry explicit uncertainty semantics and are never combined by
numeric coincidence. Likelihood-ratio factors update an explicitly calibrated
probability from the original prior in log-odds space, under a declared
conditional-independence assumption. Deterministic implication and bounded
support/contradiction aggregation remain separate methods.

`test_belief_inference.py` proves a hand-verifiable `0.2 * LR(3) = 3/7`
posterior, stable extreme ratios and endpoint priors, incompatible method and
correlated-evidence rejection, exact factor/operator/input derivations,
deterministic replay, and unrelated-region exclusion. Stale evidence removes
only its factor and leaves the residual prior/evidence contribution; it never
becomes probability zero. An all-stale recomputation retains the original
prior and an explicit previous posterior reverts to that prior, marking the
output changed so downstream invalidation can continue. The direct dirty
frontier is topology-safe for a diamond, consumes newly computed upstream
states, and gates downstream traversal by its declared threshold. The engine
accepts the existing #18 projection dependency-region callback and the
trusted `DomainExtensionRegistry` operator seam, so a caller's dirty-region
and candidate/review workflow can drive the same bounded recomputation path.
`BeliefProjectService` composes the durable `TemporalProjectionService` directly:
candidate, review/promotion, and derived output are immutable temporal records;
restart discovers them from the projection rather than a second belief store.
The public operator consumes actual declared prior/evidence payloads and aligned
factor IDs. Tombstoned evidence removes only its contribution, while an
authorizer denial filters the affected candidate before any derived output is
returned.
Temporal admissibility is evaluated at the requested valid/known time, so
future, expired, superseded, and review-tombstoned rows cannot drive output.
The public service requires a current authorizer over the complete candidate,
review, prior, factor, and evidence lineage before inference or persistence;
exact derivation records are retained as temporal rows for restart explanation.

## K29-SHARED-SIGNED-AUTHORITY — connected Git actions require current exact grants

Every connected canonical Git proposal, review, read, search, and export action
must re-resolve a caller-owned signed context and verify its tenant, project,
capability identity/version, signed action/resource request binding, validity
interval, and revocation state.
Durable pending review metadata must not let a revoked reviewer resume a Git
promotion after restart, and an absent resolver grant must deny cached reads.

`test_signed_authority_public_journey_rechecks_revoked_pending_review_after_restart`
uses the public `SharedKnowledgeWorkspace` journey with real HMAC contexts,
fault-injected interruption after the durable pending receipt, reviewer
revocation, restart, fresh re-issuance, and post-cache denial.

## K29-HOSTED-READ-RELEASE — hosted reads recheck current authority after I/O

A signed source grant must still be valid immediately before the hosted facade
releases capture bytes or a scoped metadata page and cursor. Object and metadata
adapters may block after the initial decision; revocation or expiry during that
work denies the result with the same opaque `AccessDenied` surface. A successful
read still records one allowed audit event, while a raced denial records no
source names or body in the audit ledger.

`test_hosted_reader_rechecks_current_authority_after_adapter_io` exercises
both public read and list operations with a narrow second user. It injects
revocation or clock expiry inside the owning object/metadata adapter, after the
first authorization and before the facade return. All four cases failed with
returned data before the repair and pass with final release checks.

## K21-PROJECTION-STORAGE-BINDING — a derived grid cannot become parallel authority

An extension-owned rebuildable projection declares its canonical temporal
authority, exact schema/version, and writer family outside the unchanged v1
extension digest. Registration rejects conflicting writer declarations.
Preparation and derived reads verify bounded exact canonical inputs and live
authorization. A stale, missing, or revoked declaration prevents use of the
candidate grid; the robotics consumer independently resolves authorized
canonical records instead. `test_registered_spatial_projection_uses_declared_current_sources_and_canonical_fallback`
and `test_registered_projection_rejects_writer_conflict_stale_inputs_and_midread_revocation`
cover the public registry plus region-query boundary, including same-process
source changes, callback mutation, and post-query revocation.
`test_registered_spatial_read_abstains_when_another_writer_adds_a_candidate_midquery`
uses two canonical store instances to add an in-region object after the first
refresh; the raced read must abstain, and the next read must show both records.

## K23-COMPANY-GUIDANCE — reviewed guidance requires current company authority

Operational guidance joins an exact reviewed procedure with the current
company ownership and policy projections. The read path resolves owner and
policy from the canonical temporal service under signed readers, rechecks both
after the procedure read, and abstains without disclosing stale state when any
service, policy, evidence, or review lineage is revoked. It does not activate
an environment or accept caller-supplied owner/policy values.

`test_company_guidance_candidate_review_restart_and_current_authority` proves
verified Core provenance -> candidate review/promotion -> current owner/policy
-> actionable guidance, then reopening both company and procedure stores with
identical output and lineage, including a digest of a persisted local
test-result fixture. The substitution, source-revocation,
review-evidence, signed reader-revocation, scope, and authority-race regressions in
`test_domain_extension_initial_proof.py` prove that an unrelated service
cannot reuse a reviewed procedure, affected guidance abstains after
service/policy/evidence invalidation, and a procedure revoked during the final
company recheck is not returned.

## K30-COMPANY-GUIDANCE-PACKET — negotiated guidance packets retain exact review lineage

The separately negotiated `krail.company-guidance-packet@1.0.0` capability
publishes an immutable, bounded packet schema. Creation and every read require
fresh caller-signed capability, tenant/project, action, purpose/scope, and
exact-resource checks. The packet preserves the original authorization digest;
a fresh read returns a separate current reauthorization digest and timestamp.
Only current reviewed procedure, command, environment, evidence, decision,
owner, and policy refs may enter the packet. Invalidating one source tombstones
only matching packet files, and restart cannot resurrect them; unavailable
responses disclose no packet metadata.

`test_company_guidance_packet_signed_readers_restart_and_source_invalidation`
proves signed creation, deterministic digest, persisted fixture bytes, fresh
reader reauthorization, disk restart, exact lineage, and source-scoped durable
tombstoning.

## K21-OBSERVED-INVOCATION — a model observation is evidence, not review

An already-dispatched nondeterministic extension result may be bound to one
trusted-local observer's exact Run and canonical-JSON artifact only when the
artifact bytes match the result output digest, the model/provider/version and
configuration are declared, and input/Run/artifact read authority survives a
final check. The resulting digest-bound evidence says
`caller_observed_unverified`; it does not authenticate Core, verify an outcome,
activate an environment, or approve a procedure. A proposed procedure revision
must cite the evidence and all underlying exact refs, clear inherited review
and test attestations, and fail read authorization after source withdrawal.
Existing v1 extension dispatch and descriptor digests remain unchanged.

`test_observed_invocation.py` exercises public extension dispatch through the
observer and procedure-candidate APIs, including exact artifact mismatch,
revocation before and after byte read, mutable-output tampering, and denial of
the candidate after its artifact source is withdrawn. The evidence repository
is reopened before the candidate read; an authorized read resolves the exact
stored ref while a different project scope cannot reuse it.
