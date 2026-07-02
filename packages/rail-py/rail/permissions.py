from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PUBLIC_VISIBILITIES = {"", "public", "project"}
RESTRICTED_VISIBILITIES = {"private", "team", "restricted"}
PATH_RESOURCE_KINDS = {"path", "topic", "source", "file"}
SUPPORTED_ACTIONS = {
    "read",
    "write",
    "execute",
    "dispatch_agent",
    "read_secret",
    "set_secret",
    "promote",
    "admin",
}


@dataclass(frozen=True)
class PermissionActor:
    """Local actor identity for policy checks.

    KRAIL stays public-by-default for backward compatibility. Actors only matter
    when records opt into restrictive metadata.
    """

    id: str = "local:user"
    type: str = "user"
    roles: tuple[str, ...] = field(default_factory=tuple)
    agent: str | None = None

    @classmethod
    def from_env(cls) -> "PermissionActor":
        actor_id = os.environ.get("KRAIL_ACTOR") or os.environ.get("USER") or "local:user"
        roles = tuple(item.strip() for item in os.environ.get("KRAIL_ROLES", "").split(",") if item.strip())
        agent = os.environ.get("KRAIL_AGENT") or None
        actor_type = "agent" if agent else "user"
        return cls(id=actor_id, type=actor_type, roles=roles, agent=agent)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "roles": list(self.roles), "agent": self.agent}


