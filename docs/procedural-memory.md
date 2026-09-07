# Procedural memory v1

`rail.procedural_memory` stores evidence-backed environment procedures as
immutable records. A record binds exact `ResourceRef` revisions for packages,
commands, environments, tests, and dependencies, along with rationale,
bitemporal timestamps, writer/authority metadata, and a digest.

Lifecycle is explicit: `desired` describes configuration intent,
`observed_activation` carries an exact activation reference supplied by the
runtime, and `reviewed` requires exact test evidence. KRAIL stores these facts;
OpenSaddle owns grants, effective activation, scheduling, and execution.

`supersede` requires the exact prior record digest. `invalidate_for_dependency`
marks only records that declare the changed exact revision as stale and is
idempotent. `replay_procedure_history` sorts out-of-order revisions by effective
time, recorded time, version, and digest, while requiring every supersession
parent to be present. `authorize_procedure` checks every package, command,
environment, test, dependency, and activation reference before returning any
procedure metadata; denial details are opaque.

`ProcedureReviewDecision` records an explicit accepted or rejected review for
one exact candidate digest, reviewer reference, and evidence set. The
canonical Core provenance adapter persists these decisions in the existing
semantic store. Accepted decisions create a reviewed procedure with the exact
review evidence; rejected decisions never create a promoted procedure.
Review authorization is checked before creation and again before exposure,
and decision IDs are idempotent only for the same exact decision payload.
The review action itself is caller-authorized through the existing permission
or IAM seam; a readable reviewer reference is only lineage and cannot grant
promotion authority. Accepted procedures are distinct superseding revisions
with an exact review provenance reference, so they remain replayable history.

`ProcedureExplanationService` reads that persisted candidate/review history
through the canonical Core store. Its bounded `krail.procedure-explanation`
result retains exact refs and digests, separates review state from runtime
activation, explains supersession, and reports missing, stale, or conflicting
support. Every ref is authorized at read time and again before exposure;
revoked readers receive no cached or replayed explanation.

Dependency invalidation is represented separately from immutable procedure
history. Exact invalidation events and rebuildable freshness projections mark
affected guidance stale without changing the ProcedureRecord or review
decision digest. Replaying the same event is idempotent, conflicting event
IDs fail closed, and explanation reads expose the exact invalidation refs and
reasons under live authorization.

The bounded #18 temporal projection lives in `rail.procedure_projection`.
It ingests immutable `TemporalRecord` history into the canonical semantic
store, records typed exact input-to-output dependency edges, and materializes
rebuildable current-state rows using separate `valid_at` and `known_at`
cutoffs. A superseding revision atomically dirties its own output and the
reverse exact-edge region. Durable dirty entries drive bounded recomputation:
it evaluates the dirty entities and their direct temporal inputs, carries exact
input revision identities plus current/dirty/stale state into each output, and
does not replay unrelated entities. The JSON-store transaction snapshots and
clears the consumed dirty revisions together, so a concurrent later enqueue
remains pending. Equal output rows keep their revision.
Tombstones are immutable effective-time/known-time events and require the
existing signed `procedure.invalidate` action, never a `context.read` grant.
Checkpoints, dirty entries, recompute receipts, and current rows are all
replayable derived state. Exact external input refs are retained but their
provider freshness is not resolved by this local projection. This does not
replace temporal history or claim a general dependency scheduler.

Core provenance can opt into this projection only by supplying both the shared
projection and an explicit projection writer. `CoreProvenanceService` ingests
its temporal envelope, and `ProcedureReviewService` uses
`procedure_temporal_history` so a reviewed successor depends on the exact
parent envelope. The existing signed `procedure.invalidate` write may then
mark and recompute that derived region. `ProcedureExplanationService` reads
the persisted derived state under its existing exact read checks and reports
stale support when an authorized candidate/review chain has stale or dirty
temporal dependencies. A read grant never becomes projection-write authority.
Supersession itself is lineage, not staleness: a reviewed successor is current
until an explicit exact invalidation reaches that lineage. Replaying a durable
Core receipt or invalidation repairs an interrupted disposable projection
publish before returning idempotently.

The minimum typed temporal-record envelope from #16 now exists and this record
composes into it. `procedure_temporal_history` converts a complete procedure
chain, preserving procedure revisions and translating each procedure digest
parent into the corresponding temporal envelope digest. A full bitemporal
query engine and executable domain extension registry (#21) remain epic scope;
payload operators are not inferred or executed by this module.
