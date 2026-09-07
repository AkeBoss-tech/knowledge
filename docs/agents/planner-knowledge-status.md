# Knowledge planner status

## #19 feature branch: temporal tabletop world-memory hardening (2026-09-07)

- `TabletopWorldMemory` persists immutable observation and estimate envelopes
  in the existing canonical `JsonSemanticStore`, refreshes a second open
  reader before each query, and keeps physical observation time (`valid_from`)
  separate from the trusted ingestion clock (`recorded_at`). A late physical
  observation is therefore absent from a historical `known_at` query until it
  arrives, while its original effective time remains intact.
- Location answers distinguish last observed, current estimated, expired
  estimate (`stale`), and unknown current location. Authorized stale and
  last-seen answers retain exact source and canonical-record evidence;
  unauthorized callers fail before any status, evidence, class count, or scene
  reference is exposed. Scene construction and class ambiguity recheck every
  exact source and record ref immediately before return.
- The deterministic tabletop episode has two same-class cups, an occlusion and
  unseen move, a bounded-expiry estimate, later reobservation with a new map
  revision, and an hour-boundary-safe timeline. Class lookup filters both
  future and unauthorized objects before deciding ambiguity, and scene records
  must belong to one exact world.
- The real trusted-local registry dispatch registers the world-memory lookup as
  explicitly nondeterministic because it reads a mutable canonical snapshot.
  Handlers may return the reserved `HANDLER_LINEAGE_REFS` channel; dispatch
  removes it from output, authorizes it, and binds every exact consumed ref
  into the immutable invocation lineage. Denied or malformed added refs fail
  closed. This does not make a mutable lookup replayable without the matching
  canonical snapshot.
- Verification on `codex/roadmap-knowledge-followup`:
  `/private/tmp/krail-temporal.zSLchI/bin/python -m pytest -q packages/rail-py/tests/test_robotics_world_memory.py packages/rail-py/tests/test_extension_registry.py packages/rail-py/tests/test_procedure_projection.py packages/rail-py/tests/test_core_provenance.py packages/rail-py/tests/test_authorized_context.py packages/rail-py/tests/test_hosted_authorization.py packages/rail-py/tests/test_procedural_memory.py packages/rail-py/tests/test_temporal_records.py packages/rail-py/tests/test_capability_publication.py --tb=short`
  reports `140 passed`; compileall and `git diff --check` pass.
- Remaining #19 boundary: no signed hosted robotics writer/reader adapter or
  `TemporalProjectionService` materialization exists yet; registry authorization
  is the caller-supplied local seam plus `WorldReader`. No ROS transport,
  perception pipeline, binary asset store, live robot control, identity merge,
  or general world-model engine is claimed.

## Current authoritative slice: durable procedure freshness projection (2026-09-07)

- Added immutable `procedure_invalidation` events and rebuildable
  `procedure_freshness` rows to the existing canonical semantic store.
  `record_invalidation` is idempotent by event digest and rejects conflicting
  event IDs; rebuild sorts events deterministically and matches exact declared
  procedure refs. It never rewrites source ProcedureRecord or review decision
  digests.
- `ProcedureExplanationService` consumes projection freshness, stale reasons,
  and exact invalidation refs under the same live read authorization. Accepted
  guidance changes from current to stale after a command revision event;
  unrelated records remain current. Restart and rebuild restore the same
  explanation digest. Existing graph support only provides exact lineage and
  supersession; no unproven transitive dependency graph was introduced.
- Exact isolated verification command (Python 3.13.12, editable
  `packages/rail-py`):
  `/private/tmp/krail-temporal.zSLchI/bin/pytest -q packages/rail-py/tests/test_core_provenance.py packages/rail-py/tests/test_authorized_context.py packages/rail-py/tests/test_hosted_authorization.py packages/rail-py/tests/test_procedural_memory.py packages/rail-py/tests/test_temporal_records.py packages/rail-py/tests/test_capability_publication.py --tb=short`
  Result: `108 passed`; compileall and `git diff --check` pass. A genuine
  public-boundary red showed an invalidation recorded after rebuild could
  still explain guidance as `current`; `freshness_for` now compares the
  projection's exact invalidation-ref watermark with canonical events and
  rebuilds before returning (or fails closed if it remains out of date).
  Projections preserve more than 16 causes; explanations fail closed above
  their 16-ref/reason bound instead of dropping causes. No commits, push,
  deploy, or tracker mutation performed.
