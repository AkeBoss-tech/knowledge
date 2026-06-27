# Listeners And Event Triggers

KRAIL listeners turn local or external changes into durable project events, then
optionally trigger repo-backed workflows.

```text
listener observes change -> event is recorded -> trigger invokes workflow
```

Listener specs live under `research_plan/listeners/`. Runtime cursors and
hashes live under `.krail/listener_state.json`. Events are written to
`research_plan/events/` so they can be inspected, committed, or replayed.

## Commands

```bash
krail --local listener list
krail --local listener templates
krail --local listener init website_change_monitor --id watched_site
krail --local listener validate watched_site
krail --local listener doctor
krail --local listener show watched_site
krail --local listener test watched_site
krail --local listener poll watched_site
krail --local listener poll --all
krail --local listener daemon
krail --local listener serve --port 8787

krail --local event list
krail --local event show evt_...
krail --local event replay evt_... --dry-run
```

`listener test` observes without writing state or events. `listener poll`
updates listener state, records deduped events, and invokes workflow triggers
unless `--no-execute` is set.

`listener doctor` checks enabled listeners, validation errors, missing workflow
targets, failing listeners, stale success timestamps, unhandled events, large
event logs, and listener lock files.

## Website Hash Watcher

Use the built-in `http` listener to watch a page or raw endpoint for changes.

```yaml
id: karpathy_llm_wiki
type: http
url: https://gist.githubusercontent.com/karpathy/442a6bf555914893e9891c11519de94f/raw
interval: 1h
change_detection:
  mode: hash
  normalize: readable_text
on_change:
  workflow: refresh_llm_wiki_notes
  dry_run_first: true
```

The first poll records a baseline by default. Set `emit_initial: true` if the
first observation should also create an event.

## Event Context In Workflows

When a listener invokes a workflow with `mode: execute` or the default trigger
mode, KRAIL passes first-class event inputs into the workflow:

```yaml
inputs:
  event_id: evt_...
  event:
    source: http.url.changed
    payload:
      target: https://example.com
      old_hash: sha256:...
      new_hash: sha256:...
```

Workflow steps can interpolate that context with the KRAIL expression syntax:

```yaml
id: refresh_source_notes
steps:
  - id: capture_url
    kind: command
    run: krail --local capture --url "${{ inputs.event.payload.target }}"
```

## GitHub Polling Listener

The built-in `github` listener polls through the `gh api` CLI, which keeps the
first version local-first and avoids webhook tunnel setup.

```yaml
id: github_issue_triage
type: github
repo: owner/repo
events:
  - issues.opened
  - pull_request.opened
  - check_suite.completed
ref: HEAD
interval: 5m
on_change:
  workflow: triage_github_issue
  dry_run_first: true
```

Supported GitHub event families in this first polling adapter:

- `issues.opened`
- `pull_request.opened`
- `check_suite.completed`

## Filesystem Listener

```yaml
id: new_sources
type: file
glob: sources/**/*.pdf
on_change:
  workflow: ingest_new_sources
```

## Custom Listener

Use `type: command` when a source is project-specific. The command should print
a JSON object or list of objects. Each object can include `source`, `target`,
`changed`, and `hash`.

```yaml
id: custom_metric_alert
type: command
run: python scripts/check_metric.py
on_event:
  workflow: metric_diagnostic
```

Python adapters can use the small helper:

```python
import json
from rail.listeners import emit_event

print(json.dumps(emit_event(
    source="linear.issue.created",
    target="LIN-123",
    payload={"title": "Investigate metric drop"},
)))
```

This lets KRAIL support common listeners out of the box while still allowing
project-specific adapters for Slack, databases, calendars, and other systems.

## Webhook Foundation

`krail --local listener serve` starts a local HTTP receiver that records posted
JSON events. This is intentionally minimal: it provides a development foundation
for webhook adapters, while production webhook auth, replay protection, and
tunnel management should be added before exposing it publicly.
