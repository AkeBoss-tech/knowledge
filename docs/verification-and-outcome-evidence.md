# Verification and Outcome Evidence

KRAIL assembles verification evidence from immutable inputs supplied by a
caller. `rail.verification_evidence` links an originating Context Brief and its
exact source versions to one exact diff or patch, changed-file `ResourceRef`
values, command descriptors, bounded check summaries, artifact digests,
tool/environment metadata, semantic-processing versions, claims, conflicts,
and explicit gaps. Assembly is deterministic and local. It does not run the
described commands, read a working tree, approve a change, or mutate a
repository.

Raw patches and logs are not embedded in verification models or their
`EvidencePacket`. `ArtifactReference` carries exact identity, version, digest,
media type, optional size, and at most a bounded summary. Partial artifacts
also carry the digest of the supplied partial content. Missing, inaccessible,
and redacted checks use constant disclosure without resource identities or
counts.

`rail.outcome_observations` ingests provider-confirmed commit, pull-request,
review, CI-run, and check-run observations as immutable provider-authoritative
records. `authority=provider-observed` is separate from KRAIL semantic
assertions, and those assertions must cite the exact observed `ResourceRef`.
Supplying newer state never replaces a pinned observation: it must name the
prior observation digest and produces explicit drift metadata in a new
observation version.

Missing, inaccessible, redacted, partial, corrected, retained, and erased
states have deterministic shapes. Unavailable or erased observations retain no
provider resource identity, payload, summary, or hidden count. Correction,
retention, and erasure are represented by new observations linked through
digest-only supersession.

Both routines accept optional `operation_id`, `correlation_id`, and
`causation_id` values plus an optional digest-only `DomainEventRef` for an
external operation/effect receipt. These are stable correlation references,
not a claim of cross-system codec compatibility. KRAIL remains fully
standalone when they are absent and does not import an external orchestrator.

Executable contract fixtures:

- `packages/rail-py/tests/fixtures/evidence_foundation/verification_request.json`
- `packages/rail-py/tests/fixtures/evidence_foundation/outcome_request.json`
