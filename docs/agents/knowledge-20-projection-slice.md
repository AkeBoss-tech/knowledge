# #20 local current-state and spatial projection slice

`rail.spatial_current_projection.SpatialCurrentProjection` is an in-memory,
rebuildable grid projection over immutable `robotics.world-memory`
`TemporalRecord` values. It neither creates a canonical store nor reads or
copies external asset bytes: the exact `ResourceRef` remains in the canonical
world record.

The projection is an internal candidate selector, not an authorized answer
surface. A consumer must resolve its exact `record_ref` through
`TabletopWorldMemory` with the current `WorldReader`; that owner enforces
record lineage authorization and its projection invalidation/tombstone logic.
This avoids claiming that a derived grid can independently decide reader
revocation, source invalidation, or observation freshness.

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

The fixture uses 0.1 m cells and deterministic 2026-09-07 timestamps. Its
assertions record work counts rather than machine-specific latency claims:

| Synthetic case | Actual work asserted |
| --- | --- |
| 100 current objects; query x=0..0.02 | 1 grid cell and 10 candidate rows read; 3 exact hits |
| Current state by ID | 1 materialized row read; no temporal history replay |
| Move one object with 2 historical records | 2 history rows read, 2 index rows touched (remove plus insert) |
| Same local move with 500 extra distinct buckets | still 2 index rows touched |
| Restart | canonical records plus move rebuild to the identical regional candidate result |
| 100 m cubed query | rejects as `too-large` before allocating a grid-cell tuple, above the 4,096-cell budget |

The projection returns `current-candidates` for a known frame with an empty
region and `abstained` for a frame/world/revision with no currently materialized
records. It reports candidate rows and cells inspected on every regional query.

## Requirement evidence and remaining work

| #20 requirement | Evidence in this slice | Status |
| --- | --- | --- |
| Bounded affected index state after movement | exact identity-to-bucket map; local removal/insertion; scaling regression | Met for RAM grid projection. |
| Current latest state avoids whole event history | `current_candidate()` is one materialized identity lookup | Met for this projection. |
| External assets remain references | `SpatialHit` has only canonical record ref; module never dereferences asset refs | Met. |
| Rebuildable spatial projection/restart | constructor rebuild and deterministic restart regression | Met for RAM grid. |
| Valid/known time, deletion/revocation/invalidation | frozen snapshot and estimate/current filtering; disclosure delegates to owning world-memory reader/invalidation path | Partial: no direct projection subscription or tombstone index. |
| World/session isolation and abstention | world/frame/revision buckets, authority-qualified IDs, duplicate-ID regression, no-frame abstention | Partial: world isolation covered; session is not in current object records and remains a world-memory query concern. |
| Full benchmark fixture | two object/bucket scales and recorded work counts | Partial: no p50/p95, history/appearance/scene/asset byte or delayed-update-rate benchmark. |
| R-tree/ANN/relationship indexes, SQLite tier, live ROS/MoveIt integration | Not introduced | Remaining #20 work. |

The grid intentionally has a fixed query-cell budget; broad regions return
`too-large` rather than expanding memory or scanning every object. It is not a
replacement for a production R-tree or a public provider operation.