- Freshness writes require the additive signed
  `krail.procedure-invalidation` `1.0.0` capability and
  `procedure.invalidate` action, with exact changed-ref and precomputed event
  digest binding. A `context.read` adapter is rejected at the public writer
  boundary; the existing v1 actions remain compatible.
- Invalidation writes use the existing caller-owned
  `CoreProvenanceAuthorizer` before and after event publication; repository
  possession is not a freshness-writing grant. Direct impact means an event's
  exact `ResourceRef` matches a declared package/command/environment/test or
  dependency ref. Superseded reviewed records inherit matching direct events
  through their copied lineage; no general transitive dependency graph is
  claimed.

### #18 temporal projection implementation boundary

- `rail/procedure_projection.py` now stores immutable `temporal_record`
  history, typed exact input-to-output `procedure_projection_edge` rows,
  rebuildable `procedure_projection_current` rows, durable dirty entries,
  recompute receipts, and deterministic checkpoints in the existing canonical
  `JsonSemanticStore`. A normal change follows `mark_dirty` plus `recompute`;
  full `rebuild` is recovery-only and returns its existing checkpoint when its
  byte-stable content is unchanged.
- A new superseding revision atomically queues its own output and the reverse
  exact-edge region rooted at the exact superseded output. Recompute reads
  dirty rows, records, and tombstones inside one `JsonSemanticStore`
  transaction and only clears the exact consumed dirty revisions. A concurrent
  enqueue waits for that commit then remains pending; repeating the same
  empty recompute returns its existing receipt instead of conflicting.
- Reverse traversal compares only `ResourceRef.exact_key`, never a bare
  digest. The tested three-level dependency chain reaches its indirect
  dependents, while same-digest refs from different authorities remain
  isolated. Recompute clears only affected dirty entries and does not revise
  equal current-state rows. Recompute evaluates only dirty entities and their
  direct temporal inputs, not unrelated entities. Every materialized output
  includes its exact input revision digests and direct dependency states
  (`current`, `dirty`, `stale`, or `missing`); exact external refs remain
  current inputs locally until another explicit invalidation, rather than
  claiming provider freshness resolution.
- Tombstones are immutable events with separate effective and recorded times.
  Current-state reconstruction applies them only when both cutoffs include the
  event, retaining historical rows otherwise. Tombstone writes require the
  existing signed `krail.procedure-invalidation` `1.0.0` /
  `procedure.invalidate` adapter; authorization uses the live clock, so a
  backdated event cannot revive expired authority. `context.read` is rejected.
- Focused proof covers duplicate and late replay convergence, checkpoint
  restart stability, signed exact-write authority, effective/known tombstone
  history, three-level propagation, exact-ref isolation, unchanged unrelated
  output rows, restart after durable dirty marking, automatic transitive
  dirtying after supersession, exact dependency lineage/stale state, no
  unrelated evaluator invocation, concurrent enqueue during recompute, and
  idempotent no-op recompute. JSON-store atomic transactions remain the crash
  boundary; no scheduler, second database, or general dependency engine was
  added.

### Core provenance projection bridge (2026-09-07)

- `CoreProvenanceService` and `ProcedureReviewService` now accept a projection
  only with an explicit paired `ProjectionWriter`; ordinary Core/read
  authorization cannot publish temporal inputs. Reviews are synchronized using
  `procedure_temporal_history`, preserving the exact envelope supersession
  parent as a typed projection edge. Existing receipt/procedure/review digests
  are not rewritten.
- `record_invalidation` may update this non-authoritative projection only after
  its existing signed invalidation action succeeds. `ProcedureExplanationService`
  can read the persisted projection and reports stale support for an authorized
  candidate/review chain with stale or dirty temporal dependencies. It does
  not expose new source identities.
- Added signed `krail.procedure-projection` `1.0.0` / `projection.write`
  adapter support. It binds tenant/project, exact source refs, exact temporal
  record digests, capability digest, and the live clock; `context.read` and an
  expired context are rejected. The action is only typed availability in
  `AccessClaims`, never a default grant.
