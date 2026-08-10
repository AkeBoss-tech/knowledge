"""Digest-addressed discovery of packaged hosted migrations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True)
class Migration:
    version: str
    up_sql: str
    down_sql: str
    up_digest: str
    down_digest: str


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def migrations() -> tuple[Migration, ...]:
    root = files("rail.hosted.migrations")
    result: list[Migration] = []
    for resource in sorted(root.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".sql") or resource.name.endswith(".down.sql"):
            continue
        version = resource.name.removesuffix(".sql")
        rollback = root.joinpath(f"{version}.down.sql")
        if not rollback.is_file():
            raise RuntimeError(f"migration {version} has no rollback resource")
        up_sql, down_sql = resource.read_text(encoding="utf-8"), rollback.read_text(encoding="utf-8")
        result.append(Migration(version, up_sql, down_sql, _digest(up_sql), _digest(down_sql)))
    if not result:
        raise RuntimeError("no hosted migrations were packaged")
    return tuple(result)
