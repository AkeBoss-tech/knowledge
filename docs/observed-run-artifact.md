# Observed OpenSaddle Run artifacts

`rail.opensaddle_run_artifact.OpenSaddleRunArtifactSource` reads a completed
generic-agent Run through OpenSaddle Core's human bearer-authenticated Run,
agent-result review, and artifact-content endpoints. Configure the exact Core
origin, current human bearer, stable local installation authority, and Project
ID in the trusted local process. The bearer is kept in memory; evidence stores
no token, locator, result body, or HTTP origin.

This adapter currently requires the personal SQLite Core result-review route;
hosted PostgreSQL advertises that route as unavailable. It does not provision
an identity, create a Run, or call a provider.

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
