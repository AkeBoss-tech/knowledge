# KRAIL MCP Server

`rail-mcp` exposes a local KRAIL project to MCP-compatible agents and clients.
It is the bridge that lets tools like Codex, Claude Code, Cursor, and other
MCP clients query a repo-backed local knowledge workspace instead of relying on
ephemeral chat context alone.

Use it when you want:

- local project memory instead of repeated file uploads
- search and `think` over a KRAIL workspace from an MCP client
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

## Project Layout

The server expects a local KRAIL project with:

- `krail.yaml` or `rail.yaml`
- `.ontology/`
- `state/`
- `artifacts/`
- optional `.krail/vector.sqlite` for local vector search
