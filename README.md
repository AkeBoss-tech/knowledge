# KRAIL

**GBrain-style brain UX. RAIL-style truth engine.**

KRAIL is a local-first, repo-backed knowledge runtime extracted from RAIL. It is
for projects where agents need more than chat memory: they need a durable
knowledge workspace with sources, claims, tasks, workflows, agent instructions,
hydration pipelines, evidence ledgers, and integrity checks.

This repo is intentionally headless. There is no bundled frontend, no required
hosted database, no Convex, no Railway deployment requirement, and no hosted
runner dependency. The core should work from a local project folder with a CLI
and MCP tools, then scale outward through optional API or custom interfaces.

## What KRAIL Is

KRAIL is a runtime for knowledge projects.

A KRAIL project is a repository that can contain:

- captured notes and source pointers
- ontology definitions and hydration pipelines
- project-specific knowledge packs
- agent prompts, checklists, and skills
- repo-backed tasks and work orders
- workflow outputs and session records
- source, claim, assumption, and artifact lineage ledgers
- final artifacts that can be verified before promotion

The runtime provides:

- `search`: retrieve raw evidence from the project
- `think`: return an answer envelope with evidence, gaps, conflicts, and next actions
- `capture`: add notes, URLs, files, or stdin into a predictable inbox
- `pack`: activate project/domain packs
- `doctor`: inspect project health
- `agent`: run local CLI agents as auditable workers
- `task`: create and dispatch repo-backed tasks
- `workflow`: turn pack-defined workflow IDs into tasks
- MCP tools for agents like Codex, Claude Code, Cursor, and Gemini
- optional FastAPI adapter for custom clients

## Product Thesis

Most retrieval systems stop at "here are the matching pages." KRAIL should
eventually do the work:

```text
search = find evidence
think = synthesize evidence + cite + expose gaps
workflow = create auditable work + dispatch local agents + record outputs
integrity = decide what can be trusted or promoted
```

The long-term goal is a headless knowledge runtime where each project defines
its own schema, sources, workflows, agents, prompts, and interfaces. KRAIL
should provide the engine: search, synthesis, graph traversal, hydration,
provenance, integrity, scheduled refresh, and safe agent dispatch.

## Current Status

KRAIL is ready for pilot projects. It is not yet a polished production brain.

Ready now:

- local project scaffolding
- knowledge pack activation
- capture inbox
- deterministic local file search
- deterministic `think` envelope
- local project doctor checks
- local CLI runner discovery
- repo-backed tasks, work orders, and session records
- dry-run and full dispatch to local CLIs
- MCP tools for search, think, capture, tasks, workflows, and integrity
- optional local FastAPI adapter using `.krail/store.json`

Not ready yet:

- real LLM synthesis inside `think`
- vector search or reranking
- graph-aware retrieval and auto-linking
- full workflow scheduler
- remote permission scopes
- pack installation from external repos
- production-grade sandbox enforcement
- fully green legacy integrity test suite

## Install

Requirements:

- Python 3.11+
- git
- optional local agent CLIs such as `codex`, `claude`, `gemini`, `agent`, or `gh`

From the repo root:

```bash
./scripts/install-rail.sh
```

This creates `.venv`, installs the local packages in editable mode, and copies
`.env.example` to `.env` if needed.

After install:

```bash
source .venv/bin/activate
krail --help
rail --help
```

`krail` and `rail` currently point to the same CLI. `krail` is the preferred
name for this fork; `rail` is kept for compatibility.

Optional agent CLI check:

```bash
./scripts/install-agent-clis.sh
```

## Quick Start

Create a research knowledge project:

```bash
krail init robotics-kb --pack research-intelligence
cd robotics-kb
krail --local doctor
```

Capture something:

```bash
krail --local capture "GCS may be useful as a feasibility layer for LLM task plans"
krail --local capture --file ./paper-notes/gcs.md --type paper-note
echo "quick thought from stdin" | krail --local capture --stdin
```

Search raw evidence:

```bash
krail --local search "GCS feasibility" --explain
```

Ask for the current deterministic answer envelope:

```bash
krail --local think "what do we know about GCS feasibility?"
```

List workflows declared by the active pack:

```bash
krail --local workflow list
```