@dataclass(frozen=True)
class AuthorizationResource:
    kind: str
    target: str
    metadata: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def path(cls, target: str, metadata: dict[str, Any] | None = None, *, kind: str = "path", context: dict[str, Any] | None = None) -> "AuthorizationResource":
        return cls(kind=kind, target=target, metadata=dict(metadata or {}), context=dict(context or {}))

    @classmethod
    def workflow(cls, target: str, metadata: dict[str, Any] | None = None, *, context: dict[str, Any] | None = None) -> "AuthorizationResource":
        return cls(kind="workflow", target=target, metadata=dict(metadata or {}), context=dict(context or {}))

    @classmethod
    def tool(cls, target: str, metadata: dict[str, Any] | None = None, *, context: dict[str, Any] | None = None) -> "AuthorizationResource":
        return cls(kind="tool", target=target, metadata=dict(metadata or {}), context=dict(context or {}))

    @classmethod
    def secret(cls, target: str, metadata: dict[str, Any] | None = None, *, context: dict[str, Any] | None = None) -> "AuthorizationResource":
        return cls(kind="secret", target=target, metadata=dict(metadata or {}), context=dict(context or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "metadata": dict(self.metadata),
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class AuthorizationDecision:
    actor: PermissionActor
    action: str
    resource: AuthorizationResource
    allowed: bool
    reason: str
    audit_required: bool = False

    @property
    def decision(self) -> str:
        return "allowed" if self.allowed else "denied"

    def as_tuple(self) -> tuple[bool, str]:
        return self.allowed, self.reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor.to_dict(),
            "action": self.action,
            "resource": self.resource.to_dict(),
            "allowed": self.allowed,
            "decision": self.decision,
            "reason": self.reason,
            "audit_required": self.audit_required,
        }


class PermissionPolicy:
    def __init__(self, project_path: str | Path, actor: PermissionActor | None = None):
        self.project_path = Path(project_path)
        self.actor = actor or PermissionActor.from_env()

    def metadata_for_path(self, rel_path: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        global_rules = self._global_rules()
        for rule in global_rules:
            pattern = str(rule.get("path") or rule.get("pattern") or "")
            if pattern and fnmatch.fnmatch(rel_path, pattern):
                merged.update({key: value for key, value in rule.items() if key not in {"path", "pattern"}})
        if metadata:
            merged.update(metadata)
        return merged

    def authorize(
        self,
        action: str,
        resource: str | AuthorizationResource,
        metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
        resource_kind: str | None = None,
    ) -> AuthorizationDecision:
        normalized_action = str(action or "").strip().lower()
        normalized_resource = self._normalize_resource(
            normalized_action,
            resource,
            metadata=metadata,
            context=context,
            resource_kind=resource_kind,
        )
        deny_reason = self._deny_reason(normalized_action, normalized_resource)
        if deny_reason:
            return self._decision(normalized_action, normalized_resource, False, deny_reason)
        if normalized_action == "read":
            allowed, reason = self._authorize_read(normalized_resource)
        elif normalized_action in {"write", "promote"}:
            allowed, reason = self._authorize_write(normalized_resource)
        elif normalized_action in {"execute", "dispatch_agent"}:
            allowed, reason = self._authorize_execute(normalized_resource)
        elif normalized_action in {"read_secret", "set_secret"}:
            allowed, reason = self._authorize_secret(normalized_action, normalized_resource)
        elif normalized_action == "admin":
            allowed, reason = self._authorize_admin(normalized_resource)
        else:
            allowed, reason = False, f"unsupported_action:{normalized_action or 'unknown'}"
        return self._decision(normalized_action, normalized_resource, allowed, reason)

    def can_read(self, target: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        return self.authorize("read", target, metadata).as_tuple()

    def can_write(self, target: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        return self.authorize("write", target, metadata).as_tuple()

    def can_execute(self, workflow_id: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        return self.authorize("execute", AuthorizationResource.workflow(workflow_id, metadata)).as_tuple()

    def can_dispatch_agent(self, workflow_id: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        return self.authorize("dispatch_agent", AuthorizationResource.workflow(workflow_id, metadata)).as_tuple()

    def can_read_secret(self, secret_name: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        return self.authorize("read_secret", AuthorizationResource.secret(secret_name, metadata)).as_tuple()

    def can_set_secret(self, secret_name: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        return self.authorize("set_secret", AuthorizationResource.secret(secret_name, metadata)).as_tuple()

    def can_use_tool(self, tool_name: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
        return self.authorize("execute", AuthorizationResource.tool(tool_name, metadata)).as_tuple()

    def filter_readable(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        readable: list[dict[str, Any]] = []
        for result in results:
            target = str(result.get("path") or result.get("id") or result.get("title") or "record")
            metadata = self._metadata_from_record(result)
            decision = self.authorize("read", target, metadata)
            if decision.allowed:
                if decision.audit_required:
                    self.audit_decision(decision)
                readable.append(result)
            else:
                self.audit_decision(decision)
        return readable

    def doctor(self) -> dict[str, Any]:
        unlabeled_sensitive: list[str] = []
        restricted_records: list[str] = []
        for path in sorted((self.project_path / "topics").rglob("*.md")) if (self.project_path / "topics").exists() else []:
            rel = path.relative_to(self.project_path).as_posix()
            metadata = self.metadata_for_path(rel, _frontmatter(path))
            if metadata.get("sensitivity") and not metadata.get("visibility"):
                unlabeled_sensitive.append(rel)
            if str(metadata.get("visibility") or "public").lower() in RESTRICTED_VISIBILITIES:
                restricted_records.append(rel)
        return {
            "ok": not unlabeled_sensitive,
            "actor": self.actor.to_dict(),
            "public_by_default": True,
            "restricted_records": restricted_records,
            "unlabeled_sensitive": unlabeled_sensitive,
            "audit_log": "research_plan/audit/access.jsonl",
        }

    def audit(self, action: str, target: str, decision: str, reason: str, *, metadata: dict[str, Any] | None = None) -> None:
        audit_dir = self.project_path / "research_plan" / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "actor": self.actor.to_dict(),
            "action": action,
            "target": target,
            "decision": decision,
            "reason": reason,
        }
        if metadata and metadata.get("sensitivity"):
            record["sensitivity"] = _as_str_list(metadata.get("sensitivity"))
        with (audit_dir / "access.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def audit_decision(self, decision: AuthorizationDecision) -> None:
        self.audit(
            decision.action,
            decision.resource.target,
            decision.decision,
            decision.reason,
            metadata=decision.resource.metadata,
        )

    def _global_rules(self) -> list[dict[str, Any]]:
        manifest_path = self.project_path / "rail.yaml"
        if not manifest_path.exists():
            return []
        try:
            import yaml

            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return []
        permissions = manifest.get("permissions") if isinstance(manifest, dict) else None
        rules = permissions.get("rules") if isinstance(permissions, dict) else None
        return [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []

    def _normalize_resource(
        self,
        action: str,
        resource: str | AuthorizationResource,
        *,
        metadata: dict[str, Any] | None,
        context: dict[str, Any] | None,
        resource_kind: str | None,
    ) -> AuthorizationResource:
        if isinstance(resource, AuthorizationResource):
            kind = resource_kind or resource.kind or self._default_resource_kind(action)
            target = str(resource.target)
            merged_metadata = {**resource.metadata, **dict(metadata or {})}
            merged_context = {**resource.context, **dict(context or {})}
        else:
            kind = resource_kind or self._default_resource_kind(action)
            target = str(resource)
            merged_metadata = dict(metadata or {})
            merged_context = dict(context or {})
        if kind in PATH_RESOURCE_KINDS and target:
            merged_metadata = self.metadata_for_path(target, merged_metadata)
        return AuthorizationResource(kind=kind, target=target, metadata=merged_metadata, context=merged_context)

    @staticmethod
    def _default_resource_kind(action: str) -> str:
        if action in {"execute", "dispatch_agent"}:
            return "workflow"
        if action in {"read_secret", "set_secret"}:
            return "secret"
        return "path"

    def _decision(self, action: str, resource: AuthorizationResource, allowed: bool, reason: str) -> AuthorizationDecision:
        return AuthorizationDecision(
            actor=self.actor,
            action=action,
            resource=resource,
            allowed=allowed,
            reason=reason,
            audit_required=self._audit_required(action, allowed, resource.metadata),
        )

    def _authorize_read(self, resource: AuthorizationResource) -> tuple[bool, str]:
        read_paths = _as_str_list(resource.metadata.get("read") or resource.metadata.get("allowed_read_paths"))
        if read_paths and not any(fnmatch.fnmatch(resource.target, pattern) for pattern in read_paths):
            return False, "read_path_not_allowed"
        visibility = str(resource.metadata.get("visibility") or "public").lower()
        constrained = any(resource.metadata.get(key) for key in ("allowed_roles", "allowed_agents", "allowed_users", "allowed_actors"))
        if constrained:
            if self._actor_allowed(resource.metadata):
                return True, "actor_allowed"
            return False, "allowlist_not_matched"
        if visibility in PUBLIC_VISIBILITIES:
            return True, "public_default"
        if visibility not in RESTRICTED_VISIBILITIES:
            return True, "unknown_visibility_public_default"
        if self._actor_allowed(resource.metadata):
            return True, "actor_allowed"
        return False, f"visibility:{visibility}"

    def _authorize_write(self, resource: AuthorizationResource) -> tuple[bool, str]:
        write_paths = _as_str_list(resource.metadata.get("write") or resource.metadata.get("allowed_write_paths"))
        if write_paths and not any(fnmatch.fnmatch(resource.target, pattern) for pattern in write_paths):
            return False, "write_path_not_allowed"
        return self._authorize_read(resource)

    def _authorize_execute(self, resource: AuthorizationResource) -> tuple[bool, str]:
        if resource.kind == "tool":
            allow = _as_str_list(resource.metadata.get("allow") or resource.metadata.get("allowed_tools"))
            if allow and not any(fnmatch.fnmatch(resource.target, pattern) for pattern in allow):
                return False, "tool_not_allowed"
        else:
            allowed_agents = _as_str_list(resource.metadata.get("agents") or resource.metadata.get("allowed_agents"))
            if allowed_agents and self.actor.agent not in allowed_agents:
                return False, "agent_not_allowed"
            allowed_workflows = _as_str_list(resource.metadata.get("allow") or resource.metadata.get("allowed_workflows"))
            if allowed_workflows and not any(fnmatch.fnmatch(resource.target, pattern) for pattern in allowed_workflows):
                return False, "workflow_not_allowed"
        return self._authorize_read(resource)

    def _authorize_secret(self, action: str, resource: AuthorizationResource) -> tuple[bool, str]:
        allow = _as_str_list(resource.metadata.get("allow") or resource.metadata.get("allowed_secrets") or resource.metadata.get("secrets"))
        if allow and not any(fnmatch.fnmatch(resource.target, pattern) for pattern in allow):
            return False, "secret_not_allowed"
        if action == "set_secret":
            return self._authorize_write(resource)
        return self._authorize_read(resource)

    def _authorize_admin(self, resource: AuthorizationResource) -> tuple[bool, str]:
        if self._actor_allowed(resource.metadata):
            return True, "actor_allowed"
        return self._authorize_write(resource)

    def _deny_reason(self, action: str, resource: AuthorizationResource) -> str | None:
        if action not in SUPPORTED_ACTIONS:
            return None
        metadata = resource.metadata
        denied_roles = _as_str_list(metadata.get("denied_roles"))
        if denied_roles and set(self.actor.roles).intersection(denied_roles):
            return "role_denied"
        denied_agents = _as_str_list(metadata.get("denied_agents"))
        if denied_agents and self.actor.agent in denied_agents:
            return "agent_denied"
        denied_users = _as_str_list(metadata.get("denied_users") or metadata.get("denied_actors"))
        if denied_users and self.actor.id in denied_users:
            return "actor_denied"
        deny_actions = _as_str_list(metadata.get("deny_actions"))
        if deny_actions and (action in deny_actions or "*" in deny_actions):
            return f"action_denied:{action}"
        deny_targets = self._deny_targets_for(action, resource)
        if deny_targets and any(fnmatch.fnmatch(resource.target, pattern) for pattern in deny_targets):
            return "explicit_deny"
        return None

    @staticmethod
    def _audit_required(action: str, allowed: bool, metadata: dict[str, Any]) -> bool:
        return bool(metadata.get("sensitivity")) or not allowed or action in {"read_secret", "set_secret"}

    @staticmethod
    def _deny_targets_for(action: str, resource: AuthorizationResource) -> list[str]:
        metadata = resource.metadata
        if resource.kind == "tool":
            return _as_str_list(metadata.get("deny") or metadata.get("denied_tools"))
        if resource.kind == "secret":
            return _as_str_list(metadata.get("deny") or metadata.get("denied_secrets"))
        if action in {"write", "promote", "admin"}:
            return _as_str_list(metadata.get("denied_write_paths") or metadata.get("deny"))
        if action in {"execute", "dispatch_agent"}:
            return _as_str_list(metadata.get("deny") or metadata.get("denied_workflows"))
        return _as_str_list(metadata.get("denied_read_paths") or metadata.get("deny"))

    def _actor_allowed(self, metadata: dict[str, Any]) -> bool:
        owners = _as_str_list(metadata.get("owner") or metadata.get("owners"))
        if owners and self.actor.id in owners:
            return True
        allowed_agents = _as_str_list(metadata.get("allowed_agents"))
        if allowed_agents and self.actor.agent in allowed_agents:
            return True
        allowed_roles = _as_str_list(metadata.get("allowed_roles"))
        if allowed_roles and set(self.actor.roles).intersection(allowed_roles):
            return True
        allowed_users = _as_str_list(metadata.get("allowed_users") or metadata.get("allowed_actors"))
        if allowed_users and self.actor.id in allowed_users:
            return True
        return False

    @staticmethod
    def _metadata_from_record(record: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        source = record.get("record") if isinstance(record.get("record"), dict) else record
        for key in (
            "visibility",
            "owner",
            "owners",
            "allowed_roles",
            "allowed_agents",
            "allowed_users",
            "allowed_actors",
            "denied_roles",
            "denied_agents",
            "denied_users",
            "denied_actors",
            "deny_actions",
            "read",
            "write",
            "allow",
            "deny",
            "allowed_read_paths",
            "allowed_write_paths",
            "allowed_workflows",
            "allowed_tools",
            "allowed_secrets",
            "denied_read_paths",
            "denied_write_paths",
            "denied_workflows",
            "denied_tools",
            "denied_secrets",
            "sensitivity",
        ):
            if isinstance(source, dict) and key in source:
                metadata[key] = source[key]
            elif key in record:
                metadata[key] = record[key]
        return metadata


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    try:
        import yaml

        loaded = yaml.safe_load(text[4:end]) or {}
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}
