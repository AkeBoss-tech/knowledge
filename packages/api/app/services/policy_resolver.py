from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field

class RunnerPolicy(BaseModel):
    default: str = "codex_cli"
    approval_required: bool = True
    max_retries: Optional[int] = None
    timeout_minutes: Optional[int] = None
    bash_access: bool = True

class PathPolicy(BaseModel):
    read: List[str] = Field(default_factory=list)
    write: List[str] = Field(default_factory=list)
    deny: List[str] = Field(default_factory=list)

class SecretPolicy(BaseModel):
    allow: List[str] = Field(default_factory=list)

class ToolPolicy(BaseModel):
    allow: List[str] = Field(default_factory=list)
    deny: List[str] = Field(default_factory=list)

class CompletionPolicy(BaseModel):
    requires: List[str] = Field(default_factory=list)

class SkillPolicy(BaseModel):
    allow_use: bool = False

class RuntimePolicy(BaseModel):
    runner: RunnerPolicy
    paths: PathPolicy
    secrets: SecretPolicy
    tools: ToolPolicy
    completion: CompletionPolicy
    skills: SkillPolicy


class RunnerScope(BaseModel):
    allowed_paths: List[str] = Field(default_factory=list)
    denied_paths: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    denied_tools: List[str] = Field(default_factory=list)
    allowed_secrets: List[str] = Field(default_factory=list)


def _normalize_scope_items(values: list[str] | None, *, strip_slashes: bool) -> list[str]:
    normalized: list[str] = []
    for raw in values or []:
        item = str(raw or "").strip().replace("\\", "/")
        if strip_slashes:
            item = item.strip("/")
        if not item or item == "." or item in normalized:
            continue
        normalized.append(item)
    return normalized


def _path_within(scope_path: str, parent_scope: str) -> bool:
    return scope_path == parent_scope or scope_path.startswith(f"{parent_scope}/")

def resolve_runner_policy(runner: dict[str, Any] | None) -> RunnerPolicy:
    if not runner:
        return RunnerPolicy()
    return RunnerPolicy(
        default=runner.get("default") or "codex_cli",
        approval_required=runner.get("approval_required", True),
        max_retries=runner.get("max_retries"),
        timeout_minutes=runner.get("timeout_minutes"),
        bash_access=runner.get("bash_access", True),
    )

def resolve_path_policy(permissions: dict[str, Any] | None) -> PathPolicy:
    if not permissions:
        return PathPolicy()
    return PathPolicy(
        read=permissions.get("read") or [],
        write=permissions.get("write") or [],
        deny=permissions.get("deny") or []
    )

def resolve_secret_policy(secrets: dict[str, Any] | None) -> SecretPolicy:
    if not secrets:
        return SecretPolicy()
    return SecretPolicy(
        allow=secrets.get("allow") or []
    )

def resolve_tool_policy(tools: dict[str, Any] | None) -> ToolPolicy:
    if not tools:
        return ToolPolicy()
    return ToolPolicy(
        allow=tools.get("allow") or [],
        deny=tools.get("deny") or []
    )

def resolve_completion_policy(completion: dict[str, Any] | None) -> CompletionPolicy:
    if not completion:
        return CompletionPolicy()
    return CompletionPolicy(
        requires=completion.get("requires") or []
    )

def resolve_skill_policy(skills: dict[str, Any] | None, role: str | None = None) -> SkillPolicy:
    if not skills:
        return SkillPolicy(allow_use=(role == "planner"))
    return SkillPolicy(
        allow_use=skills.get("allow_use", role == "planner")
    )

def resolve_role_policy(agent_config: dict[str, Any]) -> RuntimePolicy:
    return RuntimePolicy(
        runner=resolve_runner_policy(agent_config.get("runner")),
        paths=resolve_path_policy(agent_config.get("permissions")),
        secrets=resolve_secret_policy(agent_config.get("secrets")),
        tools=resolve_tool_policy(agent_config.get("tools")),
        completion=resolve_completion_policy(agent_config.get("completion")),
        skills=resolve_skill_policy(agent_config.get("skills"), agent_config.get("role")),
    )


def resolve_runner_scope(
    policy: RuntimePolicy,
    *,
    requested_write_paths: list[str] | None = None,
    requested_tools: list[str] | None = None,
) -> RunnerScope:
    policy_write_paths = _normalize_scope_items(policy.paths.write, strip_slashes=True)
    denied_paths = _normalize_scope_items(policy.paths.deny, strip_slashes=True)
    requested_paths = _normalize_scope_items(requested_write_paths, strip_slashes=True)
    for path in requested_paths:
        if any(_path_within(path, denied) for denied in denied_paths):
            raise PermissionError(f"Requested runner write path is denied by role policy: {path}")
        if policy_write_paths and not any(_path_within(path, allowed) for allowed in policy_write_paths):
            raise PermissionError(f"Requested runner write path is outside role policy: {path}")
    allowed_paths = requested_paths or policy_write_paths

    policy_allowed_tools = _normalize_scope_items(policy.tools.allow, strip_slashes=False)
    denied_tools = _normalize_scope_items(policy.tools.deny, strip_slashes=False)
    requested_tool_names = _normalize_scope_items(requested_tools, strip_slashes=False)
    for tool_name in requested_tool_names:
        if tool_name in denied_tools:
            raise PermissionError(f"Requested runner tool is denied by role policy: {tool_name}")
        if policy_allowed_tools and tool_name not in policy_allowed_tools:
            raise PermissionError(f"Requested runner tool is outside role policy: {tool_name}")
    allowed_tools = requested_tool_names or policy_allowed_tools

    return RunnerScope(
        allowed_paths=allowed_paths,
        denied_paths=denied_paths,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        allowed_secrets=_normalize_scope_items(policy.secrets.allow, strip_slashes=False),
    )
