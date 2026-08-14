# KRAIL 1.2 reconciliation report

The requested Phase 5 commit objects and remote branch refs were unavailable in
the supplied checkout, and network access to the canonical remote was denied.
Consequently, no stale branch was blindly merged and no Phase 5 change is
claimed as ported. Current main's managed adapters, hosted authorization and
reliability implementation, CI, and version history remain intact.

The 1.2 release work adds the independent local-project onboarding boundary,
versioned proposal/work-order contracts, review-gated promotion, starter skills,
workflow templates, tests, and documentation. Phase 5 semantic commits remain a
deliberate follow-up: fetch the listed objects, compare each patch with current
main, and replay only non-superseded behavior while retaining current CI and
adapter implementations.
