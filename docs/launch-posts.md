# Launch Posts

These drafts are designed to be edited lightly and posted.

## X / LinkedIn Post

I kept running into the same problem with AI agents: the work disappears into
chat history.

So I built KRAIL: a local-first, repo-backed workspace for agents that need
durable memory, evidence, tasks, and workflows instead of disposable context.

The basic flow is:

- capture notes and source pointers locally
- search project evidence
- run `think` to get a cited answer envelope
- prepare auditable tasks and workflow runs for other agents

It works with local projects and can be exposed to MCP clients like Codex,
Claude Code, and Cursor.

Repo: https://github.com/AkeBoss-tech/knowledge

I would especially love feedback from people building agent workflows, local
research tools, or MCP integrations.

## Show HN Draft

Title:

Show HN: KRAIL, a local-first memory and workflow layer for AI agent projects

Body:

I built KRAIL because I wanted something between "throw files into chat" and
"stand up a whole hosted knowledge system."

KRAIL is a repo-backed local workspace for agent projects. It lets you keep
sources, notes, claims, tasks, workflow runs, and prompts in a project folder,
then query that state with commands like `search`, `think`, `task`, and
`workflow`.

The part I care about most is making agent work more durable and auditable:

- raw notes land in a predictable inbox
- `search` retrieves local evidence first
- `think` returns a cited answer envelope with gaps and next actions
- tasks and workflows create repo-backed work records before another agent runs

It also exposes local projects over MCP for tools like Codex, Claude Code, or
Cursor.

The v1 contract is intentionally local-first and repo-backed. I would love
feedback on the onboarding flow, the core mental model, and whether the
repo-backed agent workflow feels useful.

Repo: https://github.com/AkeBoss-tech/knowledge

## Reddit / Niche Community Post

I’ve been working on a local-first tool called KRAIL for AI agent projects.

The problem I’m trying to solve is that serious project work does not fit well
into chat memory alone. Notes, source pointers, workflows, and agent outputs
end up scattered or disappearing entirely.

KRAIL uses a repo-backed local project as the working memory layer:

- capture notes and source pointers
- search local evidence
- run `think` for a cited answer envelope
- create auditable task/workflow records before dispatching another agent

It can also expose that project to MCP clients.

I’m not looking for generic promotion here. I’d really like feedback on:

1. whether the workflow makes sense
2. where the onboarding is confusing
3. whether the value is clear quickly enough

Repo: https://github.com/AkeBoss-tech/knowledge

## Blog Post

Full draft:

[Blog Post Draft](blog-post.md)