Dry-run a workflow task:

```bash
krail --local workflow run weekly_literature_refresh --runner codex_cli --dry-run
```

## Pilot Project Protocol

If another agent is going to use this package for a pilot, give it this protocol.

```text
You are piloting KRAIL as a local-first knowledge runtime.

1. Create a local project:
   krail init robotics-kb --pack research-intelligence
   cd robotics-kb

2. Run health checks:
   krail --local doctor
   krail --local pack active

3. Capture initial material:
   krail --local capture "Initial research objective..."
   krail --local capture --file ./notes.md --type note

4. Search before answering:
   krail --local search "<question>" --explain

5. Use think for the answer envelope:
   krail --local think "<question>"

6. Create auditable work before launching agents:
   krail --local task create "<task title>" --description "<task detail>" --runner codex_cli
   krail --local task list
   krail --local task dispatch <task_id> --dry-run

7. Only remove --dry-run when the command and work order look correct.

8. Treat all generated outputs as candidates until claims have evidence and
   integrity checks pass.
```

The pilot should report:

- whether project creation worked
- whether `doctor` is useful
- whether captures land in the right place
- whether search results are useful enough
- whether `think` returns a helpful envelope
- whether task/work-order/session records are understandable
- whether dry-run dispatch gives enough confidence to run a real agent
- what command the agent wished existed next

## Search vs Think

KRAIL separates retrieval from synthesis.

```bash
krail --local search "customer onboarding workflow"
krail --local think "what changed in onboarding this week?"
```

`search` returns ranked local evidence. It is useful for:

- finding source material
- building agent context
- checking whether a project knows something
- debugging why a result matched

`think` returns:

- answer
- evidence
- confidence
- gaps
- conflicts
- suggested next actions

Current `think` is intentionally conservative. It does not fake LLM synthesis.
It uses search hits and tells you what is missing. The next step is to wire
provider-backed synthesis into this envelope.

## Capture

Capture makes KRAIL part of daily work.

```bash
krail --local capture "A thought I want to remember"
krail --local capture --file ./meeting-notes.md --type meeting
krail --local capture --url https://arxiv.org/abs/1234.5678 --workflow add_new_paper
echo "from a pipe" | krail --local capture --stdin
```

Default path:

```text
topics/inbox/YYYY-MM-DD-<hash>.md
```

Captured notes include frontmatter:

```yaml
---
type: note
captured_at: 2026-06-07T00:00:00+00:00
workflow: add_new_paper
---
```

Workflows can triage the inbox later.

## Knowledge Packs

Knowledge packs describe a project's domain shape.

Current built-in packs:

- `research-intelligence`
- `company-brain`
- `software-architecture`
- `policy-compiler`

Commands:

```bash
krail pack list
krail pack show research-intelligence
krail --local pack use company-brain
krail --local pack active
krail --local pack validate
krail --local pack detect
krail --local pack suggest
```

An active pack is stored at:

```text
.krail/pack.yaml
```

Packs currently define:

- entity types
- link types
- workflow IDs

They should eventually define:

- folder structure
- source templates
- workflow templates
- prompts
- skills
- integrity gates
- MCP injection blocks
- default interfaces

## Local Agents and Workflows

KRAIL can use local CLI agents as workers.

Supported runner names:

- `codex_cli`
- `claude_code`
- `gemini_cli`
- `cursor_cli`
- `copilot_cli`

Check configured agents:

```bash
krail --local agent list
```

Run a one-off task:

```bash
krail --local agent run "research recent papers on graph of convex sets" --runner codex_cli --dry-run
```

Create and dispatch a task manually:

```bash
krail --local task create "Compare GCS and diffusion policy" \
  --description "Research differences, cite evidence, and record gaps" \
  --runner claude_code

krail --local task list
krail --local task work-order <task_id>
krail --local task dispatch <task_id> --dry-run
```

Run a workflow declared by the active pack:

```bash
krail --local workflow list
krail --local workflow run weekly_literature_refresh --runner codex_cli --dry-run
```

Dry runs create records but do not launch another agent process. Remove
`--dry-run` only after reviewing the generated command.

Records are written under:

```text
research_plan/tasks/*.json
research_plan/work_orders/*.json
research_plan/sessions/<session_id>/
```

