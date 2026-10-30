# Observed OpenSaddle Run artifacts

`rail.opensaddle_run_artifact.OpenSaddleRunArtifactSource` reads a completed
generic-agent Run through OpenSaddle Core's human bearer-authenticated Run,
agent-result review, and artifact-content endpoints. Configure the exact Core
origin, current human bearer, stable local installation authority, and Project
ID in the trusted local process. The bearer is kept in memory; evidence stores
no token, locator, result body, or HTTP origin.

This adapter requires a supported generic-agent Core result-review route.
SQLite supports it; PostgreSQL support is scoped to hosted-agent lineage and
does not cover ordinary Project Goal Runs. It does not provision an identity,
create a Run, or call a provider.

`record_observed_run_artifact(run_id, source=..., authorizer=...,
input_refs=(...))` binds the current final-lease artifact ID to a SHA-256 of
the original bytes, up to 1 MiB. The source checks the Run and result target
before and after its bounded content read. On `read_exact`, the repository
repeats the Core calls and current KRAIL authorization. Caller-supplied input
refs are declarations; the caller must use an authorizer that checks each
current source. The Core result-review endpoint itself enforces current Run
membership, policy, and protected packet availability. It does not require a
positive human result decision: this receipt is historical observation only.
The `core_run_updated_at` field reports Core's Run update timestamp, not the
time the KRAIL observer first saw it; the durable KRAIL row's `created_at`
records that separate event.

`add_observed_run_artifact_to_procedure_candidate` creates a new `desired`
procedure version citing the evidence, input, Run, and artifact refs. It clears
earlier test, review, and activation refs. Read with
`RunArtifactAwareProcedureAuthorizer` so the stored observation is reopened
under current Core access; independent procedure verification and human review
remain necessary before promotion. KRAIL's existing v1 nondeterministic
invocation contract, canonical-JSON output digest, and dispatcher do not change.

`ObservedProcedureCandidateService.propose` persists one such desired
candidate in the same project-scoped semantic store as its observation and
canonical procedure review history. It takes an exact accepted review decision
for a Core-receipt predecessor, not a caller-supplied predecessor record. The
new row stores the derived candidate, predecessor decision ID, and observed
evidence ref. Reload recomputes the candidate from those canonical rows and
rejects substitution. This first version accepts one observed successor of a
Core-receipt reviewed procedure; it does not recursively import an unlimited
chain of observed candidates.

Construct `CoreProvenanceRepository` with a current `observed_source` and
`observed_authorizer` to review or explain these candidates. Unconfigured
repositories fail closed for this row kind. Candidate lookup and final release
reopen the Core artifact and reauthorize declared inputs; generic read grants
for the saved evidence ref alone cannot turn it into standing access. Only the
existing signed `procedure.review` action can promote it. Existing invalidation
events mark dependent source, package, or environment revisions stale without
rewriting the accepted candidate and review history. A later Core revert must
be represented by a new exact Core environment revision and separately reviewed
candidate; KRAIL never selects or activates an environment on its own.
An explanation performs a current observation at entry and again before
release. Its bounded review rows are rederived structurally within that one
read, rather than downloading the same Core artifact once per review. Guidance
may make additional entry/release checks; no observation is cached across
public calls. Projection rebuild may validate lineage without a live Core read,
but it cannot release a procedure or guidance.

OpenSaddle's native worker result API currently accepts UTF-8 text. The KRAIL
receipt still models the returned body as opaque bytes because it hashes the
actual Core artifact representation rather than reconstructing a JSON output.
Core's artifact endpoint supplies `application/octet-stream`; no provider media
type or provider binary identity is attested here. The configured installation
authority and exact input refs are trusted-local declarations, not signatures.
The Core read and KRAIL write are separate transactions; a concurrent
revocation may require a later read to observe denial. No model execution or
external provider claim is made by this bridge.

## Retained native-artifact qualification