- Focused integration proves Core ingest, review, exact parent invalidation,
  current-before/stale-after explanation, interrupted invalidation replay, and
  interrupted receipt projection publish use one persisted temporal projection.
  Supersession lineage by itself remains current; only an explicit exact cause
  makes its dependency state stale. Current bounded verification is `128 passed` across projection, Core,
  authorization, hosted, procedural, temporal, capability, and extension
  tests; compileall and `git diff --check` pass.
- Exact current command (isolated Python 3.13.12):
  `/private/tmp/krail-temporal.zSLchI/bin/pytest -q packages/rail-py/tests/test_procedure_projection.py packages/rail-py/tests/test_core_provenance.py packages/rail-py/tests/test_authorized_context.py packages/rail-py/tests/test_hosted_authorization.py packages/rail-py/tests/test_procedural_memory.py packages/rail-py/tests/test_temporal_records.py packages/rail-py/tests/test_capability_publication.py --tb=short`
  Result: `117 passed`; compileall and `git diff --check` pass. No scheduler,
  second database, or remote deployment was added.

## Current authoritative slice: authorized procedure explanation (2026-09-07)

- Added the read-only `ProcedureExplanationService` over the existing
  `CoreProvenanceRepository` and canonical `JsonSemanticStore`. It returns the
  exact candidate, reviewed successor when uniquely accepted, package,
  command, environment, evidence, dependency, reviewer/decision refs,
  rationale, lifecycle, activation state, and supersession explanation. It
  never mutates, reviews, or activates history.
- Every returned source and derived ref is authorized twice through the
  existing live `ContextAuthorizer` seam, including the generated
  `procedure-review` decision ref. Signed readers need `context.read`; they do
  not receive `procedure.review`. A revoked signed delegation blocks replay
  and return. Missing review, stale records, and conflicting accepted/rejected
  decisions are explicit bounded statuses and gaps.
- Lineage assembly is derived from `_procedure_refs` for every returned
  candidate/reviewed record and a complete decision-ref enumerator for every
  returned review decision (reviewer, evidence, and decision row ref). Review
  history beyond the caller's bound fails closed before an explanation is
  formed, so a later contradictory decision cannot be hidden as a false
  `truncated=false` result. Signed-read tests deny omitted reviewer lineage on
  both fresh and restarted reads.
- Published the versioned read-only `krail.procedure-explanation` capability
  with `explain_procedure`, `max_reviews <= 32`, and a 131072-byte response
  bound. No second store, cache, provider-v1 widening, activation, or generic
  explanation layer was added.
- Exact current verification command (isolated Python 3.13.12 runtime with
  editable `packages/rail-py` and pytest):
  `/private/tmp/krail-temporal.zSLchI/bin/pytest -q packages/rail-py/tests/test_core_provenance.py packages/rail-py/tests/test_authorized_context.py packages/rail-py/tests/test_hosted_authorization.py packages/rail-py/tests/test_procedural_memory.py packages/rail-py/tests/test_temporal_records.py packages/rail-py/tests/test_capability_publication.py --tb=short`
  Result: `103 passed`. `python3.13 -m compileall -q packages/rail-py/rail packages/rail-py/tests`
  and `git diff --check` also pass.
- Acceptance proof includes a real signed read context after durable review,
  restart replay with unchanged rows, revoked dependency/delegation denial,
  missing support, conflicting reviews, and no implicit activation. Remaining
  gap: evidence/provider existence is represented by exact persisted refs and
  authorization; this local repository does not claim hosted provider
  freshness resolution beyond record freshness/stale reasons. No commits,
  push, deploy, or tracker mutation performed.

## Current authoritative slice: procedure history conversion (2026-09-07)

- Reproduced the real generic replay failure with two `ProcedureRecord`
  revisions: `procedure_temporal_record` produced payload schema versions
  `1.0.0` and `1.1.0`, so `query_temporal_records` rejected the history with
  `ValueError: temporal replay cannot mix entity or payload identities`.
  The replacement also carried a procedure digest where generic replay
  requires a temporal-envelope digest.