This is the main design point: agents should do work through auditable tasks and
work orders, not loose invisible chat sessions.

## MCP

KRAIL exposes MCP tools for local agents.

Run stdio MCP against a local project:

```bash
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

Common tools:

- `search`
- `think`
- `capture`
- `doctor`
- `pack_active`
- `list_agents`
- `create_task`
- `list_tasks`
- `dispatch_task`
- `list_workflows`
- `run_workflow`
- `list_classes`
- `get_entities`
- `query_sql`
- `hydrate`
- `integrity_status`
- `integrity_sources`
- `integrity_claims`

Example local Codex-style setup depends on your Codex MCP configuration, but the
server command is:

```bash
rail-mcp --local --path /path/to/project
```

For safety, MCP `dispatch_task` and `run_workflow` default to dry-run behavior.

## Optional API Runtime

The API is optional. It wraps local project/runtime operations for clients that
prefer HTTP.

Start it from this repo:

```bash
make run
```

API:

```text
http://localhost:8000
http://localhost:8000/docs
```

Operational API records use:

```text
.krail/store.json
```

or:

```bash
LOCAL_STORE_PATH=/path/to/store.json
```

Durable project truth should live in the project repo, not in the API store.

## Project Layout

A generated local project looks like this:

```text
project/
  rail.yaml
  .krail/
    pack.yaml
  .ontology/
    ontology.yaml
    sources/
    pipelines/
    transforms/
  topics/
    inbox/
  research_plan/
    current_plan.md
    tasks/
    work_orders/
    sessions/
    state/
  agents/
    prompts/
    checklists/
  skills/
  specs/
  artifacts/
  scripts/