The [pinned HTTP receipt](testing/receipts/observed-candidate-pinned-http-20260920.json)
records a separate read of a retained real Codex result through a copied Core
runtime. The observation and derived candidate survived KRAIL reopen and
returned guidance after explicit signed fixture reviews. An invalid Core bearer
then denied guidance. The runtime source and original-state preservation are
hash checked. The predecessor and reviews remain fixtures: this is not actual
human acceptance, production input-grant qualification, or Core activation.

## Durable workflow consumers

`ObservedRunArtifactRepository.observe` combines current-authority observation
and idempotent retention. `recall` reads a bounded tuple of exact evidence refs
and revalidates source bytes and authorization before returning the set. A
consumer can therefore retain its own execution checkpoint separately: replaying
an observation after a checkpoint failure does not duplicate the evidence row.

These methods are domain-independent: a native analysis result and a software
artifact have the same opaque provenance semantics. They do not certify an
outcome, approve work, schedule an agent or copy artifact byte authority into
KRAIL. OpenSaddle's persistent project agent uses these methods between steps;
other consumers may use the same repository without importing OpenSaddle.

`test_workflow_observe_replay_and_recall_revalidate_authority` in
`packages/rail-py/tests/test_observed_run_artifact.py` verifies reopen/replay, exact
recall, revocation and changed-byte rejection under K21-OPAQUE-RUN-ARTIFACT.

## Pinning an actual accepted Core result review

`rail.opensaddle_reviewed_run_artifact.OpenSaddleReviewedRunArtifactSource`
adds a read-only opt-in boundary over the existing Core HTTP adapter. Its `pin`
factory requires the exact Project/Run, observer subject, review ID/revision,
artifact ID and artifact digest the caller inspected. It checks Core's
`/api/v2/capabilities` authenticated subject and the real Run/result-review
responses; a completed but unreviewed or rejected result cannot be pinned.
The configured origin follows the existing HTTPS or loopback-HTTP policy, with
redirects disabled and bounded response reads.

The frozen `AcceptedCoreResultReviewBinding` contains selectors and an exact
`agent-result-review` ResourceRef. The ref hashes immutable review metadata and
the note digest. It is not a portable signed Core receipt, standing grant or
outcome certification. Reconstructing a source from that binding still reads
Core on every observation: subject, Project/Run, final artifact and accepted
current review must match. Replacing a review with another accepted decision
also invalidates the older binding. Bearers, HTTP origins, notes and artifact
bytes are not written into KRAIL evidence.

Use `source.observe(repository, authorizer=live_input_authorizer, at=...,
input_refs=(...))` to retain ordinary opaque Run evidence with that exact review
ref added to its dependencies. Use `source.authorizer(live_input_authorizer)`
when reopening the observation or configuring `CoreProvenanceRepository`.
The wrapper checks the pinned review dependency against live Core and delegates
all other exact-input checks to the supplied current authorizer. It does not
mint source grants. Protected Core outputs retain their existing packet-access
checks; declared inputs outside such a packet still require the caller's real
source authorizer.

The same source implements the existing `CoreProvenanceTrust.verify(receipt)`
interface for its configured Project. It reads only Core's fixed authenticated
provenance-observation endpoint, with exact command/environment/invocation
selectors, and compares the entire returned typed receipt. An internally
consistent caller-created digest is insufficient. Pinned observer and accepted
review checks bracket that read. Core's configuration-only
`execution_verified=false` and `activation_observed=false` values remain intact;
this supplies an authenticated transport trust boundary, not a portable signature.

The raw observation remains **`caller_observed_unverified`**, with provider
metadata not attested. The separate authenticated fact is that the configured
Core currently reports an exact human-accepted result review. Neither that
review nor a read grant is a `procedure.review` permission. Existing
`ObservedProcedureCandidateService` still creates only a `desired` successor
of an explicitly reviewed Core-configuration predecessor. Existing exact-version
procedure review, verification evidence and signed review authorization remain
necessary for promotion. No command is installed, activated or scheduled by
this adapter, and no financial or business-impact claim is inferred.

