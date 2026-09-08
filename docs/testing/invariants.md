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