```

Important paths:

- `topics/inbox`: captured notes and source pointers
- `research_plan/tasks`: local task records
- `research_plan/work_orders`: exact dispatch envelopes
- `research_plan/sessions`: worker session records
- `research_plan/state`: sources, claims, assumptions, conflicts, lineage
- `.ontology`: ontology config and hydrated artifacts
- `.krail/pack.yaml`: active project/domain pack

## Integrity and Trust

KRAIL should be stricter than a normal memory app.

Generated content is not automatically trusted. A good workflow should:

1. capture or hydrate sources
2. extract candidate claims
3. attach evidence
4. record assumptions
5. generate artifacts
6. run verification
7. promote only what passes integrity gates

The current CLI exposes integrity commands from the existing RAIL SDK:

```bash
krail --local integrity status
krail --local integrity sources
krail --local integrity claims
krail --local integrity assumptions
```

The local-first brain UX is new. The older integrity system still needs pruning
and stabilization for this fork.

## How KRAIL Differs From Upstream RAIL

This fork deliberately removed or de-emphasized:

- bundled Next.js frontend
- Convex backend
- Jules hosted runner dependency
- Railway/Vercel deployment assumptions
- generated project dumps
- old release packaging scripts
- UI-first docs/specs

It kept or is keeping:

- ontology/hydration engine
- Python SDK and CLI
- MCP server
- optional FastAPI adapter
- repo-backed state and integrity ideas
- local CLI runner concepts
- work orders and session records

## What To Pull From Updated Upstream RAIL

Do not merge `upstream/future` wholesale. It reintroduces frontend, Convex,
Jules, release/deployment assets, and generated validation projects that conflict
with this local-first fork.

Worth porting by hand:

1. **Generated project hygiene**
   - `docs/generated-project-hygiene.md`
   - useful rules for keeping project outputs out of the runtime repo

2. **Goal mode / repo-backed control-plane ideas**
   - `docs/goal-upgrades/*`
   - parts of `goal_service.py`, `planner_runtime.py`, and control-plane docs
   - port only if they stay repo-backed and local-first

3. **Research quality gates**
   - closeout gates for research design, figures, and quality
   - useful for KRAIL integrity/promotion checks
   - should be moved into local project workflow checks, not API-only routes

4. **Runner lifecycle hardening**
   - local CLI completion fixes
   - stuck/session reconciliation improvements
   - event normalization ideas
   - must be adapted to KRAIL's `research_plan/tasks`, `work_orders`, and
     `sessions` records

5. **Minimal example project**
   - upstream `examples/minimal-project`
   - adapt into a KRAIL example or template

6. **Public repo hygiene**
   - `CONTRIBUTING.md`
   - `SECURITY.md`
   - parts of CI that do not require deleted API tests or frontend

Skip or avoid:

- `apps/web`
- Convex client/config
- Jules runner/profile/config
- Railway/Vercel deployment docs
- generated validation artifacts with databases/images/PDFs
- UI observability pages
- old broad specs that assume the hosted platform

Recommended next porting order:

```text
1. examples/minimal-project -> examples/research-intelligence
2. generated-project-hygiene doc -> docs/
3. research quality gates -> rail-py local integrity checks
4. runner lifecycle hardening -> rail-py local dispatcher
5. repo-backed goal mode -> local workflow/task state
6. CONTRIBUTING/SECURITY/CI -> repo hygiene
```

## Roadmap

Phase 1: local brain UX

- `init`
- `doctor`
- `capture`
- `search`
- `think`
- `pack`
- MCP tools
- local task/work-order/session records

Phase 2: better retrieval

- BM25-style scoring
- vector search
- search diagnostics
- source freshness boosts
- claim/evidence trust boosts

Phase 3: auto-linking and graph

- typed wikilinks
- deterministic edge extraction
- graph query
- dependency edges
- graph-aware search boosts

Phase 4: real synthesis

- provider-backed `think`
- citations
- confidence and freshness analysis
- conflict detection
- suggested follow-up workflows

Phase 5: workflows

- declarative workflow files
- pack-defined workflow templates
- workflow scheduler
- reusable workflow steps
- local/remote trust boundaries

Phase 6: integrity and promotion

- claim registry cleanup
- evidence chunk flow
- artifact lineage promotion
- source freshness checks
- verification certificates

Phase 7: interfaces

- notebooks
- custom project UIs
- HTTP API clients
- remote MCP with scopes

## Known Limitations

- `think` is not LLM-backed yet.
- `search` is deterministic local keyword search, not hybrid retrieval.
- Agent dispatch can launch local CLI tools, but sandboxing is still mostly the
  responsibility of those tools and the operator.
- Workflows are currently stubs that create tasks from pack workflow IDs.
- The optional API is still large relative to the desired KRAIL core.
- Some legacy RAIL integrity tests fail and need a local-first cleanup pass.

## Command Reference

Project setup:

```bash
krail init <directory> --pack research-intelligence
krail --local doctor
```

Capture:

```bash
krail --local capture "note"
krail --local capture --file ./notes.md
krail --local capture --url https://example.com --workflow add_new_paper
```

Search and think:

```bash
krail --local search "query" --explain
krail --local think "question"
```

Packs:

```bash
krail pack list
krail pack show company-brain
krail --local pack use company-brain
krail --local pack active
krail --local pack validate
```

Agents:

```bash
krail --local agent list
krail --local agent run "task prompt" --runner codex_cli --dry-run
```

Tasks:

```bash
krail --local task create "Task title" --description "Task detail"
krail --local task list
krail --local task work-order <task_id>
krail --local task dispatch <task_id> --dry-run
```

Workflows:

```bash
krail --local workflow list
krail --local workflow run weekly_literature_refresh --dry-run
```

Ontology/hydration:

```bash
krail --local query classes
krail --local hydrate
```

Integrity:

```bash
krail --local integrity status
krail --local integrity sources
krail --local integrity claims
```

API:

```bash
make run
```

MCP:

```bash
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

## Docs

Start here:

- [Architecture](docs/architecture.md)
- [Project Layout](docs/project-layout.md)
- [API Runtime](docs/api-runtime.md)
- [GBrain Synthesis](docs/gbrain-synthesis.md)
- [Generated Project Hygiene](docs/generated-project-hygiene.md)

Examples:

- [Minimal KRAIL Project](examples/minimal-project/README.md)
- [Robotics TAMP Knowledge Base](projects/robotics-tamp-kb/README.md)

## For Agents

Agents should read [AGENTS.md](AGENTS.md) first.

Core habits:

- search before answering
- capture important new context
- use dry-run before dispatch
- write through tasks/work orders
- treat generated claims as candidates
- promote only evidence-backed outputs