The real HTTP integration tests in
`packages/rail-py/tests/test_opensaddle_reviewed_run_artifact.py` use a disposable
Core with bearer-authenticated fixture identity, a reviewed Git source, an actual
invoice recipe result and canonical result-review writes. The predecessor is
obtained from Core's registered command/environment and provenance APIs; review
permissions and judgments remain explicit fixture decisions, not real user
acceptance. Tests cover changed/rejected reviews, changed account/Project,
invalid bearer, repository reopening, source withdrawal and a review rejection
injected after artifact delivery but before the observer's final checks.

Core and KRAIL reads/writes are separate transactions. Rechecking before and
after the bounded read cannot make the entire HTTP sequence linearizable or
retract bytes already returned before a later revocation. The adapter does not
claim provider-side erasure, production qualification or independently verified
model correctness.

The October 29 local owning run passed all 10 tests across the new adapter,
existing opaque observation and observed-procedure candidate suites, with no
skips. The full-flow test uses the production adapter as `CoreProvenanceTrust`
and rejects a self-consistent forged receipt before accepting Core's actual
receipt. The run took about 282 seconds: repeated live authorization checks are
intentional, and this is not an interactive-latency qualification. Logs remain
in the integration task's ignored Knowledge evidence directory. A wheel built
before this module existed must be rebuilt and separately qualified; this
source result does not publish a package or activate a consumer.

October29 installed-artifact qualification: all four reviewed-adapter HTTP
checks passed with SDK `1.2.0rc3`, Core `1.2.0rc6` and MCP `1.2.0rc3` in a new
isolated environment. Before/after origin checks kept SDK/Core/MCP implementations
inside site-packages; source files supplied only the test harness. The broader
source/wheel/installed parity and retained-context lifecycle check also passed.
These unpublished candidate wheels are separate from the currently public
release lane; see K-REVIEWED-RUN-PROCEDURE-1 for qualification limits.

## Exact human acceptance of an ordinary Project Goal result

`rail.opensaddle_goal_run_artifact.OpenSaddleGoalReviewedRunArtifactSource`
subclasses the reviewed-result adapter for ordinary Project Goal Runs. Its `pin`
requires the Goal ID, Run ID, authenticated observer subject, exact accepted Goal
revision, artifact ID and SHA-256. Core must have recorded
`opensaddle.project-goal-result-review.v1` through the existing Goal control
command with all three explicit selectors: `artifact_id`,
`expected_artifact_digest`, and `expected_lease_epoch`. Legacy Run-only
`accept_result` remains lower assurance and cannot supply this binding.

Core's exact review binds the final worker publication and later completion
sequences, assigned worker/current lease, retained artifact bytes, human actor,
review time, actual terminal Run update timestamp, and committed Goal revision.
This establishes which bytes the authenticated human accepted; it does not prove
business correctness or financial impact. An independent result verifier and
separate signed exact-version procedure review remain necessary.

The source uses only capabilities, the existing passive Goal audit, and exact
artifact-content GETs. It does not call Goal GET, digest, wake, Run GET or
work-execution. Audit pages have a 1,000,000-byte transport limit, at most 100
items per page and 32 pages per observation, with bounded deadlines. Histories
outside those limits fail closed. The original acceptance event's sequence,
revision and snapshot digest are pinned in a `goal-result-review` ResourceRef;
the latest audit snapshot must retain the same accepted exact review. Initial
pinning scans the bounded history from zero. A reconstructed pinned source starts
at `audit_sequence - 1`, re-reads the original acceptance event itself, and
requires its exact sequence, revision and snapshot digest before scanning all
later snapshots. It never caches authority or skips the current retained review.
Missing/reordered acceptance events, changed snapshots, invalid cursors and
replacement reviews deny access. Current
Core membership, selected source/context authority and observer identity are
rechecked on each read and before/after byte release. The artifact-content
handler internally uses Core's existing Run lookup; deadline reconciliation has
no eligible row for this already-terminal completed Run. This is not a general
guarantee that every Core artifact endpoint is transactionally read-only.

Use `source.observe(...)`, `source.authorizer(live_inputs)`, the existing
`ObservedProcedureCandidateService`, and the existing signed
`ProcedureReviewService`. Reopening the semantic store and reconstructed frozen
binding still requires live Core access. Raw evidence remains
`caller_observed_unverified`, provider metadata remains `not_attested`, and the
successor remains `desired` until separate explicit promotion. There is no
activation or scheduler permission. HTTP observations are not atomic; a
revocation after release cannot retract bytes already delivered.

