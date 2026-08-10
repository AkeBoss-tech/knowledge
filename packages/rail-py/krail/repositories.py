"""Repository ports used by KRAIL application services.

The ports live beside the domain contracts so local and hosted deployments can
share one semantic model.  They deliberately do not select a runtime authority;
deployment composition must choose exactly one repository implementation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from krail.epistemic_history import (
    EpistemicRecord,
    OperationContext,
    RecordKind,
)


@runtime_checkable
class EpistemicHistoryRepository(Protocol):
    """Semantic repository behavior consumed by ``ContextBriefService``."""

    def list(self) -> list[EpistemicRecord]: ...

    def append(
        self,
        *,
        kind: RecordKind,
        subject_digest: str,
        details: dict[str, Any],
        created_at: datetime,
        retention_until: datetime | None = None,
        operation_context: OperationContext | None = None,
        supersedes: str | None = None,
        corrects: str | None = None,
    ) -> EpistemicRecord: ...

    def supersede(
        self,
        record_id: str,
        *,
        details: dict[str, Any],
        created_at: datetime,
        retention_until: datetime | None = None,
        operation_context: OperationContext | None = None,
    ) -> EpistemicRecord: ...

    def correct(
        self,
        record_id: str,
        *,
        details: dict[str, Any],
        created_at: datetime,
        retention_until: datetime | None = None,
        operation_context: OperationContext | None = None,
    ) -> EpistemicRecord: ...

    def erase(self, record_id: str, *, erased_at: datetime, reason: str) -> EpistemicRecord: ...

    def enforce_retention(self, *, as_of: datetime) -> list[EpistemicRecord]: ...


class EpistemicUnitOfWork(Protocol):
    """Explicit transaction boundary for hosted epistemic writes."""

    history: EpistemicHistoryRepository

    def __enter__(self) -> "EpistemicUnitOfWork": ...

    def __exit__(self, exc_type: object, exc: BaseException | None, traceback: object) -> bool | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


__all__ = ["EpistemicHistoryRepository", "EpistemicUnitOfWork"]
