# KRAIL MCP Server

`rail-mcp` exposes a local KRAIL project to MCP-compatible agents and clients.
It is an adapter over the repo-backed knowledge engine, not the source of truth.

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

