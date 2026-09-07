"""Disposable exact cosine search over persisted appearance descriptor records."""
from __future__ import annotations
from math import hypot
from rail.temporal_records import TemporalRecord


class AppearanceSimilarityProjection:
    def __init__(self, records: tuple[TemporalRecord, ...], *, valid_at, known_at) -> None:
        self.valid_at, self.known_at = valid_at, known_at
        self.rows = tuple(record for record in records if record.payload_schema == "robotics.appearance-observation" and record.valid_from <= valid_at and (record.ingested_at or record.recorded_at) <= known_at and record.payload.get("descriptor") is not None)

    def query(self, *, descriptor: tuple[float, ...], model: str, version: str, limit: int) -> tuple[tuple[TemporalRecord, float], ...]:
        norm = hypot(*descriptor)
        if not norm:
            raise ValueError("appearance query descriptor must not be zero")
        matches = []
        for record in self.rows:
            payload = record.payload
            vector = tuple(float(value) for value in payload["descriptor"])
            if payload["descriptor_model"] != model or payload["descriptor_version"] != version or len(vector) != len(descriptor):
                continue
            other = hypot(*vector)
            if not other:
                continue
            matches.append((record, sum(a*b for a,b in zip(descriptor, vector, strict=True)) / (norm * other)))
        return tuple(sorted(matches, key=lambda item: (-item[1], item[0].record_digest))[:limit])