- Added stable procedure payload schema identity (`1.0.0`) and kept each
  procedure instance version in the temporal `revision` field. Added
  `procedure_temporal_history`, which converts a complete validated procedure
  chain and remaps supersession parents to the exact generated envelope
  digests. A standalone superseding conversion now fails closed and directs
  callers to the history adapter; supersession is never silently dropped.
- Behavioral proof: `procedure_temporal_history` plus generic
  `query_temporal_records` returns revision `1.0.0` before the `1.1.0`
  correction is known and `1.1.0` afterward, with exact envelope parent
  linkage preserved.
- Exact isolated verification command:
  `/private/tmp/krail-temporal.zSLchI/bin/pytest -q packages/rail-py/tests/test_procedural_memory.py packages/rail-py/tests/test_temporal_records.py packages/rail-py/tests/test_core_provenance.py --tb=short`
  Result: `35 passed` (Python 3.13.12 venv with editable `packages/rail-py`
  and pytest). Pre-fix behavioral red was the focused history test, failing
  at generic replay with the mixed payload-identity assertion above.
- Remaining gap: callers that need to expose/replay multiple procedure
  revisions must use `procedure_temporal_history`; the Core provenance
  adapter currently ingests one proposed revision at a time and does not
  claim a multi-revision hosted persistence/query integration. No commits,
  push, deploy, or tracker mutation performed.

### Chain correction follow-up (2026-09-07)

- Root reproduced a second genuine boundary failure with three revisions
  (`1.0.0` -> `1.1.0` -> `1.2.0`): the two-revision adapter passed, but the
  third child still pointed at the parent’s unlinked base envelope digest and
  generic replay raised `temporal supersession must reference a supplied exact
  revision`.
- `procedure_temporal_history` now resolves parents recursively to their final
  envelope digests, independent of arrival order or retrospective effective
  time, and fails closed on missing parents or cycles.
- Identical post-fix chain proof passes known-time cutoffs at 12/14/16 and
  returns `1.0.0`, `1.1.0`, then `1.2.0`; the broader focused command now
  reports `36 passed` in the same isolated Python 3.13 environment.

## Current authoritative slice: explicit procedure review/promotion (2026-09-07)

- Ownership contract: `rail.procedural_memory` defines immutable review
  decisions and promotion semantics; `CoreProvenanceRepository` persists
  them as `procedure_review` rows in the existing canonical semantic store;
  `ProcedureReviewService` is the explicit caller-owned review boundary. Core
  remains responsible for activation and runtime effects.
- Added `ProcedureReviewDecision` with exact candidate digest, accepted or
  rejected outcome, reviewer `ResourceRef`, exact evidence refs, recorded
  time, and integrity-bound decision digest. Accepted decisions materialize a
  reviewed `ProcedureRecord`; rejected decisions persist only the decision.
  No ingestion, invocation, digest match, or successful review activates a
  procedure.
- Review authorizes every candidate lineage ref plus reviewer/evidence refs at
  the live clock before decision creation and rechecks them before exposure.
  A stale/unavailable candidate, changed decision payload under one decision
  ID, or conflicting receipt is rejected. Durable reload returns the exact
  decision/promotion and reauthorizes the refs before replay.
- Genuine red/green proof: a revocation after precheck initially allowed
  review exposure; the identical public `ProcedureReviewService.review`
  assertion now fails closed after the post-review authorization recheck.
- Exact isolated verification command:
  `/private/tmp/krail-temporal.zSLchI/bin/pytest -q packages/rail-py/tests/test_procedural_memory.py packages/rail-py/tests/test_temporal_records.py packages/rail-py/tests/test_core_provenance.py --tb=short`
  Result: `38 passed`; Python 3.13.12 venv with editable `packages/rail-py`
  and pytest. Compile and `git diff --check` also pass.
- Remaining acceptance gaps: this bounded service discovers candidates from
  Core provenance rows in the canonical local store; a generic procedure
  repository and hosted multi-review integration remain open. Reviewer
  identity is represented by an exact authorized `ResourceRef`; Core must
  supply the appropriate reviewer authority and policy boundary. No commits,
  push, deploy, or tracker mutation performed.

### Review integrity repair (2026-09-07)

