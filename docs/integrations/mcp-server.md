# KRAIL MCP Server

`rail-mcp` is a local stdio server that exposes a KRAIL project to
MCP-compatible clients. It does not host or copy the project elsewhere: the
client reads and writes the same repo-backed KRAIL workspace through the KRAIL
permission model.

## Install

Install the local KRAIL runtime and MCP adapter with Python 3.11+:

```bash
python -m pip install 'krail[local]'
python -m pip install \
  'git+https://github.com/AkeBoss-tech/knowledge.git@v1.1.12#subdirectory=packages/mcp-server'
rail-mcp --help
```

`krail` is published on PyPI. The `rail-mcp` package is currently installed
from the KRAIL GitHub source because its separate PyPI publisher is not yet
configured.

## Start Against A Local Project

First create or choose a KRAIL workspace:

```bash
krail init my-project --pack research-intelligence --mode markdown_graph
krail --local --path my-project doctor
```

Then start the server manually for a transport check:

```bash
rail-mcp --local --path "$(pwd)/my-project"
```

The server uses stdio by default. It will appear to wait when run in a terminal;
that is expected because an MCP client owns the conversation. Stop it with
`Ctrl-C` after confirming that it starts without an error.

## Configure A Client

Every stdio client needs the same executable and arguments:

```text
command: rail-mcp
args: --local --path /absolute/path/to/krail-project
```

See the ready-to-copy client configurations for [Codex](codex.md),
[Claude Code](claude-code.md), and [Cursor](cursor.md).

## Verify From The Client

Ask the client to call these tools in order:

1. `mcp_contract` — confirms the stable v1 tool boundary.
2. `doctor` — checks the local KRAIL project.
3. `search` — retrieves project evidence for a concrete question.
4. `integrity_status` — shows what is ready to trust and what needs review.

If `doctor` reports that no project is loaded, confirm the configured path
contains `rail.yaml` or `krail.yaml`. If the client cannot launch `rail-mcp`,
run `rail-mcp --help` in the same environment and use an absolute path to the
executable if necessary.

## Security Boundary

The MCP server applies KRAIL's repo-backed permissions to MCP-mediated access.
It does not sandbox the host or prevent a process with ordinary filesystem
access from reading a project directly. Treat write-capable tools such as
capture, topic updates, task creation, and workflows as actions requiring
review in your agent client.

KRAIL's stable v1 tool families are `contract`, `doctor`, search and `find`,
`think`, capture/topic/inbox operations, tasks, workflows, integrity, and
`permissions_doctor`. Other exposed tools are experimental and may change.

For the exhaustive current contract, see the [MCP package README](../../packages/mcp-server/README.md).
