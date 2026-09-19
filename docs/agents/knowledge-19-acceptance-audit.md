# Knowledge #19 acceptance audit

Audited against the original GitHub issue wording on 2026-09-07. This file is
an implementation-evidence map; it does not replace or reinterpret the issue.

## Domain requirements

| Original requirement | Source evidence | Test evidence | Status and boundary |
| --- | --- | --- | --- |
| Persistent object identity | `WorldObject`, `TabletopWorldMemory.record`, and world-scoped `entity_authority` in `packages/rail-py/rail/robotics_world_memory.py` | `test_registry_fixture_identity_and_structural_scene_refs`; `test_observed_estimated_occlusion_and_world_isolation` | Implemented. Identity is an explicit `world_id` plus `object_id`; it is not inferred from appearance. |
| Object observations/detections | `record(..., kind="observation")` creates immutable `TemporalRecord` rows | `test_observed_estimated_occlusion_and_world_isolation`; `test_late_arriving_observation_is_not_known_before_trusted_recorded_time` | Implemented as observations; no live detector/perception ingest adapter is supplied. |
| Appearance observations | `AppearanceObservation`, `record_appearance`, and `appearance_gallery` | `test_persisted_appearance_gallery_and_identity_candidates_are_bitemporal_and_never_merge`; `test_hosted_appearance_and_identity_candidate_writes_use_projection_writer` | Implemented with exact asset, optional crop/mask, descriptor, viewpoint/context, quality, and occlusion refs/metadata. Binary bytes are external. |
| Object state estimates | `record(..., kind="estimate")`, `recalibrate`, estimate expiry, and `location` | `test_observed_estimated_occlusion_and_world_isolation`; `test_estimates_require_a_future_expiry`; `test_canonical_store_reopens_world_isolation_and_expiring_estimate` | Implemented. Estimates are additional records and require expiry. |
| Pose/geometry references, units, frames | `Pose` has `metres`, normalized quaternion, `frame_id`, `map_revision`, observation time, uncertainty, revision | `test_observed_estimated_occlusion_and_world_isolation`; `test_persisted_regions_relations_and_bounded_same_frame_membership` | Implemented for poses and axis-aligned regions. No transform-tree implementation or tf2 transport integration is included. |
| Support/containment/attachment relations | `SpatialRelation`, `record_relation`, `spatial_relations` | `test_persisted_regions_relations_and_bounded_same_frame_membership` creates and retrieves all three relation types | Implemented. The deterministic tabletop episode fixture itself contains only a support relation; containment and attachment are covered by the separate persisted-relations test. |
| Places/regions | `PlaceRegion`, `record_region`, `objects_in_region` | `test_persisted_regions_relations_and_bounded_same_frame_membership` | Implemented as bounded axis-aligned regions with explicit frame/map revision. |
| Scene snapshots | `SceneSnapshot`, `scene`, `scene_at` | `test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing`; `test_scene_and_episode_historical_queries_hide_future_constituents` | Implemented with exact object references and bitemporal visibility. |
| Episodes/interactions | `SceneEpisode`, `episode`, `episode_at` | `test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing`; `test_scene_and_episode_historical_queries_hide_future_constituents` | Implemented as linked scene histories. Interaction semantics beyond snapshots are not modeled. |
| Prediction/action outcome records where useful | No prediction or action-outcome record type exists | `ActionFreshness` is exercised in `test_persisted_regions_relations_and_bounded_same_frame_membership` | Omitted under the issue's optional “where useful” wording. `ActionFreshness` is a derived read-only suitability result, not a prediction or action-outcome record. |

## Spatial and temporal requirements

| Original requirement | Source evidence | Test evidence | Status and boundary |
| --- | --- | --- | --- |
| Reference frame | `Pose.frame_id`; `PlaceRegion.frame_id` | `test_persisted_regions_relations_and_bounded_same_frame_membership` | Implemented. Frame mismatch abstains rather than being transformed or guessed. |
| Measurement/effective time | `Pose.observed_at`; temporal `valid_from` / `valid_to` | `test_late_arriving_observation_is_not_known_before_trusted_recorded_time`; `test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing` | Implemented. |
| Map/frame revision | `Pose.map_revision`; `PlaceRegion.map_revision`; `map_revision_ref` | `test_recalibration_uses_canonical_parent_for_immediate_transitive_invalidation`; `test_persisted_regions_relations_and_bounded_same_frame_membership` | Implemented as explicit metadata and invalidation dependency; no tf2 transform graph is duplicated. |
| Units/convention | `Pose.metres` and module docstring state SI metres | `Pose` validation is exercised throughout the world-memory suite | Implemented for positions. Quaternion convention is named `quaternion_xyzw`. |
| Uncertainty | `Pose.uncertainty_metres` | `Pose` construction in all location fixtures | Implemented as required non-negative metres. Explicit absence is not supported because the field is required. |
| Estimator/source provenance | `TemporalRecord.source_refs`, evidence refs, estimate parent refs | `test_recalibration_uses_canonical_parent_for_immediate_transitive_invalidation`; `test_canonical_dependency_invalidation_respects_historical_cutoffs_and_rebuild` | Implemented as exact refs and temporal dependencies. |

