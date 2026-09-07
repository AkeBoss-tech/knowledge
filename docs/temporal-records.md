# Typed temporal records v1

`rail.temporal_records` is the minimum domain-neutral envelope requested by
KRAIL #16. It keeps entity identity and canonical authority separate from the
payload schema, preserves effective (`valid_from`/`valid_to`) and known
(`recorded_at`/`ingested_at`) time, and binds exact source/provenance refs,
writer family, revision, freshness, visibility, and an immutable digest.

The envelope distinguishes observation, estimate, reported claim, approved
state, hypothesis, and proposed change. `replay_temporal_records` gives stable
out-of-order replay and requires supplied exact supersession parents. Domain
payload validation and executable operators remain deferred to #21; unknown
payload schemas are therefore represented explicitly and are not silently
executed.

`query_temporal_records` separates effective time (`valid_at`) from known time
(`known_at`). It uses `ingested_at` when present, otherwise `recorded_at`, and
only applies a retrospective correction after that correction is known. A
query returns immutable applicable records in deterministic order. The current
envelope has no tombstone field, so deletion/tombstone semantics remain an
explicit follow-up rather than an inferred payload convention. This query is
a history view, not a materialized current-state projection. It validates the
complete supplied history before applying the knowledge cutoff, so a caller
cannot hide mixed-authority or corrupted future records by querying an earlier
time. Query output remains subject to caller authorization at the access
boundary; effective/known timestamps do not grant access.

`ProcedureRecord` composes into this envelope through
`procedure_temporal_record` without changing its existing wire shape. The
procedure instance version is carried as the envelope `revision`, while the
payload schema identity remains stable across revisions. Use
`procedure_temporal_history` for a chain: it resolves procedure supersession
links to the exact generated temporal-envelope digests required by generic
replay. A standalone superseding procedure is rejected rather than emitting a
link with the wrong digest domain. Robotics and company fixtures demonstrate
that the envelope does not encode a domain specific ontology. Core-issued
OpenSaddle command and environment refs remain opaque KRAIL evidence; KRAIL
does not activate them or grant access.
