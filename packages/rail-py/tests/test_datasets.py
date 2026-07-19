from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import yaml

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime


def test_dataset_catalog_validates_and_snapshots_csv(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Data Project", slug="data-project")
    (root / "sources" / "observations.csv").write_text("id,name\n1,Alpha\n", encoding="utf-8")
    (root / "sources" / "datasets.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "datasets": [
                    {
                        "id": "observations",
                        "format": "csv",
                        "path": "sources/observations.csv",
                        "primary_key": "id",
                        "refresh": "append_only",
                        "projection": {"class": "Observation", "uri": "observation-{id}"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    runtime = KnowledgeRuntime(root)

    assert runtime.datasets_validate()["ok"] is True
    snapshot = runtime.datasets_snapshot()

    assert snapshot["errors"] == []
    record = snapshot["datasets"][0]
    assert record["schema"] == [{"name": "id", "type": "unknown"}, {"name": "name", "type": "unknown"}]
    assert len(record["content_hash"]) == 64
    state = json.loads((root / "research_plan/state/dataset_snapshots.json").read_text(encoding="utf-8"))
    assert state["datasets"]["observations"]["schema_hash"] == record["schema_hash"]


def test_dataset_catalog_requires_sqlite_query_or_table(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Data Project", slug="data-project")
    db_path = root / "sources" / "facts.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, label TEXT)")
    (root / "sources" / "datasets.yaml").write_text(
        yaml.safe_dump({"datasets": [{"id": "facts", "format": "sqlite", "path": "sources/facts.sqlite"}]}),
        encoding="utf-8",
    )

    result = KnowledgeRuntime(root).datasets_validate()

    assert result["ok"] is False
    assert "requires table or query" in "\n".join(result["errors"])


def test_dataset_cache_builds_indexes_and_detects_staleness(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Data Project", slug="data-project")
    source = root / "sources" / "events.csv"
    source.write_text("event_id,category\n1,signup\n2,purchase\n", encoding="utf-8")
    (root / "sources" / "datasets.yaml").write_text(
        yaml.safe_dump(
            {
                "datasets": [
                    {
                        "id": "events",
                        "format": "csv",
                        "path": "sources/events.csv",
                        "primary_key": "event_id",
                        "indexes": ["category"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime = KnowledgeRuntime(root)

    built = runtime.datasets_cache_build()
    fresh = runtime.datasets_cache_status()
    validated = runtime.datasets_cache_validate()
    benchmark = runtime.datasets_cache_benchmark("events", iterations=2)
    source.write_text("event_id,category\n1,signup\n2,purchase\n3,signup\n", encoding="utf-8")
    stale = runtime.datasets_cache_status()
    stale_validation = runtime.datasets_cache_validate()

    assert built["status"] == "built"
    assert built["datasets"][0]["id"] == "events"
    assert built["datasets"][0]["table"] == "data_events"
    assert built["datasets"][0]["row_count"] == 2
    assert built["datasets"][0]["indexes"] == ["event_id", "category"]
    assert fresh["datasets"][0]["fresh"] is True
    assert validated["ok"] is True
    assert benchmark["status"] == "benchmarked"
    assert benchmark["row_count"] == 2
    assert stale["datasets"][0]["fresh"] is False
    assert stale_validation["ok"] is False
    assert "stale cache table" in "\n".join(stale_validation["errors"])


def test_dataset_cache_streams_sqlite_table(tmp_path: Path):
    root = bootstrap_future_project(tmp_path, name="Data Project", slug="data-project")
    db_path = root / "sources" / "facts.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, amount REAL)")
        connection.executemany("INSERT INTO facts VALUES (?, ?)", [(1, 1.5), (2, 2.5)])
    (root / "sources" / "datasets.yaml").write_text(
        yaml.safe_dump({"datasets": [{"id": "facts", "format": "sqlite", "path": "sources/facts.sqlite", "table": "facts", "primary_key": "id"}]}),
        encoding="utf-8",
    )

    built = KnowledgeRuntime(root).datasets_cache_build()

    assert built["status"] == "built"
    assert built["datasets"][0]["row_count"] == 2
