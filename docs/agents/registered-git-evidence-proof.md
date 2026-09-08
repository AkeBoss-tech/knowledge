# Registered Git evidence bridge proof

`rail.registered_git_evidence.RegisteredGitEvidenceBridge` is an opt-in local
composition path. The caller supplies the Git root, a separate state root,
`AccessContextAuthority`, current signed context resolver, scope, and clock.
KRAIL mints no identity or grant. Its public canonical helper is
`registered_git_evidence_request_digest(action, ref, target, state_root)`.

The signed targets are `setup:<id>`, `capture:<id>`,
`review:<captureId>:<reviewId>:<blobDigest>`, and `retrieve:<captureId>`.
Every request binding covers that target and the exact immutable Git ref.

## Evidence

The local bridge test proves explicit setup, capture, review, restart retrieval,
canonical provenance mismatch denial, valid narrow-source reader denial, and
fresh unrevoked byte-tamper denial. It also fault-injects an identical-byte
sidecar symlink within the registered directory and an oversized manifest;
both are denied while Alice has a valid signed read grant.

The root deadline probe first reproduced a blocking Git read (7.36 seconds),
then passed after the bounded selector-based read repair. The current combined
deadline probe and bridge suite passed in 5.80 seconds. Root independently ran
the canonical-provenance and owning bridge tests: 2 passed in 0.64 seconds.
Root fault injection also reproduced capture publication after delegation
revocation (0.40 seconds) and after request-binding expiry while the broader
context remained valid (0.41 seconds). Capture now re-verifies the same signed
context and same signed binding after acquiring its publication lock; the root
revocation, binding-expiry, and owning bridge tests passed together: 3 passed
in 0.72 seconds.

## Limits

This is not end-to-end Core packet or Interface evidence. It does not deploy a
connector, hosted store, or background synchronization service. The concurrency
journey uses local threads and does not stress independent processes or crash
recovery. Git history, clones, and backups can retain earlier bytes.
