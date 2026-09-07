# #20 local current-state and spatial projection slice

`rail.spatial_current_projection.SpatialCurrentProjection` is an in-memory,
rebuildable grid projection over immutable `robotics.world-memory`
`TemporalRecord` values. It neither creates a canonical store nor reads or
copies external asset bytes: the exact `ResourceRef` remains in the canonical
world record.

`TabletopWorldMemory.prepare_spatial_snapshot()` explicitly admits the RAM
projection for one `(valid_at, known_at)` snapshot and reports its full rebuild
work. `objects_in_region()` uses it only to narrow candidates, then performs
the existing exact-record `WorldReader` lineage authorization and
invalidation/tombstone checks before returning an answer. It falls back to the
canonical path whenever the grid could hide an owner-level `unknown` or
`stale` result (expired/non-current estimate, old observation, or a different
frame/map revision). This avoids claiming that a derived grid independently
decides reader revocation, source invalidation, or observation freshness.
Same-process `record()` publication incrementally applies its exact canonical
record to an admitted grid, including a late-ingested record inside the fixed
known-time cutoff. Reopening uses persisted canonical inputs and requires a
fresh explicit admission; no RAM index is persisted.

## Rebuild and snapshot rules

The declared authority is the supplied immutable temporal-record sequence. A
new process rebuilds all grid cells from that sequence; no index data is
persisted and no input record is rewritten. `valid_at` and `known_at` form a
frozen bitemporal snapshot. `advance_snapshot()` rebuilds before a later query;
mutating either public timestamp without advancing causes a query failure
instead of silently returning candidates from an old snapshot. Expired
estimates and explicitly non-current records are omitted. The world/frame/map
revision are part of every bucket, and the indexed identity is
`(entity_authority, object_id)`, so duplicate object IDs in distinct worlds do
not collide.

## Reproducible measured baseline

Run the focused fixture with:

```sh
PYTHONPATH=packages/rail-py /private/tmp/krail-temporal.zSLchI/bin/python -m pytest -q packages/rail-py/tests/test_spatial_current_projection.py
```

The reproducible opt-in timing benchmark is:

```sh
PYTHONPATH=packages/rail-py /private/tmp/krail-temporal.zSLchI/bin/python \
  scripts/benchmark_robotics_spatial_projection.py \
  --output docs/agents/knowledge-20-benchmark-baseline.json
```

The committed [baseline JSON](knowledge-20-benchmark-baseline.json) records
its dated Python/platform assumptions, exact command, fixture scale, measured
p50/p95 values, raw-grid microbenchmark, update/rebuild work and time,
serialized metadata bytes, restart equality, and unavailable operations.
Public and raw timings are measured at the same admitted snapshot. A delayed
observation's expected `unknown`/`stale` abstention is reported separately from
an incorrect obsolete result.

The same fixture also times authorized appearance-gallery, finite-fixture
object-history, scene reads, and scene diffs, and records their returned
reference/record counts. `object_history()` now has an explicit `limit` from 1
through 128 (default 128). A full page returns `truncated=true` and a
snapshot-bound continuation; the final page returns `truncated=false` and no
continuation. The cursor binds world/object, valid interval, known cutoff,
canonical scope cursor, ordered exact record digests, and the last returned
digest. It is rejected after a canonical mutation or when reused with another
query. Before it reveals either a partial marker or cursor, the reader must be
authorized for the entire matching snapshot, and each disclosed page is
checked again immediately before return. This bounds output, while the current
implementation still materializes, orders, and authorizes all matching history
rows; `valid_to=None` remains an unbounded query domain. Assets stay immutable
references with zero embedded bytes; serialized
metadata accounting is the retention boundary measured here. A public
appearance ANN remains unavailable and is reported as such rather than inferred
from snapshot rows.

