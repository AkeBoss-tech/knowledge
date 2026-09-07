# #29 local connected-Git shared-knowledge journey

`rail.shared_knowledge.SharedKnowledgeWorkspace` is a standalone local
connected-Git contract. The bare Git remote is the sole canonical writer;
proposal state is restartable metadata and `AuthorizedKnowledgeContext` is an
in-process disposable cache. `SharedKnowledgeWorkspace` requires a
caller-owned `SharedKnowledgeActionAuthorizer`; it neither stores grants nor
implements IAM. The adapter receives a subject, action, and exact signed
`ResourceRef`, and must make a live decision before every shared effect and
before returning context, search, lineage, or export data. Neither a user
checkout nor a cache can promote content.

`test_two_user_git_proposals_review_conflict_restart_and_revocation` creates a
temporary bare remote and two real clones. Alice and Bob independently commit
competing proposals from the same exact `main` commit. Review atomically moves
`main` to Alice's candidate only when the remote still names her recorded base;
Bob's proposal is marked conflict and cannot overwrite it. A restarted service
rebuilds context from the remote and persisted metadata. Revoking Bob in the
live adapter denies cached context, search, and export immediately; Alice's
scoped context remains readable. State writes use an advisory lock, reload
under that lock, a monotonically increasing revision, atomic replacement, and
directory sync. If a process dies after Git's compare-and-swap promotion but
before metadata persistence, restart recovery recognizes that `main` already
equals the candidate and records promotion; any changed head instead becomes a
conflict.

| #29 requirement | Local journey evidence | Status |
| --- | --- | --- |
| One explicit authority mode | Fixed `connected-canonical-git-reviewed-changes`; remote `main` is canonical | Met for local connected Git. |
| Conflict/review promotion | exact base/candidate refs and `git update-ref` expected-old atomic promotion | Met. |
| Restart and cache revocation | persisted proposal metadata, remote reread, user-keyed cache with live reauthorization, denied search/export | Met for process-local cache. |
| Exact lineage | promoted candidate commit returned with canonical context | Met for promoted head. |
| Mode transitions with consent/backup/rollback | No transition workflow | Remaining. |
| GitHub connector/webhooks/PR reconciliation | No network connector or webhook implementation | Remaining. |
| Hosted ACL storage and cross-user cache bounds | Caller-owned signed action adapter; no hosted service or KRAIL-owned grants | Remaining. |
| Deletion/retention/backups | Revocation removes derived cache only; Git history and backups are not erased | Remaining and explicitly not claimed. |
| Direct filesystem isolation | Only clean regular worktrees whose `origin` resolves to the configured bare remote are accepted; absolute/traversal/.git paths, symlink traversal, branch reset, arbitrary remotes, and hooks are rejected/disabled. Users with raw filesystem access can still bypass KRAIL metadata. | Enforced adapter boundary; hosted isolation remains required. |

This slice does not expose an HTTP service, Core base URL, GitHub write, or
credential-bearing connector. Core/Interface may invoke the local contract in
a deterministic fixture, but must not report it as a hosted KRAIL service.
