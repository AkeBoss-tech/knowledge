# KRAIL + Cursor

Use KRAIL with Cursor to give the agent a local, durable project record: source
notes, decisions, codebase maps, captured findings, and integrity status remain
in the repository rather than in one chat session.

## 1. Install The Runtime And Adapter

```bash
python -m pip install 'krail[local]'
python -m pip install \
  'git+https://github.com/AkeBoss-tech/knowledge.git@v1.0.0#subdirectory=packages/mcp-server'

krail init my-project --pack research-intelligence --mode markdown_graph
krail --local --path my-project doctor
```

## 2. Add `.cursor/mcp.json`

When the Cursor workspace is also the KRAIL project, create
`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "krail": {
      "type": "stdio",
      "command": "rail-mcp",
      "args": ["--local", "--path", "${workspaceFolder}"]
    }
  }
}
```

Cursor also supports a personal `~/.cursor/mcp.json`. Use that file with an
absolute KRAIL path when the knowledge workspace is separate from the project
currently open in Cursor:

```json
{
  "mcpServers": {
    "krail-research": {
      "type": "stdio",
      "command": "rail-mcp",
      "args": ["--local", "--path", "/absolute/path/to/research-project"]
    }
  }
}
```

Do not commit a user-specific absolute path or any credentials.

## 3. Test The Connection

Open **Customize** in Cursor and ensure the `krail` server is enabled. Then ask
the agent:

> Call KRAIL `doctor`, search the local knowledge base for the relevant
> architecture decisions, and cite the KRAIL paths before proposing a change.

Cursor asks for MCP tool approval by default. Keep that approval enabled for
capture, topic, task, workflow, and other write-capable KRAIL tools.

## Useful Prompts

- “Search KRAIL for the current authentication decision and its evidence before changing this endpoint.”
- “Inspect `integrity_status`, then tell me which sources are stale before writing a research summary.”
- “Capture the outcome of this debugging session in the inbox; do not promote it yet.”

## Troubleshooting

- Run `rail-mcp --help` in Cursor's terminal to validate the executable.
- Verify the configured path contains `rail.yaml` or `krail.yaml`.
- Use `${workspaceFolder}` only when that folder is the KRAIL project.
- Disable and re-enable the server in **Customize** after changing `mcp.json`.

Cursor documents local stdio MCP configuration and `.cursor/mcp.json` at <https://cursor.com/docs/mcp>.
