# Local-Direct Git Writer & Connected→Local Activation — Handoff

Scope: complete the local-direct Git writer and connected→local mode
activation on top of the verified Git bundle backups and registered preview
validator landed at `33afc76` (`packages/rail-py/rail/shared_knowledge.py`).

**Revision history:**
- Round 2: root ran the acceptance suite (1 failed / 4 passed) against the
  first version and found real defects — a mis-ordered gate check, a
  genuinely broken CAS (the first version incorrectly claimed a plain
  `git push` was an exact compare-and-swap — it is not), a crash-safety hole
  that could silently lose provenance for a write that had already landed
  on canonical `main`, and a missing lineage/authorization path for
  local-direct writes. All four fixed; see "Round 2 fixes" below.
- Round 3: root reproduced a further authorization bypass in the round-2
  code: resuming a pending local write (after a crash) never re-checked
  live authorization before the staging push and the main CAS — a revoked
  caller only got `PermissionError` *after* canonical Git had already
  changed. Fixed; see "Round 3 fix" below.

## Round 3 fix: resume path was missing pre-effect authorization (root-reported bypass)

Root's reproduction: inject an `OSError` at the very first `git push` to the
per-write staging ref (right after the pending record is durably persisted,
simulating a crash there), then revoke the caller's grant, then retry the
identical `write_local` call. Confirmed: the round-2 resume branch (the
`existing is not None` path in `write_local`) called `_recover_local`
(which can mutate and persist the record's status) and then, if the record
was still `"pending"`, went straight to `_validate_checkout` → staging push
→ CAS — with **no authorization check at all** until after the CAS had
already run (the only `_authorize` calls in that branch were on the
terminal-status early-return path and at the very end of the shared tail).
A revoked subject retrying a pending write could therefore still cause a
real staging push and a real main CAS, and would only be told "denied" by
the final fail-closed check after the mutation had already happened.

Fixed in `write_local`'s `existing is not None` branch: as the very first
thing after loading the prior record (before `_recover_local`, before the
identity-match check, before any checkout/push/CAS), the code now:

1. Re-reads the current canonical head and authorizes
   `shared_knowledge.local_write` against the **current repository**
   (`self._repository_ref(current)`).
2. Authorizes `shared_knowledge.local_write` against the **exact prior
   record** (`self._local_write_ref(prior)`) — the specific write_id's
   existing candidate/digest, not a hypothetical new one.

Only once both succeed does `_recover_local` get to run (so recovery can no
longer observe or mutate a record's status for an unauthorized caller), and
only then can the resume path reach the staging push or CAS.

Additionally, per root's request, the idempotent-identity check that gates
resume/reuse of an existing `write_id` now also requires the incoming
`expected_head` to equal the prior record's `base_commit` and the incoming
`expected_generation` to equal the prior record's recorded `generation` (a
new field added to `LocalKnowledgeCommit`) — not just matching
user/path/deleted-flag/content-digest as before. This closes off a
stolen/guessed `write_id` from recovering another subject's receipt by
supplying plausible-looking content without having actually observed the
original call's exact preconditions.

New test `test_write_local_resume_requires_live_authorization_before_any_effect`
reproduces root's exact scenario (crash-inject at the first staging push,
then revoke, then retry) and asserts: the crash leaves canonical `main` and
`refs/krail/local-staging/*` untouched with the record durably `"pending"`;
a mismatched-precondition retry (wrong `expected_head`) is rejected as
`"write id already exists"` without touching Git; the revoked retry raises
`PermissionError` with canonical `main`, staging refs, and the pending
record's status all still untouched; and restoring the grant lets the
identical retry resume and land normally (live-authorized recovery still
works).

## Round 2 fixes

### 1. `review_and_promote` — gate before lookup (root-reported failure)