[Two-scale read profiling](knowledge-20-read-profile.json) shows the remaining
owner-path boundary clearly: forced refresh of 16 and 32 objects parses 48 and
96 temporal rows respectively, while the prepared spatial query selects 4 and
7 exact candidate histories. A canonical tenant/project scope cursor is
advanced atomically with temporal ingest, aliases, tombstones, scenes, and
episodes; unchanged-cursor reads avoid reparsing those rows, while an
interleaved-writer regression proves a local write cannot fast-forward past an
unseen external row. The current optimization rebuilds
authority-qualified exact-ref and entity-history maps after every refresh, so
each selected candidate no longer linearly scans all refreshed records. It does
not bypass current reader authorization or invalidation checks. The temporal
change ledger selectively loads exact immutable rows after an
external cursor change; a regression verifies one external row parses one row
while a repeated current read parses zero. Full rebuild remains the conservative
fallback for old stores, oversized ledger gaps, or non-temporal cached changes.

The fixture uses 0.1 m cells and deterministic 2026-09-07 timestamps. Its
assertions record work counts rather than machine-specific latency claims:

| Synthetic case | Actual work asserted |
| --- | --- |
| 100 current objects; query x=0..0.02 | 1 grid cell and 10 candidate rows read; 3 exact hits |
| Current state by ID | 1 materialized row read; no temporal history replay |
| Move one object with 2 historical records in an admitted fixed snapshot | 2 history rows read, 2 index rows touched (remove plus insert) |
| Advance the 100-object snapshot before that later movement | 100 history rows read and 100 index rows touched; explicitly a full rebuild |
| Empty admitted snapshot then same-cutoff publication | exact new record becomes visible without a second rebuild, in memory and after persisted reopen/re-admission |
| Same local move with 500 extra distinct buckets | still 2 index rows touched |
| Restart | canonical records plus move rebuild to the identical regional candidate result |
| 100 m cubed query | rejects as `too-large` before allocating a grid-cell tuple, above the 4,096-cell budget |

The full snapshot advance/re-admission is intentionally measured as a rebuild,
not claimed to be a bounded movement update. The projection returns
`current-candidates` for a known frame with an empty region and `abstained` for
a frame/world/revision with no currently materialized records. It reports
candidate rows and cells inspected on every regional query.

## Requirement evidence and remaining work

| #20 requirement | Evidence in this slice | Status |
| --- | --- | --- |
| Bounded affected index state after movement | exact identity-to-bucket map; local removal/insertion; scaling regression | Met for RAM grid projection. |
| Current latest state avoids whole event history | `current_candidate()` is one materialized identity lookup | Met for this projection. |
| External assets remain references | `SpatialHit` has only canonical record ref; module never dereferences asset refs | Met. |
| Rebuildable spatial projection/restart | constructor rebuild and deterministic restart regression | Met for RAM grid. |
| Valid/known time, deletion/revocation/invalidation | explicit snapshot admission; same-cutoff canonical writes incrementally apply; authorized `objects_in_region` uses candidate refs then existing reader/invalidation checks; expired/frame-mismatched cases and any active invalidation conservatively fall back | Partial: no direct projection subscription or tombstone index. |
| World/session isolation and abstention | world/frame/revision buckets, authority-qualified IDs, duplicate-ID regression, no-frame abstention | Partial: world isolation covered; session is not in current object records and remains a world-memory query concern. |
| Full benchmark fixture | deterministic p50/p95 public/raw region, appearance, finite-fixture history, scene and scene-diff reads; metadata bytes, delayed abstention, restart and two scales | Partial: history output is page-bounded but its current implementation is not work-bounded; no large asset retention policy or broad full query matrix. |
| Appearance similarity | opt-in bounded cosine descriptors and rebuildable exact RAM search with live world-reader checks | Partial: exact search only; ANN/vector acceleration and visual-quality evaluation remain absent. |
| R-tree/ANN/relationship indexes, SQLite tier, live ROS/MoveIt integration | Not introduced | Remaining #20 work. |

The grid intentionally has a fixed query-cell budget; broad regions return
`too-large` rather than expanding memory or scanning every object. It is not a
replacement for a production R-tree or a public provider operation.
