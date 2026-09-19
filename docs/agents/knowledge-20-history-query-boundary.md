# #20 history-query storage boundary

`TabletopWorldMemory.object_history()` now bounds returned rows and its
continuation token, but it does **not** bound work. The existing JSON semantic
store exposes `get(exact_id)` and whole-kind `list()` only. Its in-memory
entity map avoids scanning unrelated objects after refresh, but an object with
many observations still has every matching record materialized, sorted, and
authorized before a page marker can be disclosed.

The reproducible benchmark command was run on the current code and the same
host/runtime recorded by the #20 baseline:

```sh
PYTHONPATH=packages/rail-py /private/tmp/krail-temporal.zSLchI/bin/python \
  scripts/benchmark_robotics_spatial_projection.py \
  --objects 4 --observations 8 --scenes 1 --repeats 2 --output /tmp/history-8.json
PYTHONPATH=packages/rail-py /private/tmp/krail-temporal.zSLchI/bin/python \
  scripts/benchmark_robotics_spatial_projection.py \
  --objects 4 --observations 24 --scenes 1 --repeats 2 --output /tmp/history-24.json
```

| Observations for `object-0` | Materialized/authenticated rows | Returned rows | p50 / p95 ms |
| --- | ---: | ---: | ---: |
| 8 | 8 | 8 | 2.2824 / 2.2889 |
| 24 | 24 | 24 | 5.8562 / 5.9060 |

The benchmark records these values as `reads.history`; they are work counters,
not a performance target. JSON write setup is also linear-file rewrite work,
so these small deterministic scales are intentionally reported instead of a
misleading large-fixture latency claim.

## Proposed replaceable read model

Do not migrate canonical `temporal_record` rows or make SQLite authoritative.
An opt-in local adapter can build a disposable SQLite file from the declared
tenant/project canonical scope. Its only durable derived rows are:

```text
history_entry(
  tenant_id, project_id, entity_authority, entity_id,
  valid_from, recorded_at, record_digest,
  PRIMARY KEY (tenant_id, project_id, record_digest)
)
INDEX history_entity_time(
  tenant_id, project_id, entity_authority, entity_id,
  valid_from, recorded_at, record_digest
)
history_watermark(tenant_id, project_id, canonical_scope_cursor, build_digest)
```

`record_digest` is an exact canonical reference lookup key. The index contains
no pose, evidence payload, asset bytes, store path, or credentials. Its
`build_digest` commits the ordered indexed digest set and its canonical scope
cursor. Rebuild reads canonical temporal rows, validates temporal integrity,
and replaces the derived file atomically. It can always be deleted.

A narrow domain-facing interface would be `TemporalHistoryReadModel`:

```text
read_page(scope, entity_authority, entity_id, valid_from, valid_to,
          known_at, after_order, limit) -> {record_digests, snapshot_digest,
                                             scope_cursor, has_more}
```

It returns exact digests only. `TabletopWorldMemory` then loads each canonical
record through the existing tenant/project-scoped semantic repository,
revalidates its bitemporal predicate and integrity, checks invalidation and
the live `WorldReader`, and produces the existing `ObjectHistory` model. A
watermark/canonical cursor mismatch, missing digest, duplicate entry, malformed
entry, or partial rebuild must fall back to the existing canonical path; it can
never be treated as an empty history or a complete page.

The current reader contract is reference-by-reference. Therefore, preserving
the no-existence-leak rule requires authorizing every matching row before
revealing `has_more`; that remains unbounded even with a selective metadata
index. A later bounded end-to-end design needs an explicit, signed
history-query grant covering the exact world/object/time scope before the
index can reveal a page marker. It must be a separate reviewed authorization
contract, not inferred from SQLite access or a cached reader decision. Until
then, this adapter can bound metadata selection and canonical record loading
for a caller already granted the full query scope, but cannot replace the safe
reference-by-reference public path.

## Decision

No SQLite file, schema migration, or provider contract is added in this slice.
The JSON store cannot perform the required entity/time predicate lookup, and a
derived index without the scoped authorization contract would either leak
history existence or silently weaken live authorization. The current public
path remains canonical and conservative; the interface above is the additive,
replaceable boundary for the next reviewed storage slice.
