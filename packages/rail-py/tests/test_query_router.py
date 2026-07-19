from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

RAIL_PY_ROOT = Path(__file__).parents[1]
if str(RAIL_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(RAIL_PY_ROOT))

from rail.bootstrap import bootstrap_future_project
from rail.knowledge import KnowledgeRuntime
from rail.query_router import query_cache, query_sqlite_source, route_sql


def _project_with_sources(tmp_path: Path) -> Path:
    root = bootstrap_future_project(tmp_path, name="Query Project", slug="query-project")
    (root / "sources" / "events.csv").write_text("event_id,category\n1,signup\n2,purchase\n", encoding="utf-8")
    db_path = root / "sources" / "source.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE source_events (id INTEGER, label TEXT)")
        connection.execute("INSERT INTO source_events VALUES (1, 'source')")
    (root / "sources" / "datasets.yaml").write_text(
        yaml.safe_dump(
            {"datasets": [
                {"id": "events", "format": "csv", "path": "sources/events.csv", "primary_key": "event_id"},
                {"id": "source-events", "format": "sqlite", "path": "sources/source.sqlite", "table": "source_events", "primary_key": "id"},
            ]}
        ),
        encoding="utf-8",
    )
    KnowledgeRuntime(root).datasets_cache_build()
    return root


def test_cache_and_auto_routes_return_backend_and_lineage(tmp_path: Path):
    root = _project_with_sources(tmp_path)

    direct = query_cache(root, "SELECT category FROM data_events ORDER BY event_id")
    routed = route_sql(root, "SELECT count(*) AS total FROM data_events")

    assert direct["backend"] == "dataset_cache"
    assert direct["rows"] == [["signup"], ["purchase"]]
    assert direct["datasets"][0]["id"] == "events"
    assert routed["backend"] == "dataset_cache"
    assert routed["rows"] == [[2]]


def test_source_sqlite_query_is_bounded_and_read_only(tmp_path: Path):
    root = _project_with_sources(tmp_path)

    result = query_sqlite_source(root, "source-events", "SELECT label FROM source_events")

    assert result["backend"] == "source_sqlite"
    assert result["rows"] == [["source"]]
    with pytest.raises(ValueError, match="only SELECT"):
        query_sqlite_source(root, "source-events", "DELETE FROM source_events")
