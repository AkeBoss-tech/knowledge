# KRAIL + Codex

Use KRAIL with Codex when a coding or research task needs durable local context
instead of repeatedly rediscovering files, decisions, sources, and prior work.
Codex connects to KRAIL through a local MCP stdio server.

## 1. Prepare The KRAIL Project

```bash
python -m pip install 'krail[local]'
python -m pip install \
  'git+https://github.com/AkeBoss-tech/knowledge.git@v1.1.12#subdirectory=packages/mcp-server'

krail init my-project --pack research-intelligence --mode markdown_graph
krail --local --path my-project doctor
```

Use an existing KRAIL project instead of creating a new one when it already
contains the repository's knowledge, topics, and workflow state.

## 2. Add The MCP Server To Codex

Add this to `~/.codex/config.toml` for a personal configuration. Replace the
path with the absolute path to the KRAIL project:

```toml
[mcp_servers.krail]
command = "rail-mcp"
args = ["--local", "--path", "/absolute/path/to/my-project"]
startup_timeout_sec = 20
default_tools_approval_mode = "writes"
```

For a repository-specific setup, put the same table in
`.codex/config.toml` in that repository. Keep the KRAIL project path personal
if it is outside the repository or contains private material.

Restart Codex or start a new task after changing its configuration. The Codex
configuration reference documents the `command`, `args`, `env`, and timeout
fields for local stdio MCP servers.

## 3. Give Codex A Narrow First Request

Start with a request that requires evidence but does not write anything:

> Call `mcp_contract` and `doctor` for the KRAIL project. Then search for the
> architecture decisions related to authentication, summarize only what the
> evidence supports, cite the KRAIL paths, and list any unresolved gaps.

Then use write-capable tools deliberately:

> Capture this decision in the KRAIL inbox, propose the target topic, and do
> not promote it until I review the evidence.

## Good Fits

- Keep a codebase map, ADRs, incidents, and ownership notes available across Codex tasks.
- Search a local literature review before a research implementation task.
- Create a reviewable KRAIL work order before dispatching a larger agent task.
- Run `integrity_status` before treating a generated summary as trusted project knowledge.

## Troubleshooting

- Run `rail-mcp --help` from the same shell to confirm the executable is on `PATH`.
- Use an absolute `--path`; the configured process does not assume the task's current directory.
- If no tools appear, verify the server is enabled in `config.toml`, start a new Codex task, and check `doctor` against the project path.
- KRAIL does not replace Codex's filesystem sandbox or approval settings. Keep those controls configured independently.

See [KRAIL MCP Server](mcp-server.md) for installation and permission details.