## Appearance and identity requirements

| Original requirement | Source evidence | Test evidence | Status and boundary |
| --- | --- | --- | --- |
| Bounded informative gallery | `appearance_gallery(..., limit=8)` | `test_persisted_appearance_gallery_and_identity_candidates_are_bitemporal_and_never_merge` | Implemented for returned gallery size. Ingest volume is not capped and no gallery-selection/index policy exists. |
| Original asset/crop/mask and descriptive metadata | `AppearanceObservation` and `record_appearance` | `test_persisted_appearance_gallery_and_identity_candidates_are_bitemporal_and_never_merge` | Implemented as immutable references and metadata; no asset-byte store. |
| Similarity produces candidates, not merges | `IdentityCandidate`, `propose_identity_candidates`, `resolve_identity` | `test_tabletop_episode_registry_dispatch_and_no_identity_merge`; `test_hosted_identity_resolution_binds_current_writer_and_full_read_dependency_chain` | Implemented. A resolution records an explicit reviewed selection for one candidate. It does not merge objects. |
| Class lookup behavior after resolution | `locate_class` only evaluates current object records by class | `test_tabletop_episode_registry_dispatch_and_no_identity_merge` only asserts ambiguity before/alongside `resolved_identity` | Not implemented or demonstrated as a post-resolution class-lookup change. Resolution is retrieved through `resolved_identity`; `locate_class` does not consult resolutions. |

## Scene, world, and fixture requirements

| Original requirement | Source evidence | Test evidence | Status and boundary |
| --- | --- | --- | --- |
| Place distinct from scene, episode, and reusable pattern/procedure | Separate `PlaceRegion`, `SceneSnapshot`, `SceneEpisode`, and relation/record models | `test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing` | Place, scene, and episode are separate. A robotics pattern/procedure abstraction is not added by this issue. |
| Real/simulated/hypothetical isolation | explicit `world_id` and `session_id`; hosted adapter validates one world scope | `test_observed_estimated_occlusion_and_world_isolation`; hosted authorization tests | Implemented as world/session identifiers and hosted-world scoping. The fixture uses one real-like tabletop world; simulation/hypothesis fixtures are not supplied. |
| Two visually similar objects | `tabletop_fixture` creates `cup-left` and `cup-right`, both `red-cup` | `test_registry_fixture_identity_and_structural_scene_refs`; `test_tabletop_episode_registry_dispatch_and_no_identity_merge` | Implemented. |
| Initial table observation | `tabletop_fixture` opening records and opening scene | `test_tabletop_episode_registry_dispatch_and_no_identity_merge` | Implemented. |
| Occlusion | `tabletop_episode_fixture` creates `occluded` scene containing an estimate and right cup | `test_tabletop_episode_registry_dispatch_and_no_identity_merge` | Implemented as fixture event. |
| Move while unseen | fixture records an estimate at x=0.5 after the opening location | `test_tabletop_episode_registry_dispatch_and_no_identity_merge` | Implemented as an estimate, not a raw movement sensor event. |
| Later re-observation in a new location | fixture records x=0.45 `obs-2` with `table-map-2` | `test_tabletop_episode_registry_dispatch_and_no_identity_merge` | Implemented. |
| Ambiguous association before final resolution | fixture writes `IdentityCandidate` before `IdentityResolution` | `test_tabletop_episode_registry_dispatch_and_no_identity_merge` | Implemented. |

## Required-query audit

