# KRAIL + Claude Code

Connect Claude Code to a local KRAIL project when you want it to search and
maintain durable project knowledge through MCP instead of relying only on the
current conversation.

## 1. Install The Local Adapter

```bash
python -m pip install 'krail[local]'
python -m pip install \
  'git+https://github.com/AkeBoss-tech/knowledge.git@v1.1.12#subdirectory=packages/mcp-server'

krail init my-project --pack research-intelligence --mode markdown_graph
cd my-project
krail --local doctor
```

## 2. Add A Project-Scoped Server

From the KRAIL project root, tell Claude Code to create a shared `.mcp.json`
entry:

```bash
claude mcp add --scope project --transport stdio krail -- \
  rail-mcp --local --path '${CLAUDE_PROJECT_DIR:-.}'
```

Claude Code writes the equivalent project configuration:

```json
{
  "mcpServers": {
    "krail": {
      "command": "rail-mcp",
      "args": ["--local", "--path", "${CLAUDE_PROJECT_DIR:-.}"]
    }
  }
}
```

Commit `.mcp.json` only when the KRAIL project is intended to be shared and it
contains no credentials or private machine paths. Claude Code asks each user to
approve project-scoped MCP servers; that approval is intentional.

For a private or different KRAIL project, use a personal absolute path instead:

```bash
claude mcp add --scope local --transport stdio krail -- \
  rail-mcp --local --path /absolute/path/to/my-project
```

## 3. Validate In Claude Code

Run `/mcp` to check that `krail` is connected. Then ask:

> Use KRAIL `doctor` and `search` before editing. Find the existing decision
> records for this change, cite the project paths, and describe the smallest
> safe implementation.

## Useful Workflow

1. Ask Claude Code to search existing KRAIL topics before planning.
2. Capture unreviewed findings in the inbox.
3. Promote only source-backed material into a stable topic.
4. Run `integrity_status` before using a generated answer as a project decision.

## Portable Dynamic Workflows

Claude Code dynamic workflows use runtime-only JavaScript helpers such as
`agent()` and `pipeline()`. KRAIL can import the documented, declarative subset
without executing JavaScript, then run the resulting workflow with any
configured local runner:

```bash
krail --local workflow import-claude .claude/workflows/audit-routes.js --max-items 25
krail --local workflow suggest-runner audit-routes
krail --local workflow execute audit-routes --dry-run
```

The importer accepts `meta`, `await agent(...)`, and `await pipeline(items,
item => agent(...))`. It rejects imports, filesystem or process access, dynamic
evaluation, and unsupported JavaScript rather than attempting to emulate the
Claude runtime. The generated spec is stored under
`research_plan/workflows/` with `flow.format: krail-flow/v1`; edit its agent
steps or runner policy to choose Codex, Cursor, Claude, Gemini, or another
registered harness.

Register a project-specific prompt-taking harness without changing KRAIL core:

```yaml
agents:
  harnesses:
    antigravity:
      command: antigravity-agent
      description: Antigravity local agent harness
  runner_policy:
    preferred: [antigravity, codex_cli]
```

KRAIL appends the work-order prompt as the final command argument for a custom
harness. Use `krail --local agent list` and `workflow suggest-runner` to verify
that its executable is available before dispatching work.

## Troubleshooting

- `claude mcp list` should show `krail`; `/mcp` shows connection and tool status inside a session.
- If project configuration is pending, open Claude Code in the repository and approve the MCP server after reviewing the command.
- `${CLAUDE_PROJECT_DIR:-.}` keeps the checked-in configuration portable. Use an absolute path only in a personal configuration.
- Run `rail-mcp --help` directly if Claude Code cannot find the executable.

Claude Code documents local stdio servers, `.mcp.json`, scopes, and project approval at <https://code.claude.com/docs/en/mcp>.