- Repaired the review contract after source review found four boundary gaps:
  replay now reauthorizes the complete promoted/candidate lineage; readable
  evidence access is separate from the required caller-owned authenticated
  `authorize_review` action; final authorization is rechecked after durable
  save; and persisted promotions are rederived from the exact candidate and
  decision on load/save.
- Reviewed procedures are immutable successors with a distinct
  `procedure_version`, `supersedes_digest` pointing to the exact candidate,
  and an exact `review_ref` provenance resource carrying the decision digest.
  This makes accepted candidate -> reviewed history compose through the
  generic temporal chain without losing reviewer decision lineage. Reviewed
  records require a review decision reference; Core desired provenance rejects
  review lineage in its receipt binding.
- Added independent-valid substitution proof: replacing a stored promotion
  with another valid promoted record is rejected because it is not derived
  from the stored candidate and decision. Added authenticated-action denial,
  full-lineage replay revocation, and accepted-history temporal composition
  proofs.
- Exact isolated verification command remains:
  `/private/tmp/krail-temporal.zSLchI/bin/pytest -q packages/rail-py/tests/test_procedural_memory.py packages/rail-py/tests/test_temporal_records.py packages/rail-py/tests/test_core_provenance.py --tb=short`
  Result: `41 passed`; compile and `git diff --check` pass. The caller must
  adapt the existing permissions/IAM seam into `ProcedureReviewAuthorizer`
  rather than treating a caller-provided readable `ResourceRef` as identity.

### Signed review authorization adapter (2026-09-07)

- Added `HostedProcedureReviewAuthorizer` over the existing
  `AccessContextAuthority`/`SignedAccessContext` seam. It requires the
  explicit versioned capability `krail.procedure-review` `1.0.0`, action
  `procedure.review`, exact tenant/project scope, non-wildcard source scope,
  and reviewer `ResourceRef` identity bound to the signed subject and issuer.
  Candidate/evidence lineage must match configured exact refs and signed
  source IDs; read-only `context.read` grants are denied.
- The review service now passes candidate lineage before promotion and the
  union of candidate/promoted lineage on final/replay checks. Internal review
  decision refs remain covered by the full read authorizer and persisted
  derivation checks. Signed context expiry and delegation/context revocation
  are rechecked on each adapter call.
- Integration proof covers successful signed review, read-grant-cannot-review,
  exact scope/action checks, and revoked delegation denial. The expanded
  hosted/context/procedure suite is `82 passed` under the isolated Python
  3.13 environment; compile and `git diff --check` pass. No remote deployment
  or hosted production claim is made.
- The signed adapter also binds the action to server-owned allowed candidate
  digests and a configured capability descriptor digest. Same refs with changed
  candidate rationale and same capability name/version with a different
  descriptor digest are denied by the real signer. Generated internal review
  refs are excluded from the signed source-scope bypass; they remain covered by
  exact derivation and full read authorization.

Updated: 2026-09-07 (after the authorized procedure explanation slice)

## Bounded Antigravity review disposition (2026-09-07)

- Accepted finding: `AuthorizedContextPacket` construction/deserialization now
  recomputes the packet digest from schema version, authorization digest, and
  context. A forged-digest case is rejected by `model_validate`.
- Accepted finding: invocation lineage now binds `output_schema`, and integrity
  verification compares the result operator/version and output schema with the
  registered descriptor before exposing the result.
- Accepted findings: `supersede` and `supersede_temporal_record` now require
  the complete identity used by replay, including authority/writer family and,
  for temporal records, payload schema version. Cross-identity replacements
  are rejected before they can create replay-incompatible histories.
- Rejected finding: backward effective-time supersession is not a defect. The
  temporal model permits retrospective corrections, and replay orders records
  deterministically without claiming to project current state. No monotonic
  `valid_from` rule was added; exact parent lineage and recorded time remain
  the relevant integrity controls.

Review evidence: the bounded report supplied red reproductions for the four
accepted cases. After the fixes, the focused Knowledge suite is green at
`26 passed`; changed modules compile and `git diff --check` passes. No commits,
pushes, deploys, or issue mutations were performed.

## Core provenance consumption slice (2026-09-07)

