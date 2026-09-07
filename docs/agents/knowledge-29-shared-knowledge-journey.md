# #29 local connected-Git shared-knowledge journey

`rail.shared_knowledge.SharedKnowledgeWorkspace` is a standalone local
connected-Git contract. The bare Git remote is the sole canonical writer;
proposal state is restartable metadata and `AuthorizedKnowledgeContext` is an
in-process disposable cache. Neither a user checkout nor a cache can promote
content.

`test_two_user_git_proposals_review_conflict_restart_and_revocation` creates a
temporary bare remote and two real clones. Alice and Bob independently commit
competing proposals from the same exact `main` commit. Review atomically moves
`main` to Alice's candidate only when the remote still names her recorded base;
Bob's proposal is marked conflict and cannot overwrite it. A restarted service
rebuilds context from the remote and persisted metadata. Revoking Bob's source
grant removes his cached derived context and denies both context and export;
Alice's scoped context remains readable.

| #29 requirement | Local journey evidence | Status |
| --- | --- | --- |
| One explicit authority mode | Fixed `connected-canonical-git-reviewed-changes`; remote `main` is canonical | Met for local connected Git. |
| Conflict/review promotion | exact base/candidate refs and `git update-ref` expected-old atomic promotion | Met. |
| Restart and cache revocation | persisted proposal metadata, remote reread, user-keyed cache eviction and denied export | Met for process-local cache. |
| Exact lineage | promoted candidate commit returned with canonical context | Met for promoted head. |
| Mode transitions with consent/backup/rollback | No transition workflow | Remaining. |
| GitHub connector/webhooks/PR reconciliation | No network connector or webhook implementation | Remaining. |
| Hosted ACL storage and cross-user cache bounds | Local grants only; no hosted service | Remaining. |
| Deletion/retention/backups | Revocation removes derived cache only; Git history and backups are not erased | Remaining and explicitly not claimed. |
| Direct filesystem isolation | Temporary clones demonstrate the boundary; real shared checkouts can bypass KRAIL ACL metadata | Documented limitation; hosted isolation remains required. |

This slice does not expose an HTTP service, Core base URL, GitHub write, or
credential-bearing connector. Core/Interface may invoke the local contract in
a deterministic fixture, but must not report it as a hosted KRAIL service.
