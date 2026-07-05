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
permissions = keep access public by default, restrict only explicit records
integrity = decide what is ready to trust, verify, or promote
```

## What You Get

- local-first knowledge projects with `rail.yaml`, `.ontology/`, `topics/`,
  `sources/`, `research_plan/`, and `artifacts/`
- deterministic search, unified `find`, and `think` envelopes with citations,
  freshness, typed records, and next actions
- repo-backed tasks, workflow runs, and session outputs
- public-by-default permission metadata with repo audit logs for denied access
  and restricted-record access through KRAIL surfaces
- listener/event triggers for files, websites, RSS, GitHub polling, schedules,
  and custom command adapters
- deterministic repo snapshot, inventory, ownership, dependency, and change
  inspection for software-map workflows
- repo-backed inventory queues with batch reservation, checkpointing, and retry
  surfaces for ingestion workers
- markdown graph inspection for frontmatter-rich topic collections
- MCP tools for agents like Codex, Claude Code, Cursor, and Gemini
- optional local API adapter for custom clients and interfaces

## V1 Contract

KRAIL's v1 promise is a stable local-first runtime for repo-backed knowledge
work.

The v1 contract covers:

- `krail init` scaffolding a working local project with `rail.yaml`
- `doctor`, `mode active`, and `pack active` for local project inspection
- `capture`, `inbox list`, `inbox promote`, and `topic upsert` for the raw-note
  to durable-topic loop
- deterministic `search` and typed `find`
- optional local vector retrieval via `vector build` and `vector search`
- deterministic `think` envelopes with citations, freshness, gaps, conflicts,
  and next actions
- repo-backed tasks, workflow templates, materialized workflow execution, and
  dry-run dispatch records
- `integrity status` and related ledger views for promotion readiness
- MCP access to the stable local project subset

The v1 contract does not promise:

- hosted platform behavior or managed multi-user control planes
- host-level sandbox isolation
- autonomous agent execution without human review
- model-backed synthesis as the default `think` behavior
- mature external pack registries or plugin ecosystems
- perfect semantic retrieval or perfect reranking

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
- this repository root is the KRAIL source tree, not a local KRAIL project

From a source checkout, use the curated example fixture for a repo-root smoke
check:

```bash
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project doctor
```

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

`think` now follows the `krail.think.v1` contract. `deterministic` mode stays
honest and returns an evidence envelope rather than pretending a model
synthesized an answer. `runner` and `hybrid` modes write reviewable session
traces under `research_plan/sessions/think_*`, including the prompt, evidence
packet, result envelope, and failure state when synthesis cannot run cleanly.

Build the project graph when your notes use frontmatter:

```bash
krail --local graph build
krail --local graph entities --type Package
krail --local graph edges --entity PDDLStream
```

Inspect a local codebase when using `knowledge_mode: software`:

```bash
krail --local repo snapshot .
krail --local repo inventory .
krail --local repo symbols .
krail --local repo owners .
krail --local repo dependencies .
krail --local repo changed . --base-ref origin/main
```

Create auditable work before launching another agent:

```bash
krail --local task create "summarize new captures" --runner codex_cli
krail --local task list
krail --local task dispatch <task_id> --dry-run
```

Before you promote a topic update or ship an artifact, check integrity:

```bash
krail --local integrity status
krail --local integrity source <source_key>
krail --local integrity claim <claim_key>
krail --local integrity artifact <artifact_path>
krail --local integrity stale-graph
krail --local integrity verification-runs
```

`integrity status` is the readiness surface. It tells you what can be trusted
now, what is stale, what still lacks evidence, and which KRAIL command to run
next before promotion or release. Use the detail commands to inspect and repair
the exact record that is blocking trust.

## Retrieval Defaults

`krail --local search` now defaults to deterministic hybrid retrieval:

- lexical scoring over local files
- local graph boosts from frontmatter relations
- offline vector similarity using the built-in `local_hash` embedding provider

This keeps retrieval local-first and reproducible. The first hybrid search will
build `.krail/vector.sqlite` automatically when needed.

Use lexical plus graph only when you want the old behavior:

```bash
krail --local search "employment index" --no-rag
```

Model-backed embeddings are an explicit upgrade path, not a requirement:

```bash
krail --local vector build --provider openai --model text-embedding-3-small
```

Or use local transformer embeddings by opting into the extra dependency:

```bash
pip install 'krail[embeddings]'
krail --local vector build --provider sentence_transformers
```

If a model-backed provider is misconfigured, KRAIL keeps lexical and graph
results working and returns a clear error in the JSON response instead of
failing the whole search.

## What A Good First Run Looks Like

If KRAIL is working well for you, the first session should feel like this:

1. You initialize a local project in under a minute.
2. `doctor` tells you whether the workspace is healthy.
3. `capture` puts raw notes into a predictable inbox.
4. `search` finds the relevant local evidence.
5. `think` returns a usable answer envelope with citations and gaps.
6. `task` or `workflow` prepares agent work without losing project state.

## Permission And Security Boundary

KRAIL enforces repo-backed access rules when you go through the CLI, SDK, MCP
server, workflows, or launched runner adapters. Records stay public by default
unless they opt into restrictive metadata, and denied access plus allowed
access to restricted or sensitive repo records are written to
`research_plan/audit/access.jsonl`.

KRAIL does not isolate the host machine. Anyone with direct shell or filesystem
access to the repo can bypass KRAIL by reading or writing files outside those
mediated surfaces.

## Primary Use Cases

### Research workspaces

Track papers, methods, claims, gaps, and follow-up work in a durable local
project instead of scattered notes and ad hoc prompts.

### Software knowledge bases

Give coding agents a repo-backed memory layer with notes, decisions, prompts,
tasks, workflow history, and deterministic repo inventory.

### Private company or document brains

Keep internal sources, policy notes, owners, and stale-doc reviews local and
auditable.

## Local Runtime Status

Covered by the current CLI tests, MCP tests, or fixture smoke commands:

- local project scaffolding
- knowledge pack activation
- capture inbox
- inbox promotion and topic upsert
- deterministic hybrid search defaults with local hash embeddings, plus unified typed `find`
- deterministic `think` envelope
- markdown graph build/query/export
- repo-backed tasks, work orders, and session records
- public-by-default permissions doctor and access audit log for denied and
  restricted-record access
- listener templates, event logs, workflow triggers, and event replay
- software-map repo inspection commands and a bundled `examples/software-map`
  fixture
- queue-based ingestion, workflow dashboards, parameterized workflow inputs,
  and lightweight typed workflow outputs
- dependency-aware workflow DAGs with `needs`, parallel fan-out, retry policies,
  timeouts, conditions, loops, approvals, and child workflows
- dry-run and full dispatch to local CLIs
- MCP tools for find, search, think, capture, tasks, workflows, and integrity
- optional local FastAPI adapter using `.krail/store.json`

Explicitly outside the v1 promise for now:

- model-backed synthesis inside `think` remains opt-in and runner-backed
- embedding upgrades and reranking quality beyond the local hash default
- deeper graph-aware retrieval beyond the current deterministic graph signals
- external pack installation and registry ergonomics
- host-level isolation and managed security controls outside KRAIL-mediated
  surfaces

## MCP

`rail-mcp` exposes a local KRAIL project to MCP-compatible tools.

```bash
pip install -e packages/rail-py
pip install -e packages/mcp-server
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

This is a strong fit if you want local project knowledge available inside agent
tools without turning the project itself into a hosted service. MCP follows the
same KRAIL-mediated access policy; it is not a separate host sandbox.

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
- [Release Checklist](RELEASE.md)
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
