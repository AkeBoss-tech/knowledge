# Agent Guide

KRAIL should be used locally first.

## Local Project

```python
import rail

project = rail.local("/path/to/project")
```

Expected project files:

- `krail.yaml` or `rail.yaml`
- `.ontology/`
- `topics/`
- `sources/`
- `research_plan/`
- `research_plan/state/`
- `artifacts/`

## Knowledge Modes

Start by inspecting the active operating mode:

```bash
krail --local mode active
krail --local pack active
krail --local doctor
```

Built-in modes include:

- `research`: papers, methods, datasets, experiments, claims, evidence, and open questions
- `company`: teams, systems, policies, workflows, owners, metrics, decisions, and stale docs
- `personal`: projects, areas, resources, ideas, documents, and random notes
- `software`: services, modules, APIs, dependencies, decisions, incidents, and risks
- `project`: milestones, decisions, artifacts, risks, blockers, and closeout

Use `research_plan/` for operations such as tasks, sessions, decisions, and
workflow state. Durable domain knowledge belongs under `topics/`.

## Inbox And Topics

Raw captures are not the final knowledge shape. Put raw notes in the inbox, then
promote useful material into stable topic pages.

```bash
krail --local capture "raw note" --topic robotics --entity PDDLStream --entity-type Package
krail --local inbox list
krail --local inbox promote topics/inbox/<capture>.md --topic task-and-motion-planning --type method
krail --local topic upsert task-and-motion-planning --content "Reviewed update with evidence."
krail --local graph build
```

Prefer `inbox promote` and `topic upsert` over manually creating dated files or
new folders. If material is weak, duplicate, or unsupported, record the gap
instead of promoting it as trusted knowledge.

## Optional API

The API is an adapter, not the source of truth.

```bash
make api
```

Default URL:

```text
http://localhost:8000/api/v1
```

Operational records are stored in `.krail/store.json` unless `LOCAL_STORE_PATH`
is set.

## MCP

Install:

```bash
pip install -e packages/mcp-server
```

Run against a local project:

```bash
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

Useful tool families:

- `search`: retrieve evidence
- `think`: synthesize evidence with gaps/conflicts
- `capture`: add local notes or source pointers to `topics/inbox`
- `mode_active`, `mode_list`: inspect the knowledge operating mode
- `inbox_list`, `inbox_promote`: triage raw captures into stable topics
- `topic_list`, `topic_upsert`: inspect or update durable topic pages
- `doctor`: inspect local project health
- `pack_active`: inspect active knowledge pack
- `list_agents`: inspect local CLI runners
- `create_task`, `list_tasks`, `dispatch_task`: manage repo-backed worker tasks
- `list_workflows`, `run_workflow`: create workflow tasks from the active pack
- ontology classes and entities
- SQL queries over DuckDB artifacts
- Python execution
- hydration
- integrity status

## Search vs Think

Use `search` when you need raw evidence. Use `think` when you need a cited
answer shape with explicit gaps, conflicts, and next actions. Do not promote
generated statements into trusted state until they are registered as claims with
evidence and pass integrity checks.

## Agent Dispatch

Prefer dry-run dispatch first:

```bash
krail --local agent run "summarize new captures" --runner codex_cli --dry-run
krail --local workflow init weekly_literature_refresh
krail --local workflow execute weekly_literature_refresh --dry-run
```

Dry runs write the work order and session command files without launching a
second agent process. Full dispatch can run local CLIs such as Codex CLI,
Claude Code, Gemini CLI, Cursor CLI, and GitHub Copilot CLI.

## Principle

Agents can explore freely, but trusted project state should be promoted into
explicit repo-backed records and pass integrity checks.
