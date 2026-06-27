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
krail --local listener show watched_site
krail --local listener test watched_site
krail --local listener poll watched_site
krail --local listener poll --all
krail --local listener daemon

krail --local event list
krail --local event show evt_...
krail --local event replay evt_... --dry-run
```

`listener test` observes without writing state or events. `listener poll`
updates listener state, records deduped events, and invokes workflow triggers
unless `--no-execute` is set.

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

This lets KRAIL support common listeners out of the box while still allowing
project-specific adapters for Slack, databases, calendars, and other systems.
