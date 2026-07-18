# KRAIL Integration Guides

These guides connect a local KRAIL project to the agent or workflow where the
work already happens. KRAIL stays the repo-backed source of truth; the client
gets permission-aware access to search, capture, `think`, tasks, workflows,
and integrity checks through the CLI or MCP.

## Choose A Starting Point

- [KRAIL + Codex](codex.md): add a local KRAIL MCP server to Codex and give a coding task durable project context.
- [KRAIL + Claude Code](claude-code.md): use a project-scoped or personal MCP configuration with Claude Code.
- [KRAIL + Cursor](cursor.md): connect Cursor to the KRAIL project through `.cursor/mcp.json`.
- [KRAIL MCP Server](mcp-server.md): install, configure, test, and secure the local stdio adapter.
- [KRAIL For Literature Reviews](literature-reviews.md): retain papers, claims, gaps, and research questions across agent sessions.
- [KRAIL For Software Architecture Memory](software-architecture-memory.md): build a durable architecture map instead of rediscovering a codebase every session.

## Shared Local Setup

Every guide assumes Python 3.11+ and a KRAIL project containing `rail.yaml` or
`krail.yaml`. Create a research workspace with:

```bash
python -m pip install 'krail[local]'
krail init my-krail-project --pack research-intelligence --mode markdown_graph
cd my-krail-project
krail --local doctor
```

For agent integrations, also install the MCP adapter. `rail-mcp` is not yet a
separate PyPI project, so install its released source package from GitHub:

```bash
python -m pip install \
  'git+https://github.com/AkeBoss-tech/knowledge.git@v1.1.1#subdirectory=packages/mcp-server'
rail-mcp --help
```

Use an absolute local project path in a personal configuration. For a
checked-in project configuration, use the client-specific workspace variable
shown in its guide. Never add credentials or private paths to a shared config.

## First Health Check

Once connected, ask the client to call `mcp_contract` and `doctor` before it
uses broader KRAIL tools. A useful first request is:

> Inspect this KRAIL project with `doctor`, then use `search` to find the most
> relevant evidence for my question. Cite the returned project paths and call
> out gaps instead of inventing facts.

The stable KRAIL v1 MCP contract is documented in the [MCP server guide](mcp-server.md).
