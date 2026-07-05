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
- `state/`
- `artifacts/`
- optional `.krail/vector.sqlite` for local vector search
