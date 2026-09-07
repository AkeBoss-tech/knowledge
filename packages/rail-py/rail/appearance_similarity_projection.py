"""Disposable exact cosine search over persisted appearance descriptor records."""
from __future__ import annotations
from math import hypot
from rail.temporal_records import TemporalRecord


class AppearanceSimilarityProjection:
    def __init__(self, records: tuple[TemporalRecord, ...], *, valid_at, known_at) -> None:
        self.valid_at, self.known_at = valid_at, known_at
        self.rows = tuple(record for record in records if record.payload_schema == "robotics.appearance-observation" and record.valid_from <= valid_at and (record.ingested_at or record.recorded_at) <= known_at and record.payload.get("descriptor") is not None)

    def query(self, *, descriptor: tuple[float, ...], model: str, version: str, world_id: str) -> tuple[tuple[TemporalRecord, float], ...]:
        if not 1 <= len(descriptor) <= 512:
            raise ValueError("appearance query descriptor must be 1..512 values")
        def unit(values):
            scale = max(abs(value) for value in values)
            if not scale:
                return None
            scaled = tuple(value / scale for value in values)
            norm = hypot(*scaled)
            return tuple(value / norm for value in scaled)
        query_unit = unit(descriptor)
        if query_unit is None:
            raise ValueError("appearance query descriptor must not be zero")
        matches = []
        for record in self.rows:
            payload = record.payload
            vector = tuple(float(value) for value in payload["descriptor"])
            if payload["descriptor_model"] != model or payload["descriptor_version"] != version or payload["world_id"] != world_id or len(vector) != len(descriptor):
                continue
            other_unit = unit(vector)
            if other_unit is None:
                continue
            matches.append((record, sum(a*b for a,b in zip(query_unit, other_unit, strict=True))))
        return tuple(sorted(matches, key=lambda item: (-item[1], item[0].record_digest)))
