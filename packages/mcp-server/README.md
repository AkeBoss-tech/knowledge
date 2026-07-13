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

`rail-mcp` 1.0.0 provides the stable local-runtime v1 tool contract below. This
is not a claim that every MCP-exposed surface is frozen; unlisted tools remain
experimental and the hosted API and engine packages are outside this contract.

The v1 compatibility promise applies only to the tool families below. These are
the tools we expect clients to build against for KRAIL v1 readiness.

- `contract`: `mcp_contract`
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

Clients can discover this boundary at runtime by calling `mcp_contract`. The
tool does not require a project to be loaded and returns the stable groups and
tool names, the currently exposed experimental tool names, and the stable JSON
error shape. Pass `contract_version="v1"` (the default); unsupported versions
also return an actionable JSON error payload.

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

Install the local KRAIL runtime from PyPI, then install the MCP adapter from
the released GitHub source. `rail-mcp` is not yet a separately published PyPI
project:

```bash
pip install 'krail[local]'
pip install 'git+https://github.com/AkeBoss-tech/knowledge.git@v1.0.0#subdirectory=packages/mcp-server'
```

For local development from a repository checkout:

```bash
pip install -e 'packages/rail-py[local]'
pip install -e packages/mcp-server
```

Run the server against a local project:

```bash
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

For ready-to-copy client configuration, see the [KRAIL integration guides](../../docs/integrations/README.md).

Useful tool families include search, think, capture, local vector search,
markdown graph inspection, ontology access, workflow dispatch, SQL queries,
and project health checks.

## Current Status

`rail-mcp` follows the same 1.x local-runtime release line as `krail`.

Available in 1.0.0:

- local-project search, `find`, and deterministic `think`
- capture, inbox promotion, topic upserts, and project health checks
- source dependency, graph, vector, workflow, and task surfaces
- repo-backed permission checks for MCP-mediated reads and writes

Only the stable tool families listed above are part of the v1 contract.
Everything else remains experimental, including:

- hosted API-backed deployments as a stable compatibility target
- host-level sandboxing or production-grade isolation outside repo-mediated permissions
- long-term compatibility guarantees for workflow/runner integrations

## Package Compatibility

`rail-mcp` tracks the same major KRAIL line as the local runtime it adapts. The
1.0.0 package therefore depends on `krail>=1.0.0,<2.0.0`; a future incompatible
local-runtime contract requires a new major dependency range.

## Permission Model

`rail-mcp` enforces KRAIL's local, repo-backed permission model when clients go
through MCP tools. In practice that means:

- missing metadata stays public-by-default for backward compatibility
- restrictive frontmatter and manifest rules can hide records or block actions
- denied access and allowed access to restricted or sensitive repo records are
  audited into the project repo

The MCP server is not a separate source of authority. It mediates access to the
same repo-backed project state the CLI and SDK use.

## Work Orders And Scope

When MCP tooling launches or inspects runner work, the work order may include a
structured `capability_envelope` alongside legacy fields such as
`capabilities_required` and `allowed_paths`.

That envelope is designed to be incremental:

- it narrows a session and is meant to be intersected with repo policy
- it records write paths, tool names, and secret names for adapter enforcement
- it is auditable through repo files and dispatch logs

It does not provide host-level isolation on its own. A user with direct shell
or disk access can still bypass MCP and read files outside KRAIL-mediated
surfaces.

## Project Layout

The server expects a local KRAIL project with:

- `krail.yaml` or `rail.yaml`
- `.ontology/`
- `topics/`
- `sources/`
- `research_plan/`
- `research_plan/state/`
- `artifacts/`
- optional `.krail/vector.sqlite` for local vector search
