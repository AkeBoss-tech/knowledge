# KRAIL

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/AkeBoss-tech/knowledge)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/krail?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/krail)
[![PyPI version](https://badge.fury.io/py/krail.svg)](https://badge.fury.io/py/krail)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Local-first memory and workflows for serious AI agent projects.**

KRAIL gives agents a durable project workspace instead of fragile chat context.
You keep sources, notes, claims, workflows, prompts, and task records in a
repo-backed local project, then let agents search, synthesize, and operate on
that knowledge with citations and audit trails.

It is built for people who have outgrown "paste files into chat" and want a
local-first way to give Codex, Claude Code, Cursor, and MCP-compatible tools a
real working memory.

## Why KRAIL?

Most agent workflows break down in the same places:

- context disappears between sessions
- research notes and source files drift apart
- retrieval returns snippets but not a trustworthy working record
- agent work is hard to audit, rerun, or promote into trusted knowledge

KRAIL is the repo-backed layer that sits between raw files and agent actions.

```text
search   = retrieve document evidence in the project
find     = find typed records across docs, graph, evidence, sessions, and queues
think    = synthesize evidence + cite files + expose gaps
task     = create auditable work orders for local agents
workflow = run repeatable project routines from the active pack
listener = notice local/external changes and trigger workflows
queue    = reserve inventory batches for parallel ingestion workers
integrity = decide what is ready to trust, verify, or promote
```

## What You Get

- local-first knowledge projects with `krail.yaml`, `topics/`, `sources/`, and
  `research_plan/`
- deterministic search, unified `find`, and `think` envelopes with citations,
  freshness, typed records, and next actions
- repo-backed tasks, workflow runs, and session outputs
- listener/event triggers for files, websites, RSS, GitHub polling, schedules,
  and custom command adapters
- repo-backed inventory queues with batch reservation, checkpointing, and retry
  surfaces for ingestion workers
- markdown graph inspection for frontmatter-rich topic collections
- MCP tools for agents like Codex, Claude Code, Cursor, and Gemini
- optional local API adapter for custom clients and interfaces

## Quick Start

Install from the repo root:

```bash
./scripts/install-rail.sh
source .venv/bin/activate
```

Important:

- install name: `krail`
- import name: `rail`
- CLI commands: `krail` and `rail` both work

Create a local project and run the first health check:

```bash
krail init robotics-kb --pack research-intelligence --mode markdown_graph
cd robotics-kb
krail --local doctor
```

Capture a note, search the project, and generate a cited answer envelope:

```bash
krail --local capture "GCS may be useful as a feasibility layer for LLM task plans"
krail --local search "GCS feasibility" --explain
krail --local think "What changed in task and motion planning?"
```

Build the project graph when your notes use frontmatter:

```bash
krail --local graph build
krail --local graph entities --type Package
krail --local graph edges --entity PDDLStream
```

Create auditable work before launching another agent:

```bash
krail --local task create "summarize new captures" --runner codex_cli
krail --local task list
krail --local task dispatch 1 --dry-run
```

## What A Good First Run Looks Like

If KRAIL is working well for you, the first session should feel like this:

1. You initialize a local project in under a minute.
2. `doctor` tells you whether the workspace is healthy.
3. `capture` puts raw notes into a predictable inbox.
4. `search` finds the relevant local evidence.
5. `think` returns a usable answer envelope with citations and gaps.
6. `task` or `workflow` prepares agent work without losing project state.

## Primary Use Cases

### Research workspaces

Track papers, methods, claims, gaps, and follow-up work in a durable local
project instead of scattered notes and ad hoc prompts.

### Software knowledge bases

Give coding agents a repo-backed memory layer with notes, decisions, prompts,
tasks, and workflow history.

### Private company or document brains

Keep internal sources, policy notes, owners, and stale-doc reviews local and
auditable.

## Current Status

KRAIL is ready for pilot projects. It is not yet a polished production
platform.

Working well now:

- local project scaffolding
- knowledge pack activation
- capture inbox
- deterministic local file search and unified typed `find`
- deterministic `think` envelope
- markdown graph build/query/export
- repo-backed tasks, work orders, and session records
- listener templates, event logs, workflow triggers, and event replay
- queue-based ingestion, workflow dashboards, parameterized workflow inputs,
  and lightweight typed workflow outputs
- dry-run and full dispatch to local CLIs
- MCP tools for find, search, think, capture, tasks, workflows, and integrity
- optional local FastAPI adapter using `.krail/store.json`

Still early:

- model-backed synthesis inside `think`
- better embedding and reranking defaults
- deeper graph-aware retrieval
- external pack installation
- production-grade sandbox and permission controls

## MCP

`rail-mcp` exposes a local KRAIL project to MCP-compatible tools.

```bash
pip install -e packages/rail-py
pip install -e packages/mcp-server
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

This is a strong fit if you want local project knowledge available inside agent
tools without turning the project itself into a hosted service.

## Install Notes

Requirements:

- Python 3.11+
- git
- optional local agent CLIs such as `codex`, `claude`, `gemini`, `agent`, or
  `gh`

Optional agent CLI setup:

```bash
./scripts/install-agent-clis.sh
```

## Documentation

- [Docs Index](docs/README.md)
- [Architecture](docs/architecture.md)
- [Knowledge Modes](docs/knowledge-modes.md)
- [Project Layout](docs/project-layout.md)
- [Growth Plan](docs/growth-plan.md)
- [Launch Kit](docs/launch-kit.md)
- [Launch Posts](docs/launch-posts.md)
- [Demo Script](docs/demo-script.md)

## Contributing

KRAIL is early and local-first. The most useful contributions are:

- install and onboarding fixes
- tighter docs and examples
- focused tests around `packages/rail-py`, `packages/mcp-server`, or
  `packages/api`
- workflow, search, capture, doctor, and integrity improvements

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
