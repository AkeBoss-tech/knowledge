# Context Briefs and Epistemic History

KRAIL assembles a Context Brief from exact, authority-qualified repository and
issue `ResourceRef` values through the read-only `krail.provider.v1` boundary.
The routine is provider-neutral: GitHub issue numbers, pull request fields, and
other adapter-specific values remain in adapters or source fixtures.

`ContextBriefRequest.evaluated_at`, exact source versions/digests, bounds, query,
correlation values, and semantic-processing versions are digest inputs. Reusing
the same inputs therefore reproduces the same brief digest, EvidencePacket ID,
and ordered ranking trace. Assertions carry an exact `ResourceRef`, locator, and
processing versions. Fresh, stale, and unknown evidence are explicit, as are
declared conflicts, missing counterpart evidence, and missing supplementary
evidence. An authorization-shaped omission is a constant disclosure: it never
contains hidden resource identities or counts.

Brief assembly performs provider reads only. Recording the result is a separate,
explicit local operation through `EpistemicHistory`, stored by default at
`research_plan/state/epistemic_history.json`. This narrow store accepts only
context-brief assembly and semantic-change/correction/supersession records. It
is not an OpenSaddle journal, universal event ledger, SIEM, or execution receipt.

External correlation is optional. `DomainEventRef` exposes only the KRAIL event
digest/type and optional `operation_id`, `correlation_id`, and `causation_id`;
detailed evidence history remains in the KRAIL project. Corrections and
supersessions append a replacement and deterministically mark the prior record.
Retention expiry and explicit erasure remove details while retaining a minimal
digest/correlation tombstone so references remain honest and idempotent.
