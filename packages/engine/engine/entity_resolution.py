"""Persistent source-key to canonical-ontology-URI mappings."""
from __future__ import annotations

import sqlite3
from pathlib import Path


class EntityResolutionCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS entity_resolution (scope TEXT NOT NULL, source_key TEXT NOT NULL, uri TEXT NOT NULL, PRIMARY KEY (scope, source_key))"
        )

    def get(self, scope: str, source_key: str) -> str | None:
        row = self.connection.execute("SELECT uri FROM entity_resolution WHERE scope = ? AND source_key = ?", (scope, source_key)).fetchone()
        return str(row[0]) if row else None

    def put(self, scope: str, source_key: str, uri: str) -> None:
        self.connection.execute(
            "INSERT INTO entity_resolution (scope, source_key, uri) VALUES (?, ?, ?) ON CONFLICT(scope, source_key) DO UPDATE SET uri = excluded.uri",
            (scope, source_key, uri),
        )

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()
