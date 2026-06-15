# KRAIL

KRAIL is a local-first, repo-backed knowledge runtime for projects where agents
need durable context, not just chat history. It gives a project folder a
repeatable structure for captures, topic pages, source dependencies, lightweight
graphs, vector search, evidence-backed synthesis, workflow tasks, and integrity
records.

The PyPI distribution is named `krail`. The Python import namespace remains
`rail` for compatibility with earlier RAIL/KRAIL code.

```bash
pip install krail
```

```python
import rail

project = rail.local("./my-knowledge-project")
print(project.doctor())
```

## Why KRAIL?

Most retrieval tools stop at "here are the matching documents." KRAIL is built
around a fuller project loop:

```text
capture -> promote -> search -> think -> dispatch -> verify -> preserve
```

Use KRAIL when you want a local knowledge workspace that can:

- capture raw notes, files, URLs, and stdin into a predictable inbox
- promote useful captures into stable topic pages
- keep project knowledge in ordinary repo-backed markdown and YAML files
- build a markdown-frontmatter graph of topics, entities, claims, and links
- search local evidence deterministically
- build a local SQLite vector index for RAG-style retrieval
- synthesize answers with citations, gaps, conflicts, and next actions
- manage repo-backed tasks, work orders, workflows, and session records
- run local CLI agents such as Codex, Claude Code, Gemini, Cursor, or Copilot
- track source freshness, affected documents, claims, assumptions, and artifacts
- expose the same local project to MCP-compatible agent clients

KRAIL is intentionally headless. It does not require a hosted database, SaaS
control plane, frontend, or remote runner. A project folder plus the CLI is the
source of truth.

## Install

KRAIL requires Python 3.11 or newer.

```bash
pip install krail
```

Optional extras:

```bash
pip install "krail[local]"
pip install "krail[analysis]"
pip install "krail[embeddings]"
```

The extras enable heavier local capabilities:

- `local`: ontology and DuckDB helpers
- `analysis`: numerical and plotting dependencies
- `embeddings`: sentence-transformer embedding support

After installation, both commands point to the same CLI:

```bash
krail --version
rail --version
```

`krail` is the preferred command name. `rail` remains available for
compatibility.

## Quick Start

Create a new local knowledge project:

```bash
krail init robotics-kb --pack research-intelligence --mode markdown_graph
cd robotics-kb
krail --local doctor
```

Capture raw material:

```bash
krail --local capture "GCS may be useful as a feasibility layer for task plans"
krail --local capture --file ./paper-notes/gcs.md --type paper-note
echo "quick thought from stdin" | krail --local capture --stdin
```

Review the inbox:

```bash
krail --local inbox list
```

Promote useful material into durable topic pages:

```bash
krail --local inbox promote topics/inbox/<capture>.md \
  --topic task-and-motion-planning \
  --type method

krail --local topic upsert task-and-motion-planning \
  --content "Reviewed update with evidence."
```

Search local evidence:

```bash
krail --local search "GCS feasibility" --explain
```

Build and inspect the markdown graph:

```bash
krail --local graph build
krail --local graph entities --type Package
krail --local graph edges --entity PDDLStream
krail --local graph docs --topic task-and-motion-planning
```

Build a local vector index:

```bash
krail --local vector build
krail --local vector search "dual-arm planning benchmark"
krail --local search "dual-arm planning benchmark" --rag --explain
```

Ask KRAIL to synthesize from project evidence:

```bash
krail --local think "What changed in task and motion planning?"
```

The `think` command returns an answer envelope that can include citations,
supporting evidence, gaps, conflicts, source freshness, affected documents, graph
context, vector hits, and suggested next actions.

## Local Project Layout

A KRAIL project is just a directory with repo-backed knowledge files. Typical
projects contain:

```text
krail.yaml or rail.yaml
.ontology/
topics/
topics/inbox/
sources/
research_plan/
research_plan/state/
artifacts/
.krail/
```

The important distinction is:

- `topics/inbox/` stores raw captures that still need triage
- `topics/` stores durable topic pages and reviewed knowledge
- `sources/` stores source records and dependency metadata
- `research_plan/` stores tasks, work orders, workflows, decisions, and sessions
- `artifacts/` stores outputs that can be checked or promoted
- `.krail/` stores local runtime state such as pack selection and vector indexes

Raw captures are not treated as trusted knowledge by default. Promote material
only after it is useful, supported, and shaped into stable project records.

## Knowledge Modes And Packs

KRAIL supports project modes that tune the ontology, workflow defaults, and
agent prompts for different kinds of knowledge work.

Inspect the active mode and pack:

```bash
krail --local mode active
krail --local mode list
krail --local pack active
krail --local pack validate
```

Built-in modes include:

- `research`: papers, methods, datasets, experiments, claims, evidence, and open questions
- `company`: teams, systems, policies, workflows, owners, metrics, decisions, and stale docs
- `personal`: projects, areas, resources, ideas, documents, and notes
- `software`: services, modules, APIs, dependencies, decisions, incidents, and risks
- `project`: milestones, decisions, artifacts, risks, blockers, and closeout