- Added `rail.core_provenance` as a narrow adapter for Core's exact command and
  environment `ResourceRef`s. It validates the agreed canonical
  `opensaddle://core` issuer, resource type/id shape, immutable versions,
  receipt digest, and complete evidence state without widening provider-v1.
- Ingestion requires a caller-owned authenticated Core trust boundary,
  authorizes both refs at the live current clock before creating a proposed
  procedure, rechecks both refs before exposure, and composes the result
  through the existing `ProcedureRecord`/`TemporalRecord` path. It remains
  configuration evidence only: no execution, activation, review, or
  verification is inferred.
- Stable receipt IDs are idempotent within the service instance; a same-ID,
  different-digest replay is rejected. Partial or missing command/environment
  evidence is rejected explicitly.
- Added the durable local repository path through the existing canonical
  `JsonSemanticStore` at project-managed `.krail/semantic.json`, using a
  bounded `core_provenance` record kind. It persists receipt/procedure pairs
  with atomic fsync/replace and rebuilds temporal output on reload; hosted
  persistence is not claimed.
- Behavioral red/green proof covers live-time authorization denial for an
  expired/revoked-now grant, authenticated-origin rejection, authorization
  recheck, idempotent and conflicting receipt replay, exact refs, and proposed
  (never activated) procedure semantics. Restart/reload, current authorization
  denial after reload, and interrupted atomic publication preserving the prior
  record are also covered. Ingest time is recorded separately from Core's
  observed effective time; final auth uses a refreshed clock; reload rejects
  independently valid but receipt-unbound records; and two repository
  instances are covered for no-lost-update behavior. A real two-process race,
  stale-instance read refresh, and process-crash lock release are covered.
  Cross-process safety uses a scoped POSIX advisory lock file around reload and
  atomic publication; Windows support is not claimed. Same-store readers are
  blocked during another thread's transaction, rollback remains invisible,
  and nested transactions fail without hanging. The local persistence slice
  is `16 passed`; canonical semantic/repository regressions and the
  broader focused suite remain green.

## Minimum extension registry slice

- Added `rail.extension_registry.DomainExtensionRegistry` with explicit trusted
  local handler registration, typed schema/operator discovery, duplicate and
  schema/version collision rejection, deterministic operator/config/lineage
  digests, and caller-inherited authorization for every exact input ref.
- Proved independent robotics and company validators through the same generic
  dispatch path. Negative tests cover unknown versions, duplicate versions,
  schema collisions, denied inputs before handler execution, and untrusted or
  non-callable registration.
- This is a trusted in-process registry only. It does not dynamically import,
  eval, install, sandbox, activate, grant, or schedule third-party code.
- Test-first hardening captured two real red cases before production edits:
  authorization revoked between precheck and exposure, and missing output
  lineage digest. The fix adds a post-handler authorization recheck plus output
  and lineage integrity verification. Identical focused proof is now green.

## Shared temporal substrate slice

- Added `rail.temporal_records` as the minimum additive #16 envelope:
  authority-qualified entity identity, payload schema/version, explicit
  observation/estimate/claim/approved/hypothesis/proposed distinctions,
  effective-versus-recorded time, exact source/provenance refs, revision,
  freshness/visibility, and immutable digest.
- Added deterministic out-of-order replay and exact supersession helpers.
- Adapted `ProcedureRecord` through `procedure_temporal_record` composition,
  preserving its existing procedure model and authorization checks.
- Added robotics and company fixture records to prove domain-neutral semantics.
  The #21 minimum trusted-local registry now composes with this envelope;
  sandboxed/third-party installation and domain payload execution remain
  deferred.
- Core exact refs: command `opensaddle/command/<id>@1` with its emitted
  descriptor digest; environment `opensaddle/environment-revision/<project>`
  with revision string and emitted definition digest. The bounded
  Core-to-KRAIL proposed-provenance adapter is documented in the later Core
  provenance slice; this paragraph does not claim activation or verification.

Checks for this slice: isolated Python 3.13 environment, temporal/procedure
tests pass; `query_temporal_records` now proves effective-versus-known-time
corrections, correction chains, half-open validity boundaries, ingested-time
precedence, and validation of future records. It remains a bounded history
query rather than a full bitemporal engine; tombstones and materialized
current-state projections remain open.

