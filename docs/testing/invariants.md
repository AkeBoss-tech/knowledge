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