## CLI Overview

The CLI has two operating styles:

- local mode, using files from a project directory
- API mode, connecting to a KRAIL-compatible local API runtime

Most local commands look like this:

```bash
krail --local --path /path/to/project <command>
```

If you are already inside the project directory, this is enough:

```bash
krail --local <command>
```

Common commands:

```bash
krail init <directory>
krail --local doctor
krail --local capture "note"
krail --local inbox list
krail --local topic upsert <topic>
krail --local search "query" --explain
krail --local think "question"
krail --local graph build
krail --local graph check
krail --local vector build
krail --local sources validate
krail --local sources check
krail --local integrity status
```

Agent and workflow commands:

```bash
krail --local agent list
krail --local agent run "summarize new captures" --runner codex_cli --dry-run
krail --local task create "Audit sources" --description "Check stale docs"
krail --local task list
krail --local task dispatch <task_id> --dry-run
krail --local workflow list
krail --local workflow run weekly_literature_refresh --dry-run
```

Prefer dry runs when dispatching agents or workflows. Dry runs write the work
order and session command files without launching a second agent process.

## Python API

KRAIL can be used directly from Python.

### Local project mode

```python
import rail

project = rail.local("./robotics-kb")

health = project.doctor()
print(health["status"])

results = project.search("graph of convex sets", explain=True)
for hit in results["results"]:
    print(hit["path"], hit.get("score"))

answer = project.think("What evidence do we have about GCS feasibility?")
print(answer["answer"])
```

### API mode

```python
import rail

project = rail.connect(
    "nj-economics",
    api_url="http://localhost:8000/api/v1",
)

df = project.query(
    "SELECT county_name, unemployment_rate "
    "FROM County "
    "ORDER BY unemployment_rate DESC "
    "LIMIT 10"
)

print(df)
```

### Streaming agent responses

```python
import rail

project = rail.connect("nj-economics")

for event in project.agent.ask(
    "Compare Hudson and Bergen County unemployment trends",
    stream=True,
):
    if event["type"] == "text_delta":
        print(event["text"], end="", flush=True)
```

## Search Versus Think

Use `search` when you need raw evidence.

```bash
krail --local search "customer onboarding workflow" --explain
```

Use `think` when you need a cited answer shape with explicit gaps, conflicts,
and next actions.

```bash
krail --local think "what changed in onboarding this week?"
```

Do not promote generated statements into trusted project state until they are
registered as claims with evidence and pass the project integrity checks.

## MCP Server

KRAIL projects can be exposed to MCP-compatible clients with the companion MCP
server package.

Install from the monorepo during development:

```bash
pip install -e packages/mcp-server
```

Run against a local project:

```bash
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

Useful MCP tool families include:

- `search`: retrieve evidence
- `think`: synthesize evidence with gaps and conflicts
- `capture`: add local notes or source pointers
- `mode_active` and `mode_list`: inspect operating mode
- `inbox_list` and `inbox_promote`: triage captures
- `topic_list` and `topic_upsert`: manage durable topic pages
- `doctor`: inspect local project health
- `pack_active`: inspect the active knowledge pack
- `create_task`, `list_tasks`, and `dispatch_task`: manage worker tasks
- `list_workflows` and `run_workflow`: create workflow tasks from the active pack

## Source Freshness And Integrity

KRAIL includes local checks for source dependency freshness and research
integrity records.

```bash
krail --local sources validate
krail --local sources check
krail --local sources affected
krail --local integrity status
krail --local integrity sources
krail --local integrity claims
```

These commands help separate raw notes from trusted project state. The intended
workflow is to capture freely, promote carefully, and keep claims tied to
evidence.

## Current Status

KRAIL is suitable for pilot projects, local knowledge bases, agent workflow
experiments, and research/company/project memory prototypes.

Ready now:

- local project scaffolding
- capture inbox and topic promotion
- deterministic local search
- markdown-frontmatter graph build/query/export
- local SQLite vector database
- deterministic `think` evidence envelopes
- project health checks
- source dependency checks
- repo-backed tasks, work orders, workflows, and session state
- local CLI runner discovery and dry-run dispatch
- Python client for local and API-backed projects

Still maturing:

- model-backed synthesis and reranking
- external pack installation
- production-grade sandbox enforcement
- remote permission scopes
- hosted UI and managed deployments

## Development

From the repository root:

```bash
pip install -e packages/rail-py -e packages/mcp-server
PYTHONPATH=packages/rail-py:packages/mcp-server pytest -q packages/rail-py/tests
```

Build the PyPI package:

```bash
python -m pip install --upgrade build twine
python -m build packages/rail-py
python -m twine check packages/rail-py/dist/*
```

## Links

- Repository: https://github.com/AkeBoss-tech/knowledge
- Issues: https://github.com/AkeBoss-tech/knowledge/issues
- PyPI: https://pypi.org/project/krail/

## License

MIT
