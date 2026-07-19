# Dataset Catalog

Use `sources/datasets.yaml` to declare large, ontology-backed sources without
hydrating every source row into OWL. Raw datasets remain authoritative; later
cache and semantic-projection steps consume this catalog.

```yaml
version: 1
datasets:
  - id: customer-events
    format: csv                 # csv, json, jsonl, parquet, or sqlite
    path: sources/events.csv    # repository-relative
    primary_key: event_id
    refresh: append_only        # manual, append_only, replace, or scheduled
    partition_by: event_date    # advisory metadata for a future cache builder
    projection:                 # optional: a bounded ontology projection
      class: CustomerEvent
      uri: event-{event_id}
```

SQLite datasets must also declare `table` or `query`. For JSON and Parquet,
declare `schema` when schema inspection cannot be inferred cheaply.

```bash
krail --local datasets validate
krail --local datasets list
krail --local datasets snapshot
krail --local datasets cache-build
krail --local datasets cache-status
krail --local datasets cache-validate
krail --local datasets cache-benchmark customer-events --iterations 5
```

`snapshot` records content and schema fingerprints in
`research_plan/state/dataset_snapshots.json`. It reads files incrementally to
hash them, but does not load them into pandas or hydrate ontology individuals.

`cache-build` writes a rebuildable DuckDB file to
`artifacts/data/datasets.duckdb`. Each dataset becomes a `data_<dataset-id>`
table. The primary key and optional `indexes` fields create DuckDB indexes;
the build state in `research_plan/state/dataset_cache.json` records the exact
source content and schema hashes. `cache-status` compares those hashes with
the live source and reports whether each cached table is fresh.
`cache-validate` additionally verifies the recorded table and row count; use it
as a release or deployment gate. `cache-benchmark` reports repeatable
count-query latency against a selected real cached dataset, so teams can record
their own representative scale target rather than rely on a synthetic claim.

## Query routing

Use `query routed` for bounded read-only SQL results with backend provenance:

```bash
# Uses the dataset cache because data_events is a cached table.
krail --local query routed 'SELECT category, count(*) FROM data_events GROUP BY 1'

# Force the ontology's DuckDB mirror.
krail --local query routed 'SELECT * FROM Organization' --backend ontology

# Query an authoritative SQLite dataset without copying it first.
krail --local query source source-events 'SELECT * FROM source_events'
```

The automatic router chooses the dataset cache only when a SQL `FROM` or
`JOIN` clause names a registered cache table; otherwise it chooses the
ontology DuckDB mirror. Results are limited to 100 rows by default (maximum
10,000) and include the backend and source/cache lineage used.

## Bounded semantic hydration

For a semantic projection that must create ontology individuals, declare the
controls on the pipeline step rather than on the raw dataset:

```yaml
- name: hydrate_recent_events
  api: events
  class: Event
  uri: event-{event_id}
  batch_size: 10000
  incremental:
    watermark: updated_at
  entity_resolution:
    key: "{source_system}:{event_id}"
    scope: events
```

With `hydration_mode: incremental`, KRAIL persists the high-water mark in
`research_plan/state/hydration_progress.json` and source-key-to-canonical-URI
mappings in `.ontology/entity_resolution.sqlite`. The input must be ordered or
otherwise safely comparable by its watermark; late-arriving records older than
that value require a deliberate backfill.
