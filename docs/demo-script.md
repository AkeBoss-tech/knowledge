# Demo Script

This is the recommended short demo for KRAIL.

## Goal

Show that KRAIL gives an agent a durable local workspace with evidence, search,
and auditable next actions in under 3 minutes.

## Setup

```bash
./scripts/install-rail.sh
source .venv/bin/activate
krail init robotics-kb --pack research-intelligence --mode markdown_graph
cd robotics-kb
```

That command now seeds the first repo-backed outputs you want to show right
away, including `topics/inbox` and `research_plan/graph/*`.

If you want a lower-risk public recording path, use the synthetic fixture:

```bash
./scripts/demo-minimal-project.sh
```

That route uses [examples/minimal-project/README.md](../examples/minimal-project/README.md)
and copies the fixture into a temp workspace before running the full smoke path.

## Script

### 1. Show the workspace health check

```bash
krail --local doctor
```

Talking point:

"KRAIL starts from a local project, not a chat session. `doctor` tells you if
the workspace is healthy before the agent touches anything."

### 2. Capture raw working memory

```bash
krail --local capture "GCS may be useful as a feasibility layer for LLM task plans"
echo "Need evidence on recent task-and-motion planning approaches" | krail --local capture --stdin
krail --local inbox list
```

Talking point:

"Raw notes go into a predictable inbox instead of disappearing into prompt
history, and you can immediately see the repo-backed capture files."

### 3. Search local evidence

```bash
krail --local search "task and motion planning" --explain
```

Talking point:

"Search retrieves project evidence first. We are not asking the agent to guess
what the repo knows."

### 4. Generate a cited answer envelope

```bash
krail --local think "What changed in task and motion planning?"
```

Talking point:

"`think` packages an answer with citations, freshness, and gaps, so the output
is closer to a working research record than a disposable chat reply."

### 5. Build the graph

```bash
krail --local graph build
krail --local graph entities
```

Talking point:

"When your notes have frontmatter, KRAIL can turn them into a lightweight graph
you can inspect and reuse."

### 6. Prepare auditable agent work

```bash
krail --local workflow list
krail --local workflow execute weekly_research_review --dry-run
```

Talking point:

"Instead of jumping straight into a second agent process, KRAIL creates an
auditable work order first."

## Closing Line

"KRAIL is the repo-backed memory and workflow layer between your files and your
agents."

## Recording Notes

- keep the demo under 3 minutes
- show terminal output, not just commands
- narrate the problem being solved at each step
- keep the language user-facing and avoid architecture deep dives
- prefer the minimal-project fixture when you want a stable recording
