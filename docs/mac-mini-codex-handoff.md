# Mac mini Codex Handoff

This runbook hands the enterprise AI operating-system workspace to a developer
or Codex session running on `Familys-Mac-mini.local`. It records the verified
machine state as of 2026-08-10. It is an operating guide, not a release or merge
approval.

## Connect

From the current development Mac:

```bash
ssh -o BatchMode=yes -o IdentitiesOnly=yes \
  -i ~/.ssh/familys_mac_mini_ed25519 \
  family@familys-mac-mini.tailaa507c.ts.net
```

The remote user and working roots are:

```text
user: family
home: /Users/family
code: /Users/family/code
```

## Repository state

| Repository | Exact path | Active branch | Verified head | Status |
| --- | --- | --- | --- | --- |
| KRAIL | `/Users/family/code/knowledge` | `codex/krail-phase4-auth-reliability` | `97b4bb6d913713b50743b8b69beaec325bbc1c4f` | Accepted Phase 4; 695 tests pass |
| OpenSaddle | `/Users/family/code/opensaddle` | `codex/enterprise-ai-os-integration` | `3add2326907a0f495f86b5ad1bd10a382e5d89b6` | Reviewed integration branch; 511 tests pass |
| Interface | `/Users/family/code/opensaddle-interface` | `codex/i2-4-integrated-verification` | `2e45bbc2ce7d18666de185d84363813404ef493b` | Accepted Phase 2; full `real:check` passes |

All three worktrees were clean at handoff and track branches on the
`AkeBoss-tech` GitHub organization.

KRAIL also has a fetched, non-active work-in-progress branch:

```text
codex/krail-phase5-semantic-foundation
ce9e84ece507864f6b52e9db8a17b6b1d30743eb
```

That Phase 5 head passes 716 Rail/MCP tests and requires verified semantic-pack
signature admission. It is still a review candidate, not authority to merge or
resume downstream graph operations. Do not switch the main KRAIL worktree to it
without explicit Phase 5 acceptance.

## Installed toolchain

```text
Codex CLI  0.147.0 (ChatGPT login active)
GitHub CLI 2.97.0  (AkeBoss-tech active)
Node       24.19.0 LTS
npm        11.17.0
uv         0.11.2
Python     3.12.13 through uv environments
```

Git is configured with:

```text
user.name  AkeBoss-tech
user.email 69588353+AkeBoss-tech@users.noreply.github.com
pull.ff    only
fetch.prune true
push.autoSetupRemote true
```

Do not copy or print GitHub or ChatGPT tokens. Check identity without exposing
credentials:

```bash
gh auth status
codex login status
```

## Start a Codex session

Open the repository that owns the change before launching Codex:

```bash
cd /Users/family/code/knowledge
codex
```

Equivalent roots are `/Users/family/code/opensaddle` and
`/Users/family/code/opensaddle-interface`. Each repository contains its own
instructions; read `AGENTS.md` before editing.

For a non-mutating readiness check:

```bash
codex -C /Users/family/code/knowledge \
  -s read-only -a never exec "Reply with exactly: CODEX READY"
```

Use ordinary approval and sandbox settings for real work. Do not use
`--dangerously-bypass-approvals-and-sandbox` on this host.

## Verify each repository

### KRAIL

```bash
cd /Users/family/code/knowledge
uv sync --project packages/rail-py
uv run --project packages/rail-py \
  --with pytest --with jsonschema --with duckdb \
  python -m pytest -q packages/rail-py/tests packages/mcp-server/tests
```

The MCP package is installed editable in the KRAIL virtual environment. Check
the local CLI with:

```bash
packages/rail-py/.venv/bin/krail --version
packages/rail-py/.venv/bin/python -c 'import rail_mcp; print("rail_mcp ok")'
```

### OpenSaddle

```bash
cd /Users/family/code/opensaddle
uv sync --dev
uv run pytest -q
```

### Interface

```bash
cd /Users/family/code/opensaddle-interface
npm ci
npm ci --prefix packages/control-plane
npm ci --prefix packages/krail
npm ci --prefix electron
npm run real:check
npm run electron:build
```

`real:check` currently completes with baseline lint and bundle-size warnings.
`npm audit` also reports existing lockfile vulnerabilities. Do not run
`npm audit fix` or `--force` during setup because either can rewrite dependency
resolution and create unrelated product changes; handle upgrades on a reviewed
dependency branch.

## Safe daily workflow

Start from a clean, current integration branch:

```bash
git status --short --branch
git fetch --prune origin
git pull --ff-only
```

Create a focused branch instead of editing an accepted integration branch:

```bash
git switch -c codex/<short-task-name>
```

Before committing:

```bash
git status --short
git diff --check
git diff
```

Run the repository-specific gate, then commit and push the explicit branch:

```bash
git add <owned-paths>
git commit -m "<concise description>"
git push -u origin HEAD
```

Never force-push by default. Do not sweep unrelated untracked files into a
commit. Keep evidence, approvals, receipts, audit records, and published
ontology versions immutable; corrections should supersede or annotate them.

## Dependency and merge order

1. KRAIL Phase 1-4 is represented by the accepted Phase 4 ancestry at
   `97b4bb6d9137...`.
2. KRAIL Phase 5 signature admission at `ce9e84ece507...` needs independent
   acceptance before graph-operation work resumes.
3. OpenSaddle execution and edit-command contracts remain authoritative for
   policy, approvals, credentials, operations, external effects, and receipts.
4. KRAIL remains authoritative for knowledge claims, source observations,
   lineage, and evidence assembly.
5. The Interface consumes reviewed OpenSaddle/KRAIL contracts and must not
   invent execution or evidence truth.

Do not merge all repositories merely because their local gates pass. Merge each
accepted integration head through its own pull request and preserve the
cross-repository contract order.

## Recovery and diagnostics

Check Codex and GitHub connectivity:

```bash
codex doctor --summary
gh auth status
ssh -T git@github.com
```

Confirm push access without changing GitHub:

```bash
git push --dry-run
```

If authentication fails, do not delete existing credentials. Capture the
redacted output of `codex doctor --summary` or `gh auth status` and repair the
specific provider. If a branch has local changes, preserve them before changing
branches or pulling.

The host had approximately 8.1 GB free after setup. Check space before large
builds or parallel worktrees:

```bash
df -h /Users/family
du -sh ~/.codex /Users/family/code/*
```

Codex retained several gigabytes of historical rollout files at handoff. Do not
delete session state casually; archive completed work through Codex before any
storage cleanup.

## Handoff checklist

- [ ] Confirm SSH access and hostname.
- [ ] Run `codex login status` and `gh auth status`.
- [ ] Confirm the intended repository, branch, and exact head.
- [ ] Confirm `git status` is clean before editing.
- [ ] Read repository instructions.
- [ ] Create a focused `codex/` branch.
- [ ] Run the relevant focused and full gates.
- [ ] Review the diff and push without force.
- [ ] Record exact commit hashes and remaining risks.
- [ ] Do not present Phase 5 or downstream work as accepted without its review.
