#!/usr/bin/env python3
"""Opt-in deterministic #20 benchmark; it never fetches referenced assets."""
from __future__ import annotations

import argparse
import math
import json
import platform
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from krail.provider.v1 import ResourceRef
from rail.robotics_world_memory import Pose, TabletopWorldMemory, WorldObject
from rail.spatial_current_projection import SpatialCurrentProjection

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class Allow:
    def authorize(self, ref):
        return None


def ref(kind: str, name: str) -> ResourceRef:
    return ResourceRef(authority="fixture://benchmark", resource_type=kind, resource_id=name, version="1", digest="sha256:" + sha256(f"{kind}:{name}".encode()).hexdigest())


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; valid even for the opt-in two-repeat smoke."""
    return sorted(values)[min(len(values) - 1, max(0, math.ceil(p * len(values)) - 1))]


def elapsed_ms(fn, repeats: int) -> dict[str, float]:
    values = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        fn()
        values.append((time.perf_counter_ns() - start) / 1_000_000)
    return {"p50_ms": round(statistics.median(values), 4), "p95_ms": round(percentile(values, .95), 4)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objects", type=int, default=32)
    parser.add_argument("--observations", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--scenes", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.objects, args.observations, args.repeats, args.scenes) < 1:
        raise SystemExit("objects, observations, repeats, and scenes must be positive")

    with tempfile.TemporaryDirectory(prefix="krail-spatial-benchmark-") as directory:
        path = str(Path(directory) / "world.json")
        memory = TabletopWorldMemory(path, tenant_id="bench", project_id="spatial", clock=lambda: NOW)
        records = []
        latest = []
        assets = []
        for object_number in range(args.objects):
            obj = WorldObject(world_id="bench", object_id=f"object-{object_number}", class_label="fixture", asset_ref=ref("asset", f"object-{object_number}"))
            assets.append(obj.asset_ref)
            for observation in range(args.observations):
                at = NOW - timedelta(seconds=args.observations - observation - 1)
                record = memory.record(obj, Pose(frame_id="table", metres=(object_number / args.objects, .2, 0), quaternion_xyzw=(0, 0, 0, 1), observed_at=at, uncertainty_metres=.001, revision=f"{object_number}-{observation}", map_revision="map-1"), kind="observation", evidence=(ref("camera", f"{object_number}-{observation}"),), recorded_at=NOW)
                records.append(record)
            latest.append(records[-1])
            # One immutable, referenced-only appearance record per object.
            memory.record_appearance(obj, records[-1], asset_ref=obj.asset_ref, viewpoint="overhead", context="table", descriptor_model="fixture", descriptor_version="1", quality=.9, occluded=False, revision="appearance")
        region = memory.record_region(world_id="bench", session_id="bench-session", place_id="table", region_id="local", frame_id="table", map_revision="map-1", min_metres=(-.1, .1, -.1), max_metres=(.2, .3, .1), evidence_refs=(ref("calibration", "local"),), revision="1", valid_from=NOW, recorded_at=NOW)
        for scene_number in range(args.scenes):
            memory.scene(world_id="bench", scene_id=f"snapshot-{scene_number}", records=tuple(latest), reader=Allow(), session_id="bench-session", valid_at=NOW, recorded_at=NOW)

        rebuild_start = time.perf_counter_ns()
        rebuild_work = memory.prepare_spatial_snapshot(valid_at=NOW, known_at=NOW)
        rebuild_ms = (time.perf_counter_ns() - rebuild_start) / 1_000_000
        public = lambda: memory.objects_in_region(world_id="bench", region_ref=region.region_ref, at=NOW, known_at=NOW, reader=Allow())
        raw = lambda: memory._spatial_current.candidate_query(world_id="bench", frame_id="table", map_revision="map-1", minimum=(-.1, .1, -.1), maximum=(.2, .3, .1))
        public_answer = public()
        public_work = dict(memory.last_region_read_work)
        raw_answer = raw()
        def public_forced_refresh():
            # Same owner API and fixture; benchmark the pre-cursor behavior.
            memory._temporal_scope_cursor = None
            return public()
        forced_refresh_timing = elapsed_ms(public_forced_refresh, args.repeats)
        public_timing = elapsed_ms(public, args.repeats)
        observer = TabletopWorldMemory(path, tenant_id="bench", project_id="spatial", clock=lambda: NOW)
        observer.prepare_spatial_snapshot(valid_at=NOW, known_at=NOW)
        memory.record(WorldObject(world_id="bench", object_id="external", class_label="fixture", asset_ref=ref("asset", "external")), Pose(frame_id="table", metres=(.8, .2, 0), quaternion_xyzw=(0, 0, 0, 1), observed_at=NOW, uncertainty_metres=.001, revision="external", map_revision="map-1"), kind="observation", evidence=(ref("camera", "external"),), recorded_at=NOW)
        external_start = time.perf_counter_ns()
        observer.objects_in_region(world_id="bench", region_ref=region.region_ref, at=NOW, known_at=NOW, reader=Allow())
        external_update = {"ms": round((time.perf_counter_ns() - external_start) / 1_000_000, 4), **observer.last_refresh_work}
        raw_timing = elapsed_ms(raw, args.repeats)
        update_start = time.perf_counter_ns()
        move = memory.record(WorldObject(world_id="bench", object_id="object-0", class_label="fixture", asset_ref=assets[0]), Pose(frame_id="table", metres=(.9, .2, 0), quaternion_xyzw=(0, 0, 0, 1), observed_at=NOW, uncertainty_metres=.001, revision="move", map_revision="map-1"), kind="observation", evidence=(ref("camera", "move"),), recorded_at=NOW)
        update_ms = (time.perf_counter_ns() - update_start) / 1_000_000
        update_work = memory.last_spatial_update_work
        assert update_work is not None
        after_move_answer = public()

        # Delayed observations become explicitly unknown at a later snapshot;
        # report the observed stale/obsolete outcome rate, not a prediction.
        delayed_at = NOW + timedelta(seconds=1)
        memory.prepare_spatial_snapshot(valid_at=delayed_at, known_at=delayed_at)
        delayed = memory.objects_in_region(world_id="bench", region_ref=region.region_ref, at=delayed_at, known_at=delayed_at, reader=Allow())
        reopened = TabletopWorldMemory(path, tenant_id="bench", project_id="spatial", clock=lambda: NOW)
        reopen_work = reopened.prepare_spatial_snapshot(valid_at=NOW, known_at=NOW)
        restart_equal = reopened.objects_in_region(world_id="bench", region_ref=region.region_ref, at=NOW, known_at=NOW, reader=Allow()).object_refs == after_move_answer.object_refs
        metadata = [record.model_dump(mode="json") for record in (*memory._records, *memory._appearance_records)]
        scene_bytes = [len(json.dumps(scene.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()) for scene in memory._scenes]
        payload = {
            "benchmark_date": "2026-09-07", "command": " ".join(sys.argv),
            "runtime": {"python": sys.version.split()[0], "platform": platform.platform()},
            "fixture": {"objects": args.objects, "observations_per_object": args.observations, "appearance_records": len(memory._appearance_records), "scene_snapshots": len(memory._scenes), "external_asset_refs": len(assets), "embedded_asset_bytes": 0},
            "public_authorized_region_query": public_timing,
            "public_authorized_region_query_forced_refresh": forced_refresh_timing,
            "raw_grid_candidate_query": raw_timing,
            "rebuild": {"ms": round(rebuild_ms, 4), **rebuild_work.__dict__},
            "incremental_move": {"ms": round(update_ms, 4), **update_work.__dict__},
            "cross_process_update": external_update,
            "change_ledger": {"entries": len(memory._projection.store.list("bench", "spatial", kind="temporal_scope_change"))},
            "reads": {"public_hits": len(public_answer.object_refs), "raw_candidate_rows": raw_answer.candidate_rows_read, "raw_cells": raw_answer.cells_read, **public_work},
            "retained_metadata_bytes": {"total": len(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()), "per_observation": round(sum(len(json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()) for record in memory._records) / len(memory._records), 1), "per_object": round(len(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()) / args.objects, 1), "per_snapshot_serialized": round(statistics.mean(scene_bytes), 1)},
            "delayed_update": {"queries": 1, "expected_abstention": 1, "abstentions": int(delayed.status in {"stale", "unknown"}), "obsolete_incorrect": int(delayed.status not in {"stale", "unknown"}), "obsolete_rate": float(delayed.status not in {"stale", "unknown"}), "status": delayed.status},
            "restart": {"equivalent_authorized_refs": restart_equal, **reopen_work.__dict__},
            "unavailable": ["fine geometry fetch: no asset transport", "appearance ANN: no vector index", "ROS/MoveIt live control: out of scope"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
