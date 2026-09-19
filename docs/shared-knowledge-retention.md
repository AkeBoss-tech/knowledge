# Local shared-knowledge backup retention

`SharedKnowledgeWorkspace` manages verified Git bundles under the local state
path. Its retention policy is intentionally narrow and honest:

- at most 128 service-managed bundles are admitted;
- a backup ID is lifetime single-use within the workspace state, including
  after its bundle is successfully pruned;
- bundles have no automatic age-based expiry;
- removal requires a live `shared_knowledge.backup_prune` decision for the
  exact `backup/<id>` resource and its expected digest;
- a bundle referenced by any registered mode transition is not eligible for
  pruning;
- pruning removes only that service-owned bundle file and its active backup
  metadata. It does not rewrite canonical Git history, modify the active mode,
  reset a repository, delete external clones, or prove erasure elsewhere.

## Inspect and prune

```python
inventory = workspace.backup_inventory(owner_id="owner")

candidate = next(
    item
    for item in inventory.backups
    if item.backup_id == "private-before-tombstone"
    and item.eligible_for_prune
)

receipt = workspace.prune_backup(
    owner_id="owner",
    backup_id=candidate.backup_id,
    expected_digest=candidate.bundle_digest,
)
```

Inventory is metadata-only. Each entry reports availability, byte length,
exact bundle digest, transition protection, and the conservative fact that the
bundle may retain prior content. The inventory also declares that canonical Git
history and external clones may retain that content even after the current
knowledge view is tombstoned and the selected bundle is pruned.

Prune uses the existing locked state as a two-phase action journal. A pending
entry is durably recorded before unlinking the exact validated bundle. After an
interruption, reopening the workspace can complete that same backup ID/digest;
it cannot select another bundle. Completed retries return the same minimal audit
receipt containing identifiers, digests, and time only—never file content.
Completed audit receipts remain in the local metadata journal without automatic
expiry; the 128-item bound applies to live managed bundle files, not receipts.

Unsafe stored paths, digest mismatches, symlink substitutions, unavailable
bundles, revoked owners, and transition-protected bundles fail closed. This is a
local lifecycle API, not a production backup service or an external erasure
guarantee.

Bundle hashing is streamed in fixed 1 MiB chunks. Before unlink, prune compares
the regular file's device, inode, and size to the identity observed during
digest and Git-bundle verification. The backup directory remains a trusted
service-owned local directory: portable pathname APIs cannot make verification
and unlink one atomic operation against a privileged process concurrently
replacing directory entries. Deployments must not grant other writers access to
that directory.
