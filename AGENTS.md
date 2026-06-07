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
- `state/`
- `artifacts/`

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
krail --local workflow run weekly_literature_refresh --dry-run
```

Dry runs write the work order and session command files without launching a
second agent process. Full dispatch can run local CLIs such as Codex CLI,
Claude Code, Gemini CLI, Cursor CLI, and GitHub Copilot CLI.

## Principle

Agents can explore freely, but trusted project state should be promoted into
explicit repo-backed records and pass integrity checks.
