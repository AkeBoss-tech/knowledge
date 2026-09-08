# Knowledge #25 Provider Packet Proof

Status: Knowledge-side implementation verified and accepted for the current
feature-branch checkpoint.

## Published contract

The existing provider now publishes a separate read-only negotiated capability:

```text
krail.authorized-context-packet@1.0.0
  create_authorized_context_packet
  read_authorized_context_packet
```

The immutable result uses schema `krail.authorized-context-packet.v2`. Its
`packet_id` equals its content digest and binds the original authorization
digest, capability identity and descriptor digest, signed request digest,
shared task purpose, project scope, every exact resource reference disclosed
anywhere in the Context Brief, full Context Brief bytes, processing metadata,
freshness, conflicts, omissions, and truncation state.

`PacketRequestBinding` and `SignedPacketRequestBinding` reuse the caller-owned
`AccessContextAuthority` key and signature machinery. The binding covers the
access-context digest, tenant/project, packet capability identity, complete
request digest, purpose, scope, and validity interval. Changing the query,
purpose, scope, exact refs, byte budget, or token budget under the same signed
binding is denied. KRAIL receives no signing secret and mints no identity.

## Durable create and reread behavior

The application composition root injects the external authority, expected
tenant/project, live clock, and current-source-head resolver. The actual
`LocalKnowledgeProvider` transport exposes packet create and read methods.
Creation verifies the signed service scope before composition and immediately
before persistence and return. Packets are stored as canonical immutable bytes
under `.krail/authorized-context-packets/<packet-digest>.json`.
The capability descriptor's `read-only`/`external_effects=false` declaration
means creation mutates no knowledge source, authorization state, or external
system. The content-addressed packet file is internal derived cache bookkeeping;
it cannot change source truth or grant access and can be recreated from the
same authorized exact inputs.

## Local derived-cache lifecycle

The packet directory is ignored local derived state, not a hosted capture,
object-store object, or shared-knowledge Git bundle.  It is therefore managed
only through the packet service's local lifecycle seam, outside the published
read-only provider capability.  A caller must present a currently valid signed
`AccessContextAuthority` context scoped to the configured tenant, project, and
every exact affected source, with the existing `retention.enforce` action.
KRAIL uses its injected clock; a supplied reason or timestamp cannot authorize
or advance maintenance.

`invalidate_for_sources` removes matching cache files for a caller-authorized
`source-deleted`, `source-revoked`, or `retention-expired` event.  The durable,
bounded (1,024-entry) tombstone journal retains only packet digest, reason,
time, and a decision digest.  It has no source IDs, excerpts, lineage, counts,
or packet content.  The journal is checked before cache write and read, so a
removed packet remains unavailable across a restart and cannot be rewritten
from the same inputs.  If its capacity or journal validation is unavailable,
maintenance fails closed rather than compacting away a denial.
Journal update plus cache unlink and cache publication use one service-owned
interprocess lock. A cache deletion that fails after its tombstone is durable
returns an explicit incomplete maintenance failure; readers still deny the
tombstoned packet. Packet reads recheck tombstone and TTL at their final
release boundary.
The linearization point for a successful cache read is that final locked
tombstone/TTL check: an invalidation committed before it returns the coarse
unavailable result, even if the packet bytes and every earlier authorization
check had succeeded.
The lock uses POSIX `flock` on a local service-owned filesystem. This proof
does not claim equivalent behavior on filesystems without reliable POSIX file
locking or across independently managed cache directories.

Age expiry is disabled unless the caller explicitly injects a packet retention
policy at the same composition root.  `enforce_retention` reuses the signed
maintenance context and service clock.  It makes no hosted service, backup, or
physical-erasure claim: Git history, Git bundles and external clones, OS or
provider backups, and an unconfigured hosted object store may retain earlier
content.  Core must connect its authoritative source deletion, revocation, and
expiry decisions to this local invalidation seam; a live packet read already
rechecks grants and current exact source heads.

An existing packet read accepts a fresh caller delegation but retains the
packet's original `authorization_digest`. A separate bounded reauthorization
receipt reports the new decision. Every read validates the stored bytes and
digest, the original query/purpose/project scope, all current exact grants, and
the current head of every resource disclosed in the Context Brief. A retained
old resource remains historical provenance; advancing its current source head
still makes the cached packet unavailable.

Denial, narrowing, revocation, a source-head update, a missing handle, or packet
tamper returns only `context_packet_unavailable` with null packet and receipt.
The response contains no old source ID, count, excerpt, citation, or summary.

## Explicit bounds

The capability limits canonical packet bytes to 524,288 and the configured
context token upper bound to 32,768. The token bound is deliberately
conservative: it counts every UTF-8 byte in the canonical serialized Context
Brief, including citations and metadata, as at most one token. It does not
claim tokenizer-exact counts. The packet reports and validates both its full
canonical byte count and its canonical Context Brief byte count. Packet
metadata and receipt bytes are separately covered by the packet and response
byte limits. The immutable packet explicitly reports its estimator provenance
and that bounded search or source truncation may omit evidence.

## Local verification

```text
16 passed in 5.26s
  test_authorized_context_packet_service.py

98 passed in 21.75s
  packet, authorized-context, capability-publication, application-provider,
  provider-v1, Context Brief, and Context Brief vertical suites

47 passed in 0.68s
  hosted authorization and phase-3 capability conformance suites

908 passed in 67.24s
  full packages/rail-py/tests suite

compileall: clean
git diff --check: clean
```

The accepted lifecycle checkpoint was additionally verified by the root review
with 29 packet-service and integration tests in 12.75s. The full Knowledge
suite was rerun at `ffb2765` with:

```text
PYTHONUSERBASE=/Users/akashdubey/.codex/agent-runtime/opensaddle-20260907/knowledge-python-userbase
PYTHONPATH=packages/rail-py:/Users/akashdubey/.codex/agent-runtime/opensaddle-20260907/python-overlay
/opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest -q packages/rail-py/tests
```

It passed: 921 tests in 81.90s.

The partial-result and missing-current-head regressions were verified against a
deterministically reconstructed mutation of the reviewed draft, then with the
identical permanent assertions against the repaired source. This is mutation
evidence, not a claim that an original pre-edit source snapshot was captured.

## Acceptance boundary

Local maintenance is caller-authorized cache cleanup only. It does not replace
the caller's source revocation authority or make a source lifecycle decision.

This completes the cohesive Knowledge provider side of #25. Full #25 remains
open until Core's coding Run and Interface's Run Inspector both consume the
same packet handle and demonstrate client-state clearing on unavailable reads.
