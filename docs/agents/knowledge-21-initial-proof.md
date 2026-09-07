# Knowledge #21 Initial Proof

Status: locally implemented and ready for root review on
`codex/roadmap-knowledge-followup`; not committed or pushed by this worker.

## Proven journey

The same `DomainExtensionRegistry` now publishes executable, typed operations
for two unrelated domains:

- robotics object-state lookup and action-freshness validation, backed by the
  existing persisted tabletop temporal memory;
- company service-ownership lookup, effective-time validation, and effective
  policy lookup, backed by the existing `TemporalProjectionService` store.

The company fixture contains two teams and two services. It proves an approved
ownership transfer that is valid Tuesday but only recorded and ingested
Thursday: a Tuesday known-time query returns the old owner, while a Tuesday
valid-time query with Thursday knowledge returns the corrected owner. The old
immutable record remains replayable. Reported ownership, a policy draft, and a
contradictory chat record remain evidence and do not replace approved state.

Independent approved authorities with different owners produce `conflict`
without selecting an owner. An inaccessible approved candidate produces
`unknown` without returning its candidate identity or lineage. A denied exact
query ref, a revoked signed context, or an operator capability whose version or
descriptor digest does not match fails closed.

Every successful registered result binds the exact request ref, canonical
temporal record refs, source refs, operator version, operator descriptor digest,
config digest, output digest, and lineage digest. Company hosted composition
checks tenant, project, actual operator ID/version/descriptor digest, the
`context.read` action, non-wildcard source grants, and exact refs before work and
again before return. The directly callable ownership, policy, and effective-time
service methods enforce the same operator-specific boundary; a context for a
different tenant, project, operator, version, or descriptor cannot bypass the
registry checks.

An exact evidence deletion tombstone invalidates the ownership answer. After a
fresh `TemporalProjectionService` restart and rebuild, the answer remains
`stale` and discloses no prior owner. Robotics observation invalidation likewise
changes its registered freshness result to `needs-refresh` after restart.

A late service-source correction also dirties its exact ownership and policy
dependents. A query whose `known_at` predates the correction remains historically
current. At and after the correction, both APIs abstain as `stale` before
recompute, after restart, and after the work queue is cleared; recomputation does
not turn a record that still cites the superseded source back into guidance. A
new reviewed ownership and policy revision citing the corrected exact service
record restores current answers.

External dirty hints now retain an append-only enqueue/clear transition ledger
separate from the mutable work queue. Recompute and a later same-output enqueue
therefore cannot rewrite a historical Company ownership or policy answer. On
upgrade, the first transition seeds the exact interval still available in a
legacy queue row before recording new work; intervals already erased by an old
mutable row are not reconstructed or guessed.

## Compatibility and boundaries

- The temporal envelope and original evidence bytes are unchanged.
- `robotics.world-memory.location@1.0.0` remains registered for v1 callers.
- Core dispatch has no robotics or company branch and imports neither optional
  domain module.
- No model, inference, embedding, ANN, external adapter catalogue, deployment,
  credential, or new canonical-store claim is part of this proof.
- This completes the explicit two-domain initial proof requested by #21 and
  advances the ownership/policy slice of #23. The broader #15/#23 record and
  query catalogue remains pending.

## Local verification

```text
70 passed in 1.44s
  test_domain_extension_initial_proof.py
  test_extension_registry.py
  test_robotics_world_memory.py
  test_procedure_projection.py

892 passed in 62.53s
  full rail-py suite under the existing Conda interpreter, with persistent
  no-install overlays for rfc8785 and PyYAML inherited by subprocess tests.
  The durable core-provenance review test now injects its existing fixed clock
  consistently, so its fixed known-time assertion is independent of wall time.

25 passed in 0.87s
  schema-only suites under the prescribed Conda interpreter, with the existing
  no-install overlay

10 bounded Pydantic schemas verified with additionalProperties=false
compileall: clean
git diff --check: clean
```

Full-suite reproduction after an execution-environment reset:

```bash
KRAIL_TEST_OVERLAY="$(mktemp -d)"
mkdir -p "$KRAIL_TEST_OVERLAY/lib/python3.13/site-packages" "$KRAIL_TEST_OVERLAY/lib/python/site-packages"
cp -R /Users/akashdubey/Documents/CodingProjects/opensaddle/.venv/lib/python3.13/site-packages/rfc8785 "$KRAIL_TEST_OVERLAY/lib/python3.13/site-packages/rfc8785"
cp -R /Users/akashdubey/Library/Python/3.9/lib/python/site-packages/yaml "$KRAIL_TEST_OVERLAY/lib/python/site-packages/yaml"
PYTHONUSERBASE="$KRAIL_TEST_OVERLAY" PYTHONPATH=packages/rail-py /opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q packages/rail-py/tests
```
