# KRAIL MCP Server

`rail-mcp` exposes a local KRAIL project to MCP-compatible agents and clients.
It is the bridge that lets tools like Codex, Claude Code, Cursor, and other
MCP clients query a repo-backed local knowledge workspace instead of relying on
ephemeral chat context alone.

Use it when you want:

- local project memory instead of repeated file uploads
- typed `find`, search, and `think` over a KRAIL workspace from an MCP client
- public-by-default permission checks and `permissions_doctor`
- capture, tasks, workflows, and project health checks exposed to agents

The MCP server is an adapter over the repo-backed knowledge engine, not the
source of truth.

## Stable V1 Tools

The v1 compatibility promise applies only to the tool families below. These are
the tools we expect clients to build against for KRAIL v1 readiness.

- `doctor`: `doctor`
- `search`: `search`, `find`
- `think`: `think`, `register_think_result`, `think_sessions`, `think_session_status`
- `capture`: `capture`, `topic_list`, `topic_upsert`, `inbox_list`, `inbox_promote`
- `tasks`: `create_task`, `list_tasks`, `dispatch_task`
- `workflows`: `list_workflows`, `workflow_templates`, `init_workflow`, `show_workflow`, `validate_workflow`, `run_workflow`, `execute_workflow`, `workflow_runs`, `workflow_status`, `workflow_dashboard`
- `integrity`: `integrity_status`, `integrity_assumptions`, `integrity_sources`, `integrity_claims`, `integrity_claim_candidates`, `integrity_artifacts`, `integrity_promote_claim_candidate`, `integrity_reproducibility_rerun`, `integrity_freshness_evaluate`, `integrity_source_detail`, `integrity_claim_detail`, `integrity_verification_runs`, `integrity_benchmark`, `integrity_stale_graph`, `integrity_promote_artifact`, `integrity_artifact_detail`, `integrity_graph`, `integrity_retrieve`, `integrity_rerun_plan`
- `permissions`: `permissions_doctor`

Stable tools return JSON on success and should return actionable JSON error
payloads for invalid input, project/configuration problems, permission denials,
and common runtime failures instead of raw Python tracebacks where feasible.

## Experimental Tools

Everything not listed in the stable v1 section is experimental and excluded
from the compatibility promise for now. That currently includes:

- ontology and entity tools such as `list_classes`, `get_entities`, `search_entities`
- graph, vector, and source-maintenance tools
- mode, pack, agent-scaffolding, and repository inspection helpers
- listeners, events, queues, and other automation-oriented tools
- SQL, Python execution, analysis plugins, registry discovery, and hydration
- runner-session protocol tools and secret-management tools

Experimental tools may change shape, move behind narrower permissions, or be
removed before a broader post-v1 contract is declared.

## Local Usage

Install the package from the repository root:

```bash
pip install -e packages/rail-py
pip install -e packages/mcp-server
```

Run the server against a local project:

```bash
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

Useful tool families include search, think, capture, local vector search,
markdown graph inspection, ontology access, workflow dispatch, SQL queries,
and project health checks.

## Package Compatibility

`rail-mcp` should track the same pre-v1 KRAIL line as the repo-backed engine it
adapts. Until the core package reaches `1.0`, this package intentionally
depends on the current `0.2.x` line rather than claiming a `1.x` contract it
cannot yet guarantee independently.

## Permission Model

`rail-mcp` enforces KRAIL's local, repo-backed permission model when clients go
through MCP tools. In practice that means:

- missing metadata stays public-by-default for backward compatibility
- restrictive frontmatter and manifest rules can hide records or block actions
- audit records for sensitive allows and denials stay in the project repo

The MCP server is not a separate source of authority. It mediates access to the
same repo-backed project state the CLI and SDK use.

## Work Orders And Scope

When MCP tooling launches or inspects runner work, the work order may include a
structured `capability_envelope` alongside legacy fields such as
`capabilities_required` and `allowed_paths`.

That envelope is designed to be incremental:

- it narrows a session and is meant to be intersected with repo policy
- it records write paths today and reserves tool/secret scope for adapter work
- it is auditable through repo files and dispatch logs

It does not provide host-level isolation on its own. A user with direct shell
or disk access can still bypass MCP and read files outside KRAIL-mediated
surfaces.

## Project Layout

The server expects a local KRAIL project with:

- `krail.yaml` or `rail.yaml`
- `.ontology/`
- `state/`
- `artifacts/`
- optional `.krail/vector.sqlite` for local vector search