| Required concept | Implemented source | Named test | Status and boundary |
| --- | --- | --- | --- |
| `last_observed_location(object)` | `location(..., estimated=False)` | `test_observed_estimated_occlusion_and_world_isolation`; `test_tabletop_episode_registry_dispatch_and_no_identity_merge` | Semantically present, though not under that exact method name. |
| `estimated_location(object, at=...)` | `location(..., estimated=True)` | `test_canonical_store_reopens_world_isolation_and_expiring_estimate`; `test_tabletop_episode_registry_dispatch_and_no_identity_merge` | Semantically present. |
| `evidence_for_location(object)` | `LocationAnswer.evidence`; `object_history(...).evidence` | `test_location_rechecks_exact_evidence_access`; `test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing` | No dedicated method with this exact name; evidence is returned by location/history. |
| `objects_in_region(region, at=...)` | `objects_in_region` | `test_persisted_regions_relations_and_bounded_same_frame_membership` | Implemented. |
| `scene_snapshot(place, at=...)` | `scene_at` | `test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing` | Semantically present. |
| `object_history(object)` | `object_history` | `test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing` | Implemented with caller-supplied valid-time window and known-time cutoff. The result row count is unbounded when `valid_to=None`; do not describe it as hard-bounded. |
| Freshness/validity for downstream action | `action_freshness` | `test_persisted_regions_relations_and_bounded_same_frame_membership` | Implemented as derived suitability, not action execution/outcome storage. |

## Original acceptance criteria

| Criterion | Source and test evidence | Audit result |
| --- | --- | --- |
| “Last seen” and “believed current location” return different answers when appropriate. | `location` distinguishes observation, estimate, stale, and unknown. `test_observed_estimated_occlusion_and_world_isolation` and `test_tabletop_episode_registry_dispatch_and_no_identity_merge`. | Met. |
| The system can abstain/return unknown rather than pretending an old pose is current. | `location` returns `unknown` after an old observation with no estimate; estimates expire to `stale`. `test_observed_estimated_occlusion_and_world_isolation`, `test_canonical_store_reopens_world_isolation_and_expiring_estimate`. | Met. |
| Similar-looking objects do not silently merge identities. | Candidate/resolution types and `locate_class` ambiguity; `test_tabletop_episode_registry_dispatch_and_no_identity_merge`. | Met. Explicit resolution remains separate from class lookup. |
| Recalibration/map-revision fixtures can update derived poses without rewriting raw observations. | `recalibrate`, map-revision refs, invalidation, temporal records; `test_recalibration_uses_canonical_parent_for_immediate_transitive_invalidation`. | Met. |
| Scene snapshots structurally share unchanged state rather than copying every object/asset. | Snapshot `object_refs`, episode `scene_refs`; `test_persisted_scene_episode_and_object_history_queries_preserve_structural_sharing` proves the unchanged right-cup ref is reused across scenes. | Met for object refs. Asset byte deduplication is outside this implementation. |
| All outputs can explain their evidence and version dependencies. | Typed results include exact refs and readers re-authorize dependencies; `test_authorization_is_rechecked_before_location_scene_and_ambiguity_outputs`, `test_hosted_identity_resolution_binds_current_writer_and_full_read_dependency_chain`. | Substantially met for implemented domain outputs. A systematic output-by-output lineage audit and provider-facing explain endpoint are not present. |

## Exact hosted proof and remaining gaps

`test_hosted_identity_resolution_binds_current_writer_and_full_read_dependency_chain`
proves current signed write/read, caller-swapped world/object denial, expired writer
denial, reader denial when raw evidence is missing, invalidated review evidence
suppression, and revoked-reader denial. `reviewer_id` is documented in
`resolve_identity` as asserted metadata; the signed writer is the authenticated
actor.

Focused verification at commit `3464d3a`:

```sh
PYTHONPATH=packages/rail-py /private/tmp/krail-temporal.zSLchI/bin/python -m pytest \
  packages/rail-py/tests/test_robotics_hosted_authorization.py \
  packages/rail-py/tests/test_robotics_world_memory.py
```

Result: 30 passed in 0.64s.

Unverified or intentionally absent: ROS/MoveIt/tf2 transport integration, live
perception ingestion, a binary asset store, action/prediction outcome records,
transform-tree calculations, a bounded default object-history result, scalable
spatial/vector indexes, performance benchmarks, and a provider-v2 or opt-in
rich-domain contract. These are relevant to #20 and #22, not evidence that
#19 already satisfies them.

## Proposed next bounded slice after #19

Read #20 and #22 together before implementing storage optimization. First add
the #22 design artifact: an opt-in, versioned read-only rich-domain capability
proposal with JSON fixtures for immutable asset references, bitemporal query
parameters, frame/revision/bounds, authorization/lineage, capability
negotiation, and explicit v1 compatibility. Use one robotics and one company
example. Do not change provider v1 or expose store handles.

After that contract decision, the smallest #20 implementation slice is a
rebuildable local spatial-current-state projection with a synthetic benchmark
fixture and restart/rebuild proof. Keep binary assets as references. It should
prove that one movement touches only the affected local/index/dependency state
before adding ANN/vector or external binary-storage infrastructure.
