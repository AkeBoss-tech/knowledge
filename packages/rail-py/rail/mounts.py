from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rail.manifest import boot_validate_project


@dataclass(frozen=True)
class MountedProjectStatus:
    id: str
    name: str
    path: str
    type: str
    access_mode: str
    visibility: str
    tags: list[str]
    search_weight: float
    ok: bool
    error: str | None = None
    slug: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "access_mode": self.access_mode,
            "visibility": self.visibility,
            "tags": list(self.tags),
            "search_weight": self.search_weight,
            "ok": self.ok,
            "error": self.error,
            "slug": self.slug,
        }


class MountRegistry:
    def __init__(self, project_root: str | Path, mounts: list[Any]):
        self.project_root = Path(project_root).resolve()
        self.mounts = list(mounts or [])

    def _resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.project_root / candidate).resolve()

    def status(self, mount_id: str | None = None) -> list[MountedProjectStatus]:
        statuses: list[MountedProjectStatus] = []
        for mount in self.mounts:
            if mount_id and mount.id != mount_id:
                continue
            resolved = self._resolve_path(mount.path)
            if mount.type != "krail_project":
                statuses.append(
                    MountedProjectStatus(
                        id=mount.id,
                        name=mount.name,
                        path=str(resolved),
                        type=mount.type,
                        access_mode=mount.access_mode,
                        visibility=mount.visibility,
                        tags=list(mount.tags),
                        search_weight=mount.search_weight,
                        ok=False,
                        error=f"unsupported_mount_type:{mount.type}",
                    )
                )
                continue
            if not resolved.exists():
                statuses.append(
                    MountedProjectStatus(
                        id=mount.id,
                        name=mount.name,
                        path=str(resolved),
                        type=mount.type,
                        access_mode=mount.access_mode,
                        visibility=mount.visibility,
                        tags=list(mount.tags),
                        search_weight=mount.search_weight,
                        ok=False,
                        error="path_not_found",
                    )
                )
                continue
            try:
                manifest = boot_validate_project(resolved)
            except Exception as exc:
                statuses.append(
                    MountedProjectStatus(
                        id=mount.id,
                        name=mount.name,
                        path=str(resolved),
                        type=mount.type,
                        access_mode=mount.access_mode,
                        visibility=mount.visibility,
                        tags=list(mount.tags),
                        search_weight=mount.search_weight,
                        ok=False,
                        error=str(exc),
                    )
                )
                continue
            statuses.append(
                MountedProjectStatus(
                    id=mount.id,
                    name=mount.name,
                    path=str(resolved),
                    type=mount.type,
                    access_mode=mount.access_mode,
                    visibility=mount.visibility,
                    tags=list(mount.tags),
                    search_weight=mount.search_weight,
                    ok=True,
                    slug=manifest.project.slug,
                )
            )
        return statuses

    def list_mounts(self) -> dict[str, Any]:
        mounts = [status.to_dict() for status in self.status()]
        return {
            "mounts": mounts,
            "summary": {
                "total": len(mounts),
                "healthy": sum(1 for mount in mounts if mount.get("ok")),
                "unhealthy": sum(1 for mount in mounts if not mount.get("ok")),
            },
        }

    def resolve_projects(self, mount_ids: list[str] | None = None) -> list[tuple[Any, Any]]:
        selected = set(mount_ids or [])
        resolved_projects: list[tuple[Any, Any]] = []
        statuses = {item.id: item for item in self.status()}
        for mount in self.mounts:
            if selected and mount.id not in selected:
                continue
            status = statuses.get(mount.id)
            if not status or not status.ok:
                continue
            import rail

            resolved_projects.append((mount, rail.local(status.path)))
        return resolved_projects
