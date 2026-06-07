# KRAIL

KRAIL is a local-first knowledge runtime extracted from RAIL.

The goal is to keep the durable core:

- ontology and hydration engine
- Python SDK and CLI
- optional local API runtime
- MCP tools for agents
- repo-backed integrity/state ledgers

The runtime should not require a hosted database or a bundled frontend. Custom
interfaces can be built as clients of the SDK, API, or MCP layer.

## Install

Requirements:

- Python 3.11+
- git

```bash
./scripts/install-rail.sh
```

## Run

Start the optional API adapter:

```bash
make run
```

API:

- `http://localhost:8000`
- `http://localhost:8000/docs`

The API stores temporary operational records in `.krail/store.json` by default.
Durable project state should live in the project repo.

## Brain UX

Phase 1 exposes the GBrain-style split:

```bash
rail search "task and motion planning"
rail think "what are the open problems in task and motion planning?"
rail capture "GCS may be useful as a feasibility layer for task plans"
rail doctor
rail pack use research-intelligence
```

`search` returns ranked evidence. `think` returns an answer shape with evidence,
confidence, gaps, conflicts, and suggested next actions. In this phase, `think`
is deterministic and local; it does not pretend to be LLM synthesis until a
provider-backed synthesizer is wired in.

Local CLI agents can now be used as workflow workers:

```bash
krail --local agent list
krail --local agent run "research recent papers on graph of convex sets" --runner codex_cli --dry-run
krail --local task create "Compare GCS and diffusion policy" --runner claude_code
krail --local task dispatch task_compare-gcs-and-diffusion-policy_abc123 --dry-run
krail --local workflow list
krail --local workflow run weekly_literature_refresh --dry-run
```

These commands create repo-backed records under `research_plan/tasks`,
`research_plan/work_orders`, and `research_plan/sessions`. Use `--dry-run` to
inspect the exact command before launching another local agent process.

## Local Project Mode

```bash
export RAIL_LOCAL=1
export RAIL_PATH=/path/to/project
rail query classes
rail hydrate
rail integrity status
```

## Packages

- `packages/engine`: hydration, ontology, transforms, analysis hooks
- `packages/rail-py`: Python SDK and CLI package
- `packages/mcp-server`: MCP server for agent clients
- `packages/api`: optional HTTP adapter over local runtime state

## Docs

Start with [docs/README.md](docs/README.md).

## Direction

The next refactor is to rename and split the packages into explicit
`krail-core`, `krail-cli`, `krail-api`, and `krail-mcp` packages, then move
domain examples into `templates/` and `examples/`.