`test_opensaddle_goal_run_artifact.py` qualifies a synthetic ordinary Goal with
real PostgreSQL, service tick, enrolled worker, macOS Seatbelt execution,
authorized artifact download, independent reconciliation CLI verification,
explicit exact human fixture acceptance, HTTP audit binding, evidence retention,
desired successor, signed fixture promotion, reopen and source-change denial.
Two variants cover genuine configuration predecessors. The **same hosted
PostgreSQL deployment** variant registers the command and environment through
Core's actual configuration API, then uses the Goal source's production
`verify` facet to compare the receipt against that same authenticated origin.
The receipt remains configuration-only: `execution_verified` and
`activation_observed` are false. Registering that configuration after a completed
Goal does not establish that it dispatched the Goal. Invocation-ID provenance
is outside this PostgreSQL qualification.

The **mixed-deployment** variant uses a genuinely registered configuration from
a separate local Core deployment, with its own current production trust adapter,
distinct installation authority and explicit exact-reference delegation into the
KRAIL Project. It does not claim that local configuration dispatched or configures
the PostgreSQL Goal. Both variants independently execute the real sandbox Goal,
verify downloaded bytes, retain exact accepted-review evidence, require signed
promotion, reopen the store, and deny guidance when selected source authority
changes. These are local synthetic qualifications, not deployed product or actual
user acceptance claims.

A local real-HTTP performance qualification built 80 actual pause/resume commands
before accepting a synthetic sandbox Goal. Repeated identification downloaded
1,082,285 audit bytes in two pages before the pinned cursor change and 18,486
bytes in one page afterward (98.3% fewer audit bytes). Reopened guidance retained
all 31 live review checks while reducing audit pages from 62 to 31 and bytes from
33,550,835 to 573,066. Observed wall time fell from 2.52s to 0.92s in that run;
these are local observations, not deployment latency guarantees. The initial pin
still performs its full bounded scan. The same real journey checks membership
revocation and selected source change; HTTP response fault cases cover missing,
changed or reordered pinned events, invalid cursors and a replaced retained
review. Bounds and live authorization checks are unchanged.

### Local exact Goal evidence command

`python -m rail.goal_evidence_cli capture` and `recall` are owner-operated,
loopback-only adapters to the exact accepted Goal review source and existing
project-scoped observation repository. Each invocation requires the existing
Core owner bearer in an owner-only regular file (`--token-file`), an explicit
`--core-origin` using numeric loopback and port, `--authority`, `--project-id`,
`--goal-id`, `--run-id`, `--observer`, `--review-revision`, `--artifact-id`,
`--artifact-digest`, `--tenant`, and `--store`. Recall also requires the exact
`--evidence-digest` returned by capture. Both `--trust-local-core` and `--dedicated-short-lived-credential` must be supplied explicitly. These are operator assertions: loopback does not authenticate the server or prove credential lifetime. Only use a dedicated short-lived bearer for a listener whose ownership the operator has verified; a rogue loopback listener can receive it. The store directory must already exist, be owner-only and have no symlink path, and existing store and lock files must be owner-only regular files. These checks protect against other local users, not a malicious process running as the same user. Tokens never appear in arguments or stored evidence. The command does not create a Core credential or Goal grant.

Capture returns an exact evidence reference; recall rechecks the live Core
identity, accepted review, membership, source and artifact and returns bounded
base64 opaque bytes. Output remains `caller_observed_unverified`: human review
is not proof of business truth. The caller is responsible for protecting recall
output and the selected local store. Neither command wakes or changes a Goal,
promotes a procedure, or supplies authority to another task. Cross-service
revocation cannot retract bytes already returned. The opt-in integration test
`packages/rail-py/tests/test_goal_evidence_cli.py` uses disposable PostgreSQL,
loopback HTTP, synthetic identity, and Seatbelt; it does not establish hosted
production identity or deployment.
