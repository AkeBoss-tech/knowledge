# KRAIL For Software Architecture Memory

Use KRAIL's software mode to build an inspectable local map of services,
modules, dependencies, ownership, decisions, and recent changes. This gives an
agent durable architecture context without asking it to rediscover the codebase
from scratch every session.

## Start A Software Knowledge Project

Create the KRAIL workspace next to the codebase, or use the shipped
[`examples/software-map`](../../examples/software-map/README.md) as a
reference:

```bash
python -m pip install 'krail[local]'
krail init architecture-map \
  --knowledge-mode software \
  --pack software-architecture \
  --mode markdown_graph

krail --local --path architecture-map doctor
```

## Build The Deterministic Map First

Point the software project at the repository you want to understand:

```bash
krail --local --path architecture-map repo snapshot /absolute/path/to/service
krail --local --path architecture-map repo inventory /absolute/path/to/service
krail --local --path architecture-map repo symbols /absolute/path/to/service
krail --local --path architecture-map repo owners /absolute/path/to/service
krail --local --path architecture-map repo dependencies /absolute/path/to/service
```

Then capture a decision while it is still fresh:

```bash
krail --local --path architecture-map capture \
  'Decision: keep authentication policy evaluation in the API gateway. Reason: it centralizes audit logs and avoids duplicating rules in each service.' \
  --topic authentication-architecture \
  --entity 'Authentication gateway decision' \
  --entity-type Decision

krail --local --path architecture-map inbox list
```

Review the capture before promoting it into a stable topic. The inbox-to-topic
step distinguishes an unreviewed session note from a maintained architecture
record.

## Use The Map During Coding Work

Before an agent changes a service, ask it to:

1. Run `doctor` and inspect the active KRAIL mode.
2. Search for the service, dependencies, owners, and prior decisions.
3. Explain the paths and evidence it found before editing.
4. Capture any new decision or risk in the inbox after verification.

Example request:

> Search KRAIL for the service's dependencies, owners, and relevant ADRs. Cite
> the paths, identify what is stale or unknown, then propose the smallest safe
> implementation plan. Capture the final decision in the inbox after tests pass.

## Keep It Current

Use the software workflows to avoid a one-time architecture diagram that goes
stale:

```bash
krail --local --path architecture-map workflow show map_codebase
krail --local --path architecture-map workflow show sync_recent_changes
krail --local --path architecture-map repo changed /absolute/path/to/service --base-ref origin/main
krail --local --path architecture-map integrity status
```

Connect this project to [Codex](codex.md), [Claude Code](claude-code.md), or
[Cursor](cursor.md) when an agent should use the map directly.