## Procedural-memory hardening history

- Before the shared substrate slice, audit found no typed temporal/procedure
  envelope. Existing
  `ResourceRef`, `CommandDescriptor`, `VersionedMetadata`, verification
  evidence, and outcome supersession primitives were reused.
- Added `rail.procedural_memory` with immutable exact-revision procedure records,
  bitemporal timestamps, explicit desired/observed/reviewed lifecycle,
  package/command/environment/test refs, rationale, dependency lineage,
  supersession, and selective idempotent stale invalidation.
- Added `docs/procedural-memory.md` and five focused tests covering exact
  digests, lifecycle separation, selective invalidation, supersession, and
  invalid time/duplicate lineage rejection.
- Acceptance hardening added deterministic out-of-order replay and a
  fail-closed `authorize_procedure` gate covering every package, command,
  environment, test, dependency, and activation ref before metadata exposure.
  Denials do not disclose hidden refs or rationale.
- Audit remains explicit: the minimum typed temporal envelope from #16 now
  exists, but a full bitemporal query engine and domain-specific payload
  validation are not claimed. The broader #21 registry surface remains
  deferred beyond trusted-local registration.
- Hardened shallow-frozen payloads with digest revalidation at replay,
  supersession, procedure authorization, invalidation, and composition
  boundaries. Creation now normalizes typed inputs before digesting; conflicting
  authority/writer or same-revision/different-digest histories are rejected.

Checks for this slice: isolated Python 3.13 test environment, `7 passed` for
`test_procedural_memory.py`; compile and diff checks rerun below.

## Completed slice

- Audited the existing provider-v1, context-brief, hosted authorization,
  semantic authorization, projection, fixture, and test boundaries.
- Added an injected live `ContextAuthorizer` path to Context Brief assembly.
  It filters unauthorized search candidates before ranking/output, checks
  authorization before each exact read, and rechecks all selected refs before
  returning.
- Added `AuthorizedContextPacket` and `HostedAccessContextAuthorizer`. The
  packet binds the existing brief to the current authorization digest and
  fails closed on context/delegation revocation, source-scope denial, exact
  version/digest mismatch, or missing `context.read` action.
- Kept KRAIL as evidence/context authority. Identity, grants, credentials,
  scheduler, runs, artifacts, approvals, and operational event ownership stay
  outside this repository.

## Changed files

- `packages/rail-py/rail/context_brief.py`
- `packages/rail-py/rail/authorized_context.py`
- `packages/rail-py/rail/hosted/access.py`
- `packages/rail-py/tests/test_authorized_context.py`
- `packages/rail-py/rail/procedural_memory.py`
- `packages/rail-py/rail/temporal_records.py`
- `packages/rail-py/rail/extension_registry.py`
- `packages/rail-py/tests/test_procedural_memory.py`
- `packages/rail-py/tests/test_temporal_records.py`
- `packages/rail-py/tests/test_extension_registry.py`
- `docs/authorized-context.md`
- `docs/procedural-memory.md`
- `docs/extension-registry.md`

## Checks

- `python3.13 -m compileall` passed for the changed Python modules.
- `git diff --check` passed.
- An isolated temporary Python 3.13 environment installed the dependencies from
  `packages/rail-py/pyproject.toml`; focused authorization/context/access/
  temporal/procedure/extension verification passed: `73 passed` across
  `test_authorized_context.py`, `test_context_brief.py`,
  `test_context_brief_vertical.py`, and `test_hosted_authorization.py`.
  Final rerun after registry revocation/lineage hardening: `73 passed`.
- The machine's default Python 3.10 and pre-existing Python 3.13 environments
  remain unsuitable for this suite (`datetime.UTC`/missing pytest); no global
  environment or dependency file was changed.

## Blockers and next slice

- Core and Interface still need one shared fixture carrying KRAIL
  `ContextBrief.evidence.packet_id`, `brief_digest`, and exact `ResourceRef`
  bindings beside OpenSaddle-owned Project/Run/event/artifact/approval refs.
- Broader provider/projection regression coverage can run in the same isolated
  environment if Core requests it. No commit, push, deploy, or issue mutation
  was performed.
