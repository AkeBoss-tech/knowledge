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
