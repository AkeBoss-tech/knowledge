# Generated Project Hygiene

## Purpose

KRAIL projects produce operational state: captures, sessions, workspaces,
work orders, hydration outputs, audits, and artifacts. Those are project state,
not platform source code. Treating them like ordinary tracked runtime code makes
diffs noisy and couples engine changes to one-off agent work.

## Policy

1. Platform code and project state should be committed separately.
2. Pilot projects should live outside the runtime repo, or under `pilots/` /
   `generated_projects/` which are ignored by default.
3. Public examples should be small, synthetic, and curated under `examples/`.
4. Routine runner outputs should stay untracked:
   - `.rail/workspaces/`
   - `research_plan/sessions/`
   - transient logs
   - regenerated dashboards, datasets, databases, and intermediate artifacts
5. Repo-backed reconciliation and agent workflows must work even when runtime
   outputs are not committed.

## Operational Rules

- Use narrow `git add` commands for platform fixes.
- Do not sweep pilot projects into platform commits.
- Promote only the minimal stable files needed for examples or fixtures.
- Prefer summaries in `docs/` over committing broad project directories.

## Promotion Checklist

Before committing project output, ask:

- Is this needed as a stable fixture?
- Is the data public or synthetic?
- Does this contain transient runner state, local paths, or secrets?
- Can the same value be captured as a README, screenshot, or small fixture?
- Will this make future platform diffs harder to review?

If any answer points to transient or private state, do not commit it.