Root's run found: "restarted `review_and_promote` in local mode raises
`unknown proposal` instead of writer-disabled because the gate is
absent/late." Confirmed and fixed — `_require_writer(state, self.mode,
"connected-git")` now runs as the *first* statement inside the lock, before
the proposal dict lookup. Previously the lookup ran first, so calling
`review_and_promote` for a proposal id that was never created (which is
exactly what happens after a connected→local switch, since no connected
proposals exist anymore) raised `SharedKnowledgeError("unknown proposal")`
instead of `SharedKnowledgeError("connected-git canonical writer is
disabled")` — misleadingly implying connected review might still be live
for some other id.

Covered by `test_review_and_promote_gates_writer_before_proposal_lookup` and
re-affirmed in `test_activation_switches_mode_and_fences_stale_connected_writers`.

### 2. `write_local` — the CAS was not actually a CAS (root-reported defect)

The first version pushed the local commit straight onto `refs/heads/main`
with a plain, non-force `git push`. Root correctly flagged that this is
**not** an exact compare-and-swap: Git's non-force fast-forward check only
requires the remote's current tip to be *an ancestor* of the pushed commit
— not *exactly* the value the caller believed it was. Concretely: if
canonical `main` is at commit A, a write lands making it commit B (a child
of A), and canonical `main` is somehow moved *back* to A (or to any other
ancestor of a commit that's about to be pushed) before a second write lands,
a plain push from a checkout descended from B would still succeed as a
"legitimate" fast-forward — silently discarding whatever the intervening
state represented. The previous docstring's claim that this was "a genuine
compare-and-swap" was wrong, and has been removed.

Fixed by splitting the git-side effect into two steps:

1. Push the local commit to a disposable, per-write staging ref
   (`refs/krail/local-staging/<write_id>`) — this is a plain object
   transfer, not a canonical mutation, and always succeeds.
2. Atomically swap canonical `main` with a single
   `git --git-dir <remote> update-ref refs/heads/main <candidate>
   <expected_head>` run directly against the bare remote. Git's `update-ref`
   with an explicit *old* value only succeeds if `refs/heads/main` is
   *exactly* that value at the instant of the call — this is the real CAS.
   On success the staging ref is deleted; the record is marked `landed` on
   success or `conflict` on failure (raised as `SharedKnowledgeError`
   "canonical head is stale").

New test `test_write_local_cas_rejects_concurrent_head_change_even_to_an_ancestor`
demonstrates this concretely: it lands one write, then — via a monkeypatch
hook fired at exactly the point between the exact-head precondition check
and the CAS — force-moves canonical `main` *backward* to an ancestor of the
in-flight candidate (a case a plain push would have silently accepted) and
asserts the atomic `update-ref` CAS rejects it, leaving `main` untouched and
the record durably marked `conflict`.

### 3. `write_local` — pending-intent persistence and crash recovery (root-reported defect)

Root: "Persist authorized local-write pending intent before CAS and recover
exact receipt after post-CAS state failure without repeated write/provider
effects; currently local write loses provenance on crash." Confirmed: the
first version only persisted the `LocalKnowledgeCommit` record at the very
end of the call (implicitly, via `_locked_state`'s normal-exit persist). A
crash any time after the local `git commit` but before that final persist —
including *after* a successful CAS, i.e. after canonical `main` had already
moved — left zero durable record that the write had ever happened, even
though canonical Git already reflected it.

Fixed with the same pattern `review_and_promote` already uses for its
reviewer receipt:

- `LocalKnowledgeCommit` gained a `status` field (`"pending"` → `"landed"` /
  `"conflict"`).
- As soon as the local commit is made and the record is built, it's written
  to `state["local_commits"][write_id]` with `status="pending"` and
  `self._persist_locked(state)` is called **explicitly**, before the staging
  push or the CAS. A crash after this point can never lose the record.
- A new `_recover_local` (mirrors the existing `_recover` for proposals)
  reconciles a `"pending"` record against canonical Git on the next call
  with the same `write_id`: if canonical `main` already equals the
  candidate, it's marked `landed`; if canonical `main` moved to something
  that isn't this record's `base_commit`, it's marked `conflict`; otherwise
  it's still genuinely in-flight and safe to resume (same checkout, same
  already-made local commit — no new commit is created).
- Once a record reaches a terminal status (`landed` or `conflict`), a later
  call with the same `write_id` and the *same* logical write (same user,
  path, deleted-flag, and content digest) returns/raises the exact recorded
  outcome idempotently, without repeating the commit, the staging push, or
  the CAS. A later call reusing the id with genuinely different content
  still raises `"write id already exists"` (this preserves the existing
  test's reuse-rejection behavior).

Covered by `test_write_local_persists_pending_before_cas_and_recovers_after_crash`
(injects an `OSError` immediately after the pending persist, confirms
canonical `main` is untouched and the pending record survived, then retries
and confirms it lands exactly once and a further retry is idempotent) and
by the second half of `test_write_local_cas_rejects_concurrent_head_change_even_to_an_ancestor`
(confirms retrying a `conflict` record fails closed every time without
re-attempting the CAS).

### 4. `authorized_context` / `export` — local-direct writes were missing from lineage (root-reported defect)

Root: "`authorized_context` lineage ignores `local_commits` entirely,
include and live-authorize exact local records and test revocation/exports."
Confirmed and fixed: `lineage` is now the union of promoted-proposal
candidates and *landed* local-commit candidates matching the current head,
and the second half of `authorized_context` (which re-authorizes every
lineage entry at the read boundary) now looks up each lineage candidate in
either the promoted-proposals map or the landed-local-commits map and
authorizes it under the correct ref type (`self._proposal_ref(...)` or
`self._local_write_ref(...)`).

Covered by new assertions inside `test_local_direct_writer_cas_generation_head_and_revocation`:
after a landed local write, `export(...)["lineage"]` includes its candidate
commit, and revoking the exact `"local/<write_id>"` resource causes both
`authorized_context` and `export` to fail closed even though the broad
repository grant is still present.

## What was already correct from the prior revision (unchanged this round)

- `commit_mode_transition` — atomic single-lock validate-then-switch
  activation of a connected→local transition, one-shot per transition id,
  re-verifying owner/source/head/generation and the actual backup bundle
  bytes via `_validate_transition_locked`.
- `_require_writer` — generalized to take an explicit `expected_writer`
  (`"connected-git"` / `"local-git"`), still re-reading durable state fresh
  on every call so no in-memory instance can observe a stale mode.
- The activation stale-head / corrupt-backup tests
  (`test_activation_rejects_stale_head_and_corrupt_backup`).

## Tests

`packages/rail-py/tests/test_shared_knowledge.py` now has, in addition to
the two pre-existing tests:

- `test_activation_switches_mode_and_fences_stale_connected_writers`
- `test_activation_rejects_stale_head_and_corrupt_backup`
- `test_local_direct_writer_cas_generation_head_and_revocation` (extended
  this revision with lineage/export/revocation assertions for a landed
  local write)
- `test_write_local_cas_rejects_concurrent_head_change_even_to_an_ancestor`
- `test_write_local_persists_pending_before_cas_and_recovers_after_crash`
- `test_write_local_resume_requires_live_authorization_before_any_effect` (new, round 3)
- `test_review_and_promote_gates_writer_before_proposal_lookup`

No changes were made to the existing connected-mode deletion workflow or
its fixtures (`test_two_user_git_proposals_review_conflict_restart_and_revocation`,
`test_rejects_untrusted_checkout_and_authorizes_before_return`) beyond the
gate-ordering fix in `review_and_promote`, which does not change any of
their assertions (verified by manual trace: the gate check only changes
behavior when mode/active_writer/generation don't match, which none of
those tests' calls trigger).

## Test verification

```
PYTHONPATH=packages/rail-py /private/tmp/krail-temporal.zSLchI/bin/python -m pytest packages/rail-py/tests/test_shared_knowledge.py -q
```

Round 2 result: **8 passed in 4.56s**.

Round 3 result (after adding `test_write_local_resume_requires_live_authorization_before_any_effect`
and the `generation`-field/pre-effect-authorization fix): **9 passed in
4.64s**, verified with both `-q` and `-v`. Full `-v` output:

```
test_two_user_git_proposals_review_conflict_restart_and_revocation PASSED
test_activation_switches_mode_and_fences_stale_connected_writers PASSED
test_activation_rejects_stale_head_and_corrupt_backup PASSED
test_local_direct_writer_cas_generation_head_and_revocation PASSED
test_write_local_cas_rejects_concurrent_head_change_even_to_an_ancestor PASSED
test_write_local_persists_pending_before_cas_and_recovers_after_crash PASSED
test_write_local_resume_requires_live_authorization_before_any_effect PASSED
test_review_and_promote_gates_writer_before_proposal_lookup PASSED
test_rejects_untrusted_checkout_and_authorizes_before_return PASSED
```

## Remaining gaps / follow-ups for root review

0. **Schema note: `LocalKnowledgeCommit` gained a required `generation`
   field this round (round 3), with no default.** Any `local_commits`
   entries persisted to `state.json` by round-2 code (before this field
   existed) would fail to reconstruct via `LocalKnowledgeCommit(**existing)`
   (missing required argument). This is a non-issue for a fresh state file
   (all of this round's tests start from empty state), but if any
   already-running deployment persisted local-write records under round 2,
   this is a breaking schema change that would need a migration (or a
   default/backfill value) before upgrading in place.
1. **Staging ref cleanup on later crash.** If the process crashes *after*
   the staging push but *before* the `update-ref -d` cleanup (e.g. a crash
   right after a successful or failed CAS), the disposable
   `refs/krail/local-staging/<write_id>` ref is left behind on the bare
   remote. This doesn't affect correctness of `main` or provenance (the
   durable `local_commits` ledger is unaffected), but it is a small resource
   leak — a stale staging ref per interrupted attempt. A follow-up could add
   a cleanup pass (e.g. in `_recover_local`, delete any staging ref matching
   a terminal record) if this matters operationally.
2. **`commit_mode_transition` for `hosted-canonical-service` remains
   unimplemented** (unchanged from before, out of scope).
3. **No local→connected reverse transition** (unchanged from before, out of
   scope — only "connected-to-local mode activation" was requested).
4. **`write_local` does not verify the checkout's current branch name**
   (unchanged from before) — matches `propose`'s existing looseness about
   local branch naming, flagged for awareness rather than as a defect.
5. **Idempotent-reuse comparison is by content digest, not a caller-supplied
   idempotency token.** If two logically different intents happen to
   produce byte-identical content for the same path under the same
   `write_id`, a retry would be treated as idempotent recovery rather than
   rejected. This mirrors how content-addressed dedup normally behaves and
   seems acceptable, but is worth root's explicit sign-off given how
   security-sensitive this path is.

## Files touched

- `packages/rail-py/rail/shared_knowledge.py`
- `packages/rail-py/tests/test_shared_knowledge.py`
- `docs/agents/local-writer-handoff.md` (this file)

No commits were made; the working tree has these edits uncommitted for root
review, per instructions. `.antigravitycli/` was left untouched and unread.
No worktree was used and no settings were changed.
