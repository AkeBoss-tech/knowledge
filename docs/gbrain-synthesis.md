# GBrain Synthesis

KRAIL should copy GBrain's usable operating model while keeping the stricter
RAIL truth engine.

## Product Thesis

GBrain-style brain UX, RAIL-style truth engine.

KRAIL is a headless, repo-backed knowledge runtime where projects define their
own schema, sources, workflows, agents, prompts, and interfaces. The engine
provides search, synthesis, graph traversal, hydration, provenance, integrity,
and scheduled refresh.

## Copied Patterns

- `search` and `think` are separate.
- `capture` is a first-class daily-work command.
- MCP is the primary agent interface.
- Knowledge packs define project shape, entity types, workflows, prompts, and
  integrity gates.
- `doctor` is the health and remediation entrypoint.
- Synthesized answers must expose evidence, confidence, gaps, conflicts, and
  next actions.
- Local callers can do more than remote callers; remote writes should become
  candidate/proposal flows.

## Phase 1 Commands

```bash
rail init robotics-kb --pack research-intelligence
cd robotics-kb
rail --local doctor
rail --local capture "New research note"
rail --local search "motion planning"
rail --local think "what are the open problems?"
rail --local pack active
```

## Phase 1 Agent Dispatch

```bash
rail --local agent list
rail --local agent run "research recent GCS papers" --runner codex_cli --dry-run
rail --local task create "Compare GCS and diffusion policy" --runner claude_code
rail --local task dispatch task_compare-gcs-and-diffusion-policy_abc123 --dry-run
rail --local workflow list
rail --local workflow init weekly_literature_refresh
rail --local workflow execute weekly_literature_refresh --dry-run
```

Records are repo-backed:

- `research_plan/tasks/*.json`
- `research_plan/work_orders/*.json`
- `research_plan/sessions/*`

This makes local agents part of auditable workflows instead of loose chat
sessions. Generated outputs remain candidate material until reviewed and
promoted through integrity checks.

## Pack Commands

```bash
rail --local pack list
rail --local pack show research-intelligence
rail --local pack use company-brain
rail --local pack validate
rail --local pack detect
rail --local pack suggest
```

Built-in phase-1 packs:

- `research-intelligence`
- `company-brain`
- `software-architecture`
- `policy-compiler`

## Trust Boundary

Local CLI and stdio MCP are trusted developer interfaces. Remote MCP/API access
must narrow write capabilities:

- read scope: search, think, read, graph query
- write scope: capture candidates, proposed doc changes, claim candidates
- admin scope: hydrate, schedule workflows, approve promotions

## Not Yet Implemented

- vector search
- graph-aware retrieval
- LLM synthesis
- workflow scheduler
- pack installation from external repos
- eval runner
- remote permission scopes
