# Knowledge issue 24: explicit belief and factor layer

Date: 2026-09-07

The bounded implementation lives in `packages/rail-py/rail/belief_inference.py`.
It adds explicit `BeliefState`, `FactorRecord`, `InferenceEdge`,
`DerivationRecord`, and `InferenceRun` records plus a deterministic
`BeliefInferenceEngine`. Existing temporal records and `ClaimRecord.confidence`
are unchanged.

The probabilistic primitive uses the declared odds identity

```text
posterior odds = prior odds × likelihood ratio
```

and sums declared log likelihood ratios before applying a sign-stable sigmoid.
For prior `p=.2` and `LR=3`, the result is `3/7`. Ratios must be finite and
positive; endpoint priors remain exact 0 or 1. No epsilon is silently added.
Factors must explicitly declare conditional independence. Replaying the same
factor ID with different content is rejected, and replay recomputes from the
original prior rather than feeding a prior posterior back into the update.

Deterministic implication and support/contradiction aggregation use separate
semantics. A support score is not a probability: each support/contradiction
score is bounded to `[0,1]`, aggregation is capped at `1`, and calibrated
probability inputs are rejected. Equal numeric values with different semantics
cannot be combined by the engine. Cyclic inference edges are rejected.

Every derivation records exact input refs, omitted stale refs, factor ref and
version, operator ref and version, context keys, prior/output states, run ID,
and derived time. The engine accepts the existing #18 projection
`affected_region` callback as an optional dependency-region input. Stale refs
are omitted from factor computation, leaving the residual prior and admissible
factors; stale evidence is never treated as probability zero. If every factor
is stale, the original prior and source lineage are retained. A direct dirty
frontier updates current upstream state and propagates only when the declared
downstream threshold is exceeded, so downstream nodes consume newly computed
state. Only the affected exact output region is recomputed.

The public tests in `packages/rail-py/tests/test_belief_inference.py` cover the
hand-verifiable posterior, numerical stability, endpoint behavior, incompatible
semantics, duplicate-evidence rejection, exact derivation/replay, stale-factor
removal, contradiction aggregation, unrelated-region exclusion, all-stale
prior retention, previous-posterior reversion, multi-hop propagation,
insertion-order-invariant diamond propagation, threshold suppression,
semantic/range validation, and cycle rejection. The public
`DomainExtensionRegistry` journey dispatches the versioned posterior operator
through the existing authorizer seam; proposed operators and evidence remain
subject to the existing candidate/review/promotion path.

The integrated public journey is `BeliefProjectService`. It stores candidate,
review/promotion, and derived belief records through the existing durable
`TemporalProjectionService`, so a restarted service rebuilds from canonical
temporal history. The registry operator consumes the actual prior and evidence
payload values and aligned factor IDs; config cannot substitute an unrelated
ratio array. The journey test covers candidate review, initial posterior,
restart replay, permission filtering, and tombstoning one of two evidence
records. After the tombstone, only the remaining authorized factor contributes
and the derived output is recomputed with its original prior and retained
lineage. Unrelated exact regions remain absent from the run.
The service resolves records through the projection's bitemporal current query:
future, expired, superseded, and review-tombstoned records cannot drive a
posterior. A current lineage authorizer is mandatory and covers the approved
review, candidate, prior, factor, and every exact evidence ref before inference
or derived persistence. Dirty scheduling compares the current factor inputs
with the persisted derived lineage, so an unchanged restart has no affected
outputs while a revoked input reaches only its dependent belief. Exact
`DerivationRecord` payloads are persisted as canonical temporal rows, allowing
restart inspection through `authorized_explanations` rather than retaining
only IDs. Expired/future historical inputs remain explicitly stale so a
two-factor posterior can fall back to the remaining factor and prior; a future
candidate itself remains unavailable. Derived revision numbers use the maximum
numeric revision across canonical history, and each write rechecks complete
live lineage before persistence.

Focused validation for this pass: `pytest packages/rail-py/tests/test_belief_inference.py packages/rail-py/tests/test_temporal_records.py packages/rail-py/tests/test_procedure_projection.py -q` => **42 passed in 0.35s**; the inference file alone is **15 passed**. This includes atomic batch rollback, full-factor identity, public upstream/downstream propagation, restart, unrelated-branch exclusion, and exact derived-row denial. `compileall` and `git diff --check` also pass.

Root's before-fix reproduction passed the first LR(3) run (`p=.2` to `3/7`),
then supplied that posterior as current state during an all-stale rerun. The
old engine emitted no output and classified the target unchanged. The
regression now passes an explicit `previous_states` map and proves that the
second run emits the original `.2` prior and marks the output recomputed. A
separate all-stale case proves the original source lineage is retained rather
than manufacturing an empty source list.

The mathematical update follows MIT 18.05's odds and likelihood-ratio
formulation: [MIT 18.05 Lecture 12](https://ocw.mit.edu/courses/18-05-introduction-to-probability-and-statistics-spring-2022/mit18_05_s22_lec12.pdf).
The stable real-valued log/sigmoid primitives follow the numerical guidance in
[Stan's Functions Reference](https://mc-stan.org/docs/functions-reference/real-valued_basic_functions.html).

This is a local deterministic inference primitive, not a general Bayesian
network, learned model, calibrated claim migration, prediction system, or
automatic trusted-knowledge promotion path. Proposed factors and edges still
require the existing explicit candidate/review/promotion workflow.
