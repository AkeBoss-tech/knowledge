# OpenSaddle provenance consumption v1

`rail.core_provenance` consumes the exact command and environment references
emitted by Core as evidence. The adapter accepts the existing KRAIL
`ResourceRef` shape with canonical issuer `opensaddle://core`:

- command: `resource_type=command`, `resource_id` beginning
  `opensaddle/command/`, immutable command version, and the descriptor digest;
- environment: `resource_type=environment`, `resource_id` beginning
  `opensaddle/environment-revision/`, immutable revision, and the definition
  digest;
- receipt: a Core schema/version, stable `receipt_id`, timezone-qualified
  observation time, and a digest over the complete receipt.

`CoreProvenanceService.ingest` requires a caller-owned authenticated Core trust
boundary, authorizes both exact refs at the current clock time before creating
a proposed `ProcedureRecord`, rechecks both refs at a fresh current clock
before exposure, and emits
the composed `TemporalRecord` through `procedure_temporal_record`. The record
is configuration provenance evidence only: it does not execute the command,
claim activation, auto-review a procedure, or mark verification true. Core's
`activation_observed=false` and `execution_verified=false` values are retained
as schema constraints.

Receipt identifiers are idempotent within one service instance. Replaying the
same identifier with a different digest is rejected as a conflicting replay.
Missing or partial command/environment evidence is rejected explicitly rather
than represented as complete evidence. Authorization remains caller-owned and
revocation is checked at both boundaries.

When given the project-scoped local repository, the service persists immutable
receipt/procedure pairs as bounded `core_provenance` rows in the canonical
`.krail/semantic.json` authority. The established semantic store supplies
scoped locking, CAS, fsync, and atomic replace; temporal records are rebuilt
from the validated procedure on reload. Reload revalidates receipt and
procedure digests, binds every procedure field back to its receipt, and
rechecks caller authorization. Concurrent repository instances serialize and
reload under the canonical store lock. Reads refresh stale store instances
from the committed file. Cross-process coordination uses a POSIX advisory
`.lock` file; lock release on process exit is provided by the operating
system. Same-store readers from another thread block behind an active
transaction and cannot observe uncommitted rows; the transaction owner may
read its own uncommitted state. Nested transactions fail immediately. Hosted
persistence is not claimed, and no independent database is introduced.

`ProcedureReviewService` provides the explicit review boundary over these
proposed procedures. It persists accepted or rejected
`ProcedureReviewDecision` rows in the same canonical semantic store, binds
accepted promotion to the exact candidate digest and reviewer/evidence refs,
and performs live authorization before and after review construction. It does
not activate or execute the promoted procedure. Callers must supply an
authenticated `ProcedureReviewAuthorizer` for the review action in addition to
the evidence-read authorizer; a readable reviewer reference is not an identity
or promotion grant. Reload and replay rederive the promotion from the exact
candidate and decision, rejecting independent valid substitutions.
For hosted callers, `HostedProcedureReviewAuthorizer` verifies the signed
`procedure.review` action, versioned `krail.procedure-review` capability,
tenant/project scope, reviewer subject binding, exact candidate/evidence
sources, and live revocation on every call.

`ProcedureExplanationService` is the corresponding read-only history query.
It uses the same repository and a live `ContextAuthorizer` to return exact
candidate, reviewed, evidence, dependency, reviewer, and decision refs plus
rationale, lifecycle, activation, supersession, and bounded support status.
It reauthorizes the full source and derived lineage before return, including
the generated review ref; signed `context.read` is sufficient and
`procedure.review` is not required. Missing reviews, stale records, and
conflicting decisions are surfaced as explicit gaps, and no read creates a
review, activation, cache, or new row. The public contract is advertised as
the versioned read-only `krail.procedure-explanation` capability.

Dependency change handling is a rebuildable projection in the same semantic
store. `record_invalidation` persists an immutable exact-ref event, and
`rebuild_freshness_projection` derives `procedure_freshness` rows for affected
candidate/reviewed records. Replays are idempotent and deterministic by event
time, ID, and digest; source procedure and review rows are never rewritten.
Explanation reads include and authorize the exact invalidation refs. This
local projection covers declared exact lineage and supersession; it does not
claim a broader dependency graph.

Invalidation admission is a separate signed action boundary. Callers must
present the additive `krail.procedure-invalidation` `1.0.0` capability with
`procedure.invalidate`, exact changed-ref scope, and the event digest; a
read-only `context.read` grant cannot write stale-marking events.


## Actionable reviewed guidance

`ProcedureExplanationService.actionable_guidance(request, authorizer=...)`
returns a `ProcedureActionableGuidance` only for an accepted, currently supported
reviewed procedure. The result binds the candidate and reviewed digests,
procedure identity/version, reviewed rationale, exact evidence refs, and an
optional corroborating projection-state digest. This read does not execute the
procedure or claim that its rationale is independently correct.

The service rechecks exact read authority before exposure. Historical explanation
remains available separately; operational guidance returns `None` when it must
abstain. No structured abstention reason is currently returned.

When composed with `TemporalProjectionService`, eligibility is evaluated against
live canonical valid/known time, competing records, persisted tombstones, dirty
outputs, and exact dependency state. Cached projection rows cannot establish
eligibility or select a winner among conflicting roots. Invalidation, expiry,
new conflicting evidence, and supersession are checked before recomputation and
after restart. Explicit canonical freshness propagates through direct,
transitive, and aliased inputs. Pure supersession lineage is distinct from an
explicit dependency on a predecessor.

The company incident acceptance test persists incident revisions, uses the real
review/promotion service with signed review/read/invalidation authorities,
withholds guidance after source invalidation, and restores a supported revision
against new evidence. Core receipt trust and temporal writer admission in that
test are deterministic fixtures. This is local callable evidence, not a hosted
company-memory product, a published actionable-guidance capability, a UI journey,
or completion of Knowledge #15/#29. The existing explanation capability remains
separate from this new Python read path.
