"""
KRAIL MCP Server — exposes the local knowledge runtime as MCP tools for AI agents.

Configuration via environment variables:
  RAIL_PROJECT   Project slug (required unless --local)
  RAIL_API_URL   API base URL (default: http://localhost:8000/api/v1)
  RAIL_API_KEY   Bearer token (optional for local deployments)
  RAIL_LOCAL     Set to "1" to load the project from the current directory
  RAIL_PATH      Local project path (default: ".", only used when RAIL_LOCAL=1)
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

import rail
from rail.permissions import PermissionPolicy

mcp = FastMCP("KRAIL")

# ---------------------------------------------------------------------------
# Lazy project singleton — resolved on first tool call
# ---------------------------------------------------------------------------

_project: rail.Project | None = None


def _get_project() -> rail.Project:
    global _project
    if _project is not None:
        return _project

    if os.environ.get("RAIL_LOCAL", "") == "1":
        path = os.environ.get("RAIL_PATH", ".")
        _project = rail.local(path=path)
    else:
        slug = os.environ.get("RAIL_PROJECT")
        if not slug:
            raise RuntimeError(
                "RAIL_PROJECT env var is required. "
                "Set it to your project slug or set RAIL_LOCAL=1 to load from disk."
            )
        _project = rail.connect(
            slug=slug,
            api_url=os.environ.get("RAIL_API_URL"),
            api_key=os.environ.get("RAIL_API_KEY"),
        )

    return _project


def _json(data: Any) -> str:
    if hasattr(data, "to_dict"):
        return json.dumps(data.to_dict(orient="records"), indent=2)
    return json.dumps(data, indent=2, default=str)


_WRITE_TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "capture": ("write_repo",),
    "topic_upsert": ("write_repo",),
    "inbox_promote": ("write_repo",),
    "graph_build": ("write_repo",),
    "vector_build": ("write_repo",),
    "sources_check": ("write_repo",),
    "ci_init": ("write_repo",),
    "scaffold_krail_agents": ("write_repo",),
    "create_task": ("create_task", "write_repo"),
    "init_workflow": ("write_repo",),
    "dispatch_task": ("dispatch_agent",),
    "run_workflow": ("dispatch_agent",),
    "execute_workflow": ("dispatch_agent",),
    "listener_init": ("write_repo",),
    "listener_poll": ("dispatch_agent",),
    "event_replay": ("dispatch_agent",),
}
_SECRET_TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "list_secrets": ("manage_secrets",),
    "set_secret": ("manage_secrets",),
}


def _normalize_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _load_scope_list(env_name: str) -> list[str]:
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            items = loaded
        else:
            items = [loaded]
    except Exception:
        items = [item.strip() for item in raw.split(",")]
    normalized: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        normalized.append(value)
    return normalized


def _runner_scope() -> dict[str, list[str]]:
    scope = {
        "allowed_paths": [_normalize_path(item) for item in _load_scope_list("KRAIL_ALLOWED_WRITE_PATHS") if _normalize_path(item)],
        "denied_paths": [_normalize_path(item) for item in _load_scope_list("KRAIL_DENIED_PATHS") if _normalize_path(item)],
        "allowed_tools": _load_scope_list("KRAIL_ALLOWED_TOOLS"),
        "denied_tools": _load_scope_list("KRAIL_DENIED_TOOLS"),
        "allowed_secrets": _load_scope_list("KRAIL_ALLOWED_SECRETS"),
    }
    if scope["allowed_paths"]:
        return scope

    work_order_path = (os.environ.get("RAIL_WORK_ORDER_PATH") or "").strip()
    project_path = _local_project_path()
    if not work_order_path or project_path is None:
        return scope
    work_order_file = Path(work_order_path)
    if not work_order_file.is_absolute():
        work_order_file = project_path / work_order_file
    try:
        payload = json.loads(work_order_file.read_text(encoding="utf-8"))
    except Exception:
        return scope
    if isinstance(payload, dict):
        scope["allowed_paths"] = [
            _normalize_path(item)
            for item in (payload.get("allowed_paths") or [])
            if _normalize_path(str(item or ""))
        ]
    return scope


def _scope_is_explicit(scope: dict[str, list[str]]) -> bool:
    return any(scope.values())


def _path_matches(path: str, scope_path: str) -> bool:
    return path == scope_path or path.startswith(f"{scope_path}/")


def _tool_aliases(tool_name: str, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    aliases = [tool_name, *extra]
    deduped: list[str] = []
    for item in aliases:
        if item and item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def _authorization_failure(tool_name: str, action: str, reason: str, *, target: str = "") -> str:
    payload = {
        "ok": False,
        "status": "denied",
        "error": {
            "code": "permission_denied",
            "tool": tool_name,
            "action": action,
            "reason": reason,
        },
    }
    if target:
        payload["error"]["target"] = target
    return _json(payload)


def _local_project_path() -> Path | None:
    if os.environ.get("RAIL_LOCAL", "") == "1":
        return Path(os.environ.get("RAIL_PATH", ".")).resolve()
    return None


def _permission_policy() -> PermissionPolicy | None:
    project_path = _local_project_path()
    if project_path is None:
        return None
    return PermissionPolicy(project_path)


def _authorize_tool_scope(tool_name: str, aliases: tuple[str, ...]) -> str | None:
    scope = _runner_scope()
    if not scope["allowed_tools"] and not scope["denied_tools"]:
        return None
    if any(alias in scope["denied_tools"] for alias in aliases):
        return _authorization_failure(tool_name, "use_tool", "tool_denied_by_runner_scope")
    if scope["allowed_tools"] and not any(alias in scope["allowed_tools"] for alias in aliases):
        return _authorization_failure(tool_name, "use_tool", "tool_not_allowed_by_runner_scope")
    return None


def _authorize_write(tool_name: str, target_path: str) -> str | None:
    target = _normalize_path(target_path)
    aliases = _tool_aliases(tool_name, _WRITE_TOOL_ALIASES.get(tool_name, ()))
    denied = _authorize_tool_scope(tool_name, aliases)
    if denied:
        return denied

    scope = _runner_scope()
    if scope["denied_paths"] and any(_path_matches(target, item) for item in scope["denied_paths"]):
        return _authorization_failure(tool_name, "write", "path_denied_by_runner_scope", target=target)
    if scope["allowed_paths"]:
        if not any(_path_matches(target, item) for item in scope["allowed_paths"]):
            return _authorization_failure(tool_name, "write", "path_not_allowed_by_runner_scope", target=target)
        return None

    policy = _permission_policy()
    if policy is None:
        return _authorization_failure(tool_name, "write", "repo_policy_unavailable", target=target)
    metadata = policy.metadata_for_path(target)
    allowed, reason = policy.can_write(target, metadata)
    if not allowed:
        policy.audit("write", target, "denied", reason, metadata=metadata)
        return _authorization_failure(tool_name, "write", reason, target=target)
    if policy.requires_audit("write", True, metadata):
        policy.audit("write", target, "allowed", reason, metadata=metadata)
    return None


def _authorize_execute(
    tool_name: str,
    target: str,
    *,
    private_by_default: bool = True,
    require_explicit_tool_scope: bool = False,
) -> str | None:
    aliases = _tool_aliases(tool_name)
    denied = _authorize_tool_scope(tool_name, aliases)
    if denied:
        return denied

    scope = _runner_scope()
    if require_explicit_tool_scope:
        if not scope["allowed_tools"] or not any(alias in scope["allowed_tools"] for alias in aliases):
            return _authorization_failure(tool_name, "execute", "tool_not_allowed_by_runner_scope", target=target)
        return None

    if _scope_is_explicit(scope):
        return None

    policy = _permission_policy()
    if policy is None:
        return _authorization_failure(tool_name, "execute", "repo_policy_unavailable", target=target)
    metadata = {"visibility": "private"} if private_by_default else {}
    resolved_metadata = policy.metadata_for_path(target, metadata)
    allowed, reason = policy.can_execute(target, resolved_metadata)
    if not allowed:
        policy.audit("execute", target, "denied", reason, metadata=resolved_metadata)
        return _authorization_failure(tool_name, "execute", reason, target=target)
    if policy.requires_audit("execute", True, resolved_metadata):
        policy.audit("execute", target, "allowed", reason, metadata=resolved_metadata)
    return None


def _authorize_secret(tool_name: str, *, key: str = "") -> str | None:
    aliases = _tool_aliases(tool_name, _SECRET_TOOL_ALIASES.get(tool_name, ()))
    denied = _authorize_tool_scope(tool_name, aliases)
    if denied:
        return denied

    scope = _runner_scope()
    allowed_secret_names = scope["allowed_secrets"]
    if allowed_secret_names:
        if key and "*" not in allowed_secret_names and key not in allowed_secret_names:
            return _authorization_failure(tool_name, "read_secret" if tool_name == "list_secrets" else "set_secret", "secret_not_allowed_by_runner_scope", target=key)
        return None

    policy = _permission_policy()
    target = f".krail/secrets/{key or '*'}"
    if policy is None:
        return _authorization_failure(tool_name, "read_secret" if tool_name == "list_secrets" else "set_secret", "secret_scope_required", target=target)
    metadata = policy.metadata_for_path(target, {"visibility": "private"})
    action_name = "read_secret" if tool_name == "list_secrets" else "set_secret"
    check = policy.can_read if tool_name == "list_secrets" else policy.can_write
    allowed, reason = check(target, metadata)
    if not allowed:
        policy.audit(action_name, target, "denied", reason, metadata=metadata)
        return _authorization_failure(tool_name, action_name, reason, target=target)
    if policy.requires_audit(action_name, True, metadata):
        policy.audit(action_name, target, "allowed", reason, metadata=metadata)
    return None


def _topic_target_path(topic: str) -> str:
    slug = []
    previous_dash = False
    for char in str(topic or "").strip().lower():
        if char.isalnum():
            slug.append(char)
            previous_dash = False
            continue
        if not previous_dash:
            slug.append("-")
            previous_dash = True
    normalized = "".join(slug).strip("-") or "topic"
    return f"topics/{normalized}.md"


# ---------------------------------------------------------------------------
# Ontology / knowledge graph tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_classes() -> str:
    """List all ontology classes defined in the project (e.g. County, Indicator, Policy)."""
    return _json(_get_project().classes())


@mcp.tool()
def get_entities(class_name: str, limit: int = 20) -> str:
    """
    Return up to `limit` instances of an ontology class.

    Args:
        class_name: Exact class name from list_classes (e.g. "County").
        limit: Max rows to return (default 20, max 500).
    """
    limit = min(limit, 500)
    df = _get_project().entities(class_name, limit=limit)
    return _json(df)


@mcp.tool()
def search_entities(query: str) -> str:
    """
    Full-text search across all ontology entities.

    Args:
        query: Search term (e.g. "Middlesex County unemployment").
    """
    return _json(_get_project().search(query))


@mcp.tool()
def search(query: str, limit: int = 10, explain: bool = False, federated: bool = False, mounts_json: str = "") -> str:
    """
    Search local project evidence. Returns ranked records, not synthesis.

    Args:
        query: Search query.
        limit: Maximum number of hits.
        explain: Include ranking-signal notes when available.
        federated: Include configured mounted child projects.
        mounts_json: Optional JSON list of mount ids to consult, e.g. ["robotics"].
    """
    project = _get_project()
    mounts: list[str] | None = None
    if mounts_json.strip():
        try:
            loaded = json.loads(mounts_json)
            if isinstance(loaded, list):
                mounts = [str(item) for item in loaded]
        except Exception:
            mounts = [item.strip() for item in mounts_json.split(",") if item.strip()]
    if federated:
        return _json(project.federated_search(query, limit=limit, mounts=mounts, explain=explain))
    if hasattr(project._backend, "knowledge"):
        return _json(project._backend.knowledge.search(query, limit=limit, explain=explain))
    return _json({"query": query, "hits": project.search(query)[:limit]})


@mcp.tool()
def find(
    query: str,
    limit: int = 10,
    types_json: str = "",
    topic: str = "",
    entity: str = "",
    status: str = "",
    freshness: str = "",
    workflow: str = "",
    explain: bool = False,
    rag: bool = True,
    federated: bool = False,
    mounts_json: str = "",
) -> str:
    """
    Find typed knowledge records across docs, graph, integrity state, sessions, queues, and artifacts.

    Args:
        query: Search query.
        limit: Maximum number of results.
        types_json: Optional JSON list of result types, e.g. ["claim", "workflow_run"].
        topic: Optional topic filter.
        entity: Optional entity filter.
        status: Optional status filter.
        freshness: Optional freshness filter.
        workflow: Optional workflow filter.
        explain: Include searched surfaces and ranking notes.
        rag: Include local vector retrieval when available.
        federated: Include configured mounted child projects.
        mounts_json: Optional JSON list of mount ids to consult, e.g. ["robotics"].
    """
    types: list[str] | None = None
    mounts: list[str] | None = None
    if types_json.strip():
        try:
            loaded = json.loads(types_json)
            if isinstance(loaded, list):
                types = [str(item) for item in loaded]
        except Exception:
            types = [item.strip() for item in types_json.split(",") if item.strip()]
    if mounts_json.strip():
        try:
            loaded = json.loads(mounts_json)
            if isinstance(loaded, list):
                mounts = [str(item) for item in loaded]
        except Exception:
            mounts = [item.strip() for item in mounts_json.split(",") if item.strip()]
    project = _get_project()
    if federated:
        return _json(
            project.federated_find(
                query,
                limit=limit,
                mounts=mounts,
                types=types,
                topic=topic or None,
                entity=entity or None,
                status=status or None,
                freshness=freshness or None,
                workflow=workflow or None,
                explain=explain,
                rag=rag,
            )
        )
    return _json(
        project.find(
            query,
            limit=limit,
            types=types,
            topic=topic or None,
            entity=entity or None,
            status=status or None,
            freshness=freshness or None,
            workflow=workflow or None,
            explain=explain,
            rag=rag,
        )
    )


@mcp.tool()
def permissions_doctor() -> str:
    """Inspect local permission metadata, public-by-default status, and audit-log configuration."""
    return _json(_get_project().permissions_doctor())


@mcp.tool()
def mount_list() -> str:
    """List configured mounted child projects and their current health."""
    return _json(_get_project().mount_list())


@mcp.tool()
def think(query: str, limit: int = 5, mode: str = "deterministic", runner: str = "auto", dry_run: bool = False, federated: bool = False, mounts_json: str = "") -> str:
    """
    Synthesize from retrieved project evidence with explicit gaps and conflicts.

    Args:
        query: Question to answer from the project knowledge base.
        limit: Maximum evidence records to include.
        mode: deterministic, runner, or hybrid.
        runner: Preferred local runner when using runner-backed synthesis.
        dry_run: If True, materialize the synthesis session without launching a runner.
    """
    mounts: list[str] | None = None
    if mounts_json.strip():
        try:
            loaded = json.loads(mounts_json)
            if isinstance(loaded, list):
                mounts = [str(item) for item in loaded]
        except Exception:
            mounts = [item.strip() for item in mounts_json.split(",") if item.strip()]
    if federated:
        return _json(_get_project().federated_think(query, limit=limit, mounts=mounts, mode=mode, runner=runner, dry_run=dry_run))
    return _json(_get_project().think(query, limit=limit, mode=mode, runner=runner, dry_run=dry_run))


@mcp.tool()
def register_think_result(result_json: str, artifact_path: str, title: str = "") -> str:
    """
    Register a persisted think result as an integrity-tracked artifact and claim-candidate source.

    Args:
        result_json: JSON-encoded think result envelope.
        artifact_path: Repo-relative or absolute path to the persisted think result artifact.
        title: Optional artifact title override.
    """
    return _json(_get_project().register_think_result(json.loads(result_json), artifact_path=artifact_path, title=title or None))


@mcp.tool()
def think_sessions(limit: int = 20) -> str:
    """List recorded runner-backed think sessions."""
    return _json(_get_project().think_sessions(limit=limit))


@mcp.tool()
def think_session_status(session_id: str) -> str:
    """Inspect a specific runner-backed think session."""
    return _json(_get_project().think_session(session_id))


@mcp.tool()
def capture(text: str = "", file_path: str = "", url: str = "", type: str = "note", workflow: str = "") -> str:
    """
    Capture a note, local file, or URL into the local project's topics/inbox.

    This is local-write only. Remote deployments should expose a narrower
    propose/candidate flow before allowing writes.
    """
    denied = _authorize_write("capture", "topics/inbox")
    if denied:
        return denied
    return _json(
        _get_project().capture(
            text,
            file_path=file_path or None,
            url=url or None,
            kind=type,
            workflow=workflow or None,
        )
    )


@mcp.tool()
def mode_active() -> str:
    """Return the active KRAIL knowledge mode for this project."""
    return _json(_get_project().active_mode())


@mcp.tool()
def mode_list() -> str:
    """List built-in KRAIL knowledge modes."""
    return _json(_get_project().modes())


@mcp.tool()
def topic_list(include_inbox: bool = False) -> str:
    """List durable topic pages, optionally including inbox captures."""
    return _json(_get_project().topic_list(include_inbox=include_inbox))


@mcp.tool()
def topic_upsert(
    topic: str,
    title: str = "",
    type: str = "topic",
    content: str = "",
    source_path: str = "",
    sources_json: str = "",
    entities_json: str = "",
    entity_type: str = "",
) -> str:
    """
    Create or update a durable topic page under topics/.

    JSON-encoded list arguments are used for sources and entities to stay
    compatible with MCP scalar tool inputs.
    """
    denied = _authorize_write("topic_upsert", _topic_target_path(topic))
    if denied:
        return denied
    return _json(
        _get_project().topic_upsert(
            topic,
            title=title or None,
            kind=type,
            content=content,
            source_path=source_path or None,
            sources=json.loads(sources_json) if sources_json else None,
            entities=json.loads(entities_json) if entities_json else None,
            entity_type=entity_type or None,
        )
    )


@mcp.tool()
def inbox_list(include_handled: bool = False) -> str:
    """List unhandled captures from topics/inbox."""
    return _json(_get_project().inbox_list(include_handled=include_handled))


@mcp.tool()
def inbox_promote(
    capture_path: str,
    topic: str,
    title: str = "",
    type: str = "topic",
    entities_json: str = "",
    entity_type: str = "",
) -> str:
    """Promote an inbox capture into a stable topic page and mark the capture handled."""
    denied = _authorize_write("inbox_promote", _topic_target_path(topic))
    if denied:
        return denied
    return _json(
        _get_project().inbox_promote(
            capture_path,
            topic=topic,
            title=title or None,
            kind=type,
            entities=json.loads(entities_json) if entities_json else None,
            entity_type=entity_type or None,
        )
    )


@mcp.tool()
def doctor() -> str:
    """Check local project health: manifest, core paths, active pack, and capture inbox."""
    return _json(_get_project().doctor())


@mcp.tool()
def graph_build(write: bool = True) -> str:
    """
    Build a markdown-frontmatter graph from local project notes.

    Args:
        write: If True, write graph artifacts such as research_plan/graph/graph.json.
    """
    if write:
        denied = _authorize_write("graph_build", "research_plan/graph")
        if denied:
            return denied
    return _json(_get_project().graph_build(write=write))


@mcp.tool()
def graph_validate() -> str:
    """Validate markdown graph frontmatter and relation structure."""
    return _json(_get_project().graph_validate())


@mcp.tool()
def graph_check() -> str:
    """Check whether committed markdown graph artifacts are fresh."""
    return _json(_get_project().graph_check())


@mcp.tool()
def graph_entities(entity_type: str = "", limit: int = 100) -> str:
    """
    List entities derived from markdown frontmatter.

    Args:
        entity_type: Optional entity type filter, e.g. Package or Method.
        limit: Maximum records to return.
    """
    return _json(_get_project().graph_entities(entity_type=entity_type or None, limit=limit))


@mcp.tool()
def graph_edges(entity: str = "", relation_type: str = "", limit: int = 100) -> str:
    """
    List graph edges derived from markdown frontmatter.

    Args:
        entity: Optional entity label to filter to incident edges.
        relation_type: Optional relation type filter.
        limit: Maximum records to return.
    """
    return _json(
        _get_project().graph_edges(
            entity=entity or None,
            relation_type=relation_type or None,
            limit=limit,
        )
    )


@mcp.tool()
def graph_docs(topic: str = "", kind: str = "", source: str = "", entity: str = "", limit: int = 100) -> str:
    """
    List markdown documents that contributed graph metadata.

    Args:
        topic: Optional topic filter.
        kind: Optional document kind filter.
        source: Optional source URL/text filter.
        entity: Optional entity label filter.
        limit: Maximum records to return.
    """
    return _json(
        _get_project().graph_docs(
            topic=topic or None,
            kind=kind or None,
            source=source or None,
            entity=entity or None,
            limit=limit,
        )
    )


@mcp.tool()
def graph_export(format: str = "json") -> str:
    """
    Export the markdown graph as json, mermaid, or summary text.

    Args:
        format: One of json, mermaid, or summary.
    """
    return _json(_get_project().graph_export(export_format=format))


@mcp.tool()
def vector_build(provider: str = "", model: str = "") -> str:
    """
    Build the local SQLite vector database at .krail/vector.sqlite.

    Args:
        provider: Optional embedding provider: local_hash, openai, or sentence_transformers.
        model: Optional embedding model name for the selected provider.
    """
    denied = _authorize_write("vector_build", ".krail/vector.sqlite")
    if denied:
        return denied
    return _json(_get_project().vector_build(provider=provider or None, model=model or None))


@mcp.tool()
def vector_search(query: str, limit: int = 10) -> str:
    """
    Search the local SQLite vector database for semantically similar chunks.

    Args:
        query: Search query.
        limit: Maximum chunks to return.
    """
    return _json(_get_project().vector_search(query, limit=limit))


@mcp.tool()
def sources_validate() -> str:
    """Validate the KRAIL source dependency manifest."""
    return _json(_get_project().sources_validate())


@mcp.tool()
def sources_list() -> str:
    """List dependency sources declared by the KRAIL source manifest."""
    return _json(_get_project().sources_list())


@mcp.tool()
def sources_check(write: bool = True) -> str:
    """Snapshot dependency sources and detect changed source hashes."""
    if write:
        denied = _authorize_write("sources_check", "research_plan/state")
        if denied:
            return denied
    return _json(_get_project().sources_check(write=write))


@mcp.tool()
def sources_changed() -> str:
    """List sources marked changed by the last source check."""
    return _json(_get_project().sources_changed())


@mcp.tool()
def sources_affected(source_ids_json: str = "") -> str:
    """List markdown documents affected by changed or selected source IDs."""
    source_ids = json.loads(source_ids_json) if source_ids_json else None
    return _json(_get_project().sources_affected(source_ids=source_ids))


@mcp.tool()
def ci_init(path: str = ".github/workflows/krail-local-preview.yml") -> str:
    """Write a GitHub Actions workflow that runs KRAIL local-preview checks."""
    denied = _authorize_write("ci_init", path)
    if denied:
        return denied
    return _json(_get_project().ci_init(path=path))


@mcp.tool()
def pack_active() -> str:
    """Return the active knowledge pack for this local project, if one is configured."""
    return _json(_get_project().pack("active"))


@mcp.tool()
def list_agents() -> str:
    """List local CLI agents KRAIL can dispatch as workers."""
    return _json(_get_project().agents())


@mcp.tool()
def scaffold_krail_agents(force: bool = False) -> str:
    """Write KRAIL doctor/platform prompts, checklists, role configs, and skill docs."""
    denied = _authorize_write("scaffold_krail_agents", "agents")
    if denied:
        return denied
    return _json(_get_project().scaffold_krail_agents(force=force))


@mcp.tool()
def agent_prompt(role: str = "doctor", task: str = "") -> str:
    """Render a KRAIL role prompt for another local agent."""
    return _json(_get_project().agent_prompt(role, task=task))


@mcp.tool()
def create_task(title: str, description: str = "", runner: str = "codex_cli", role: str = "research") -> str:
    """
    Create a repo-backed task without dispatching it.

    Args:
        title: Short task title.
        description: Detailed task description.
        runner: Preferred local runner, e.g. codex_cli or claude_code.
        role: Worker role label.
    """
    denied = _authorize_write("create_task", "research_plan/tasks")
    if denied:
        return denied
    return _json(_get_project().create_task(title, description=description, runner=runner, role=role))


@mcp.tool()
def list_tasks() -> str:
    """List repo-backed local KRAIL tasks."""
    return _json(_get_project().list_tasks())


@mcp.tool()
def dispatch_task(task_id: str, runner: str = "", dry_run: bool = True) -> str:
    """
    Dispatch a task to a local CLI runner.

    Defaults to dry_run=True so agents can inspect the exact command and work
    order before launching another agent process.
    """
    if not dry_run:
        denied = _authorize_execute("dispatch_task", f"task:{task_id}", private_by_default=False)
        if denied:
            return denied
    return _json(_get_project().dispatch_task(task_id, runner=runner or None, dry_run=dry_run))


@mcp.tool()
def list_workflows() -> str:
    """List workflow IDs declared by the active knowledge pack and local workflow specs."""
    return _json(_get_project().list_workflows())


@mcp.tool()
def workflow_templates() -> str:
    """List built-in KRAIL workflow templates."""
    return _json(_get_project().workflow_templates())


@mcp.tool()
def init_workflow(workflow_id: str, force: bool = False, template: str = "") -> str:
    """Create a local workflow spec under research_plan/workflows."""
    denied = _authorize_write("init_workflow", f"research_plan/workflows/{_normalize_path(workflow_id)}.yaml")
    if denied:
        return denied
    return _json(_get_project().init_workflow(workflow_id, force=force, template=template or None))


@mcp.tool()
def show_workflow(workflow_id: str) -> str:
    """Show a local workflow spec."""
    return _json(_get_project().show_workflow(workflow_id))


@mcp.tool()
def validate_workflow(workflow_id: str) -> str:
    """Validate a local workflow spec."""
    return _json(_get_project().validate_workflow(workflow_id))


@mcp.tool()
def run_workflow(workflow_id: str, runner: str = "codex_cli", dry_run: bool = True) -> str:
    """
    Create and optionally dispatch a pack-defined workflow task.

    Defaults to dry_run=True for safety when called by agents.
    """
    if not dry_run:
        denied = _authorize_execute("run_workflow", f"workflow:{workflow_id}", private_by_default=False)
        if denied:
            return denied
    return _json(_get_project().run_workflow(workflow_id, runner=runner, dry_run=dry_run))


@mcp.tool()
def execute_workflow(workflow_id: str, dry_run: bool = True, force: bool = False) -> str:
    """
    Execute a local workflow spec.

    Defaults to dry_run=True for safety when called by agents.
    """
    if not dry_run:
        denied = _authorize_execute("execute_workflow", f"workflow:{workflow_id}", private_by_default=False)
        if denied:
            return denied
    return _json(_get_project().execute_workflow(workflow_id, dry_run=dry_run, force=force))


@mcp.tool()
def workflow_dashboard(limit: int = 50) -> str:
    """Summarize workflow and agent session status for operators."""
    return _json(_get_project().workflow_dashboard(limit=limit))


@mcp.tool()
def listener_list() -> str:
    """List local listener specs and current listener state."""
    return _json(_get_project().listener_list())


@mcp.tool()
def listener_templates() -> str:
    """List built-in listener templates and supported listener types."""
    return _json(_get_project().listener_templates())


@mcp.tool()
def listener_init(template: str, listener_id: str = "", force: bool = False) -> str:
    """Create a listener spec from a built-in template or listener type."""
    denied = _authorize_write("listener_init", f"research_plan/listeners/{_normalize_path(listener_id or template)}.yaml")
    if denied:
        return denied
    return _json(_get_project().listener_init(template, listener_id=listener_id or None, force=force))


@mcp.tool()
def listener_validate(listener_id: str = "") -> str:
    """Validate one listener spec or all listener specs."""
    return _json(_get_project().listener_validate(listener_id or None))


@mcp.tool()
def listener_doctor() -> str:
    """Diagnose listener health, failures, missing workflows, and unhandled events."""
    return _json(_get_project().listener_doctor())


@mcp.tool()
def listener_poll(listener_id: str = "", dry_run: bool = True, execute: bool = False) -> str:
    """
    Poll one listener or all listeners.

    Defaults to dry_run=True and execute=False for agent safety.
    """
    if execute and not dry_run:
        denied = _authorize_execute("listener_poll", f"listener:{listener_id or '*'}", private_by_default=False)
        if denied:
            return denied
    return _json(_get_project().listener_poll(listener_id or None, dry_run=dry_run, execute=execute))


@mcp.tool()
def event_list(limit: int = 20, listener_id: str = "") -> str:
    """List recorded listener events."""
    return _json(_get_project().event_list(limit=limit, listener_id=listener_id or None))


@mcp.tool()
def event_show(event_id: str) -> str:
    """Show one recorded listener event."""
    return _json(_get_project().event_show(event_id))


@mcp.tool()
def event_replay(event_id: str, dry_run: bool = True) -> str:
    """Replay an event's workflow trigger. Defaults to dry_run=True."""
    if not dry_run:
        denied = _authorize_execute("event_replay", f"listener_event:{event_id}", private_by_default=False)
        if denied:
            return denied
    return _json(_get_project().event_replay(event_id, dry_run=dry_run))


@mcp.tool()
def queue_status(queue_id: str) -> str:
    """Show queue counts and recent batch claims."""
    return _json(_get_project().queue_status(queue_id))


@mcp.tool()
def queue_claim(queue_id: str, limit: int = 10, where_json: str = "", owner: str = "", lease_minutes: int = 120) -> str:
    """Reserve a queue batch. where_json is an optional JSON list of key=value filters."""
    where = json.loads(where_json) if where_json else None
    return _json(_get_project().queue_claim(queue_id, limit=limit, where=where, owner=owner or None, lease_minutes=lease_minutes))


@mcp.tool()
def graph_summary() -> str:
    """Return graph counts and warnings without dumping full graph JSON."""
    return _json(_get_project().graph_summary())


@mcp.tool()
def federated_graph_summary(mounts_json: str = "") -> str:
    """Return graph summaries for the local project and configured mounted child projects."""
    mounts: list[str] | None = None
    if mounts_json.strip():
        try:
            loaded = json.loads(mounts_json)
            if isinstance(loaded, list):
                mounts = [str(item) for item in loaded]
        except Exception:
            mounts = [item.strip() for item in mounts_json.split(",") if item.strip()]
    return _json(_get_project().federated_graph_summary(mounts=mounts))


@mcp.tool()
def graph_diff() -> str:
    """Compare the current markdown graph to the saved graph artifact."""
    return _json(_get_project().graph_diff())


@mcp.tool()
def repo_inspect(path_or_url: str) -> str:
    """Inspect a local repository path for manifests, framework markers, and endpoint files."""
    return _json(_get_project().repo_inspect(path_or_url))


@mcp.tool()
def workflow_runs(limit: int = 20) -> str:
    """List local workflow run records."""
    return _json(_get_project().workflow_runs(limit=limit))


@mcp.tool()
def workflow_status(run_id: str) -> str:
    """Show one local workflow run result."""
    return _json(_get_project().workflow_status(run_id))


@mcp.tool()
def get_series(series_id: str) -> str:
    """
    Fetch a named time-series as a table of (date, value) rows.

    Args:
        series_id: Series identifier (e.g. "unemployment_rate_nj_2010_2024").
    """
    df = _get_project().series(series_id)
    return _json(df)


# ---------------------------------------------------------------------------
# SQL query tool
# ---------------------------------------------------------------------------


@mcp.tool()
def query_sql(sql: str) -> str:
    """
    Run a DuckDB SQL query against the project's artifact database.

    Use list_classes / get_entities first to discover available tables.
    Tables are named after ontology classes (snake_case), e.g. SELECT * FROM county LIMIT 5.

    Args:
        sql: A valid DuckDB SQL statement.
    """
    df = _get_project().query(sql)
    return _json(df)


# ---------------------------------------------------------------------------
# Python execution
# ---------------------------------------------------------------------------


@mcp.tool()
def execute_python(code: str, timeout: int = 60) -> str:
    """
    Execute arbitrary Python code in a project-scoped subprocess and return
    stdout, stderr, any returned dataframes, and figures.

    This helper exposes pandas, numpy, matplotlib, statsmodels, and a
    pre-connected DuckDB database via the `db` variable. It is KRAIL-mediated
    execution, not host-level isolation.

    Args:
        code: Python source code to execute.
        timeout: Max seconds before the subprocess is killed (default 60).
    """
    denied = _authorize_execute("execute_python", ".krail/tools/execute_python")
    if denied:
        return denied
    denied = _authorize_execute(
        "execute_python",
        "execute_python",
        require_explicit_tool_scope=True,
    )
    if denied:
        return denied
    result = _get_project().execute(code, timeout=timeout)
    return _json(result)


# ---------------------------------------------------------------------------
# Analysis plugins
# ---------------------------------------------------------------------------


@mcp.tool()
def run_analysis(plugin_slug: str, **kwargs) -> str:
    """
    Run a named analysis plugin registered in the project.

    Args:
        plugin_slug: Plugin identifier (e.g. "regression", "correlation_matrix").
        **kwargs: Plugin-specific configuration options passed as keyword args.
    """
    result = _get_project().run_analysis(plugin_slug, **kwargs)
    return _json(result)


# ---------------------------------------------------------------------------
# Data catalog / registry
# ---------------------------------------------------------------------------


@mcp.tool()
def search_registry(query: str, provider: str = "", geography: str = "") -> str:
    """
    Search the platform's data catalog for available datasets.

    Args:
        query: What you're looking for (e.g. "NJ unemployment 2020").
        provider: Optional filter, e.g. "BLS", "Census", "FRED".
        geography: Optional filter, e.g. "NJ", "county", "national".
    """
    return _json(_get_project().search_registry(
        query,
        provider=provider or None,
        geography=geography or None,
    ))


@mcp.tool()
def discover_templates(query: str, tags: str = "") -> str:
    """
    Search for connector templates (Census, FRED, BLS, etc.) that can be added to the project.

    Args:
        query: What kind of data you need (e.g. "housing price index").
        tags: Comma-separated tags to filter by (e.g. "economic,nj").
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    return _json(_get_project().discover(query, tags=tag_list))


# ---------------------------------------------------------------------------
# Hydration
# ---------------------------------------------------------------------------


@mcp.tool()
def hydrate(pipeline_slug: str = "") -> str:
    """
    Trigger a hydration pipeline to refresh the project's data.

    Args:
        pipeline_slug: Pipeline to run. Leave empty to use the project's default pipeline.
    """
    result = _get_project().hydrate(pipeline_slug=pipeline_slug or None)
    return _json(result)


# ---------------------------------------------------------------------------
# Research integrity
# ---------------------------------------------------------------------------


@mcp.tool()
def integrity_status() -> str:
    """Return the overall research integrity report: assumptions, sources, claims, and their verification status."""
    return _json(_get_project().integrity_status())


@mcp.tool()
def integrity_assumptions() -> str:
    """List all recorded research assumptions and whether they have been verified."""
    return _json(_get_project().integrity_assumptions())


@mcp.tool()
def integrity_sources() -> str:
    """List all evidence sources cited in the project."""
    return _json(_get_project().integrity_sources())


@mcp.tool()
def integrity_claims() -> str:
    """List all empirical claims and their supporting evidence."""
    return _json(_get_project().integrity_claims())


@mcp.tool()
def integrity_claim_candidates() -> str:
    """List registered claim candidates awaiting review or promotion."""
    return _json(_get_project().integrity_claim_candidates())


@mcp.tool()
def integrity_artifacts() -> str:
    """List integrity-tracked artifact lineage records."""
    return _json(_get_project().integrity_artifact_lineage())


@mcp.tool()
def integrity_promote_claim_candidate(candidate_key: str, status: str = "needs_evidence") -> str:
    """
    Promote a claim candidate into the canonical claim ledger.

    Args:
        candidate_key: Claim candidate key from integrity_claim_candidates.
        status: Target canonical claim status, e.g. needs_evidence or supported.
    """
    return _json(_get_project().apply_integrity_claim_candidate_promotion(candidate_key, status=status))


@mcp.tool()
def integrity_reproducibility_rerun(outputs_json: str, run_id: str = "rerun-verification", scope: str = "health") -> str:
    """
    Re-run reproducibility checks for an artifact payload.

    Args:
        outputs_json: JSON map of artifact paths to contents.
        run_id: Verification run identifier.
        scope: Verification scope label.
    """
    return _json(_get_project().apply_integrity_reproducibility_rerun(json.loads(outputs_json), run_id=run_id, scope=scope))


@mcp.tool()
def integrity_freshness_evaluate(as_of: str = "") -> str:
    """Evaluate source freshness as of an optional timestamp."""
    return _json(_get_project().apply_integrity_freshness_evaluation(as_of=as_of or None))


@mcp.tool()
def integrity_source_detail(source_key: str) -> str:
    """Inspect a specific integrity source."""
    return _json(_get_project().integrity_source_detail(source_key))


@mcp.tool()
def integrity_claim_detail(claim_key: str) -> str:
    """Inspect a specific integrity claim."""
    return _json(_get_project().integrity_claim_detail(claim_key))


@mcp.tool()
def integrity_verification_runs() -> str:
    """List integrity verification runs."""
    return _json(_get_project().integrity_verification_runs())


@mcp.tool()
def integrity_benchmark(retrieval_limit: int = 10) -> str:
    """Run the default integrity benchmark corpus."""
    return _json(_get_project().integrity_benchmark(retrieval_limit=retrieval_limit))


@mcp.tool()
def integrity_stale_graph() -> str:
    """Inspect stale source/claim/artifact relationships."""
    return _json(_get_project().integrity_stale_graph())


@mcp.tool()
def integrity_promote_artifact(artifact_path: str, target_state: str = "verified") -> str:
    """Promote an artifact to a target trust state."""
    return _json(_get_project().apply_integrity_artifact_promotion(artifact_path, target_state=target_state))


@mcp.tool()
def integrity_artifact_detail(artifact_path: str) -> str:
    """Inspect a specific integrity-tracked artifact."""
    return _json(_get_project().integrity_artifact_detail(artifact_path))


@mcp.tool()
def integrity_graph() -> str:
    """Return the integrity dependency graph."""
    return _json(_get_project().integrity_dependency_graph())


@mcp.tool()
def integrity_retrieve(
    query: str,
    limit: int = 10,
    artifact_types_json: str = "",
    claim_statuses_json: str = "",
    source_freshness_json: str = "",
    date_from: str = "",
    date_to: str = "",
    include_stale: bool = False,
    include_blocked: bool = False,
) -> str:
    """
    Retrieve integrity records with optional filters.

    JSON-encoded list arguments are used to stay compatible with MCP scalar tool inputs.
    """
    artifact_types = json.loads(artifact_types_json) if artifact_types_json else None
    claim_statuses = json.loads(claim_statuses_json) if claim_statuses_json else None
    source_freshness = json.loads(source_freshness_json) if source_freshness_json else None
    return _json(
        _get_project().integrity_retrieve(
            query,
            limit=limit,
            artifact_types=artifact_types,
            claim_statuses=claim_statuses,
            source_freshness=source_freshness,
            date_from=date_from or None,
            date_to=date_to or None,
            include_stale=include_stale,
            include_blocked=include_blocked,
        )
    )


@mcp.tool()
def integrity_rerun_plan(assumption_key: str, apply: bool = False) -> str:
    """
    Preview or apply the rerun plan triggered by an assumption change.

    When an assumption changes (e.g. a new data vintage), this identifies which
    downstream pipelines and analyses need to be re-executed.

    Args:
        assumption_key: The assumption that changed (from integrity_assumptions).
        apply: If True, create the actual rerun tasks. Default False (preview only).
    """
    if apply:
        result = _get_project().apply_integrity_rerun_plan(assumption_key)
    else:
        result = _get_project().integrity_rerun_plan(assumption_key)
    return _json(result)


# ---------------------------------------------------------------------------
# Runner Protocol / Session tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_project_state() -> str:
    """Return a comprehensive snapshot of the project's state: ontology classes, data sources, and pipelines."""
    return _json(_get_project().get_state())


@mcp.tool()
def get_work_order(work_order_id: str = "") -> str:
    """
    Fetch the typed WorkOrder for the current session.

    Args:
        work_order_id: Optional. If omitted, uses the RAIL_WORK_ORDER_ID from the environment.
    """
    return _json(_get_project().get_work_order(work_order_id or None))


@mcp.tool()
def submit_session_result(result: dict, session_id: str = "") -> str:
    """
    Submit the final structured session result for the current session.

    Args:
        result: The structured output (JSON object) matching the session's work order.
        session_id: Optional. If omitted, uses the RAIL_SESSION_ID from the environment.
    """
    return _json(_get_project().submit_session_result(result, session_id=session_id or None))


@mcp.tool()
def ask(question: str, session_id: str = "") -> str:
    """
    Ask a question to the planner or human mid-session if you are blocked or need clarification.

    Args:
        question: The question to ask.
        session_id: Optional. If omitted, uses the RAIL_SESSION_ID from the environment.
    """
    return _json(_get_project().ask(question, session_id=session_id or None))


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


@mcp.tool()
def list_secrets() -> str:
    """List secret key names configured for this project (values are never returned)."""
    denied = _authorize_secret("list_secrets")
    if denied:
        return denied
    result = _get_project().list_secrets()
    allowed_secret_names = _runner_scope()["allowed_secrets"]
    if allowed_secret_names and "*" not in allowed_secret_names and isinstance(result, list):
        result = [
            item for item in result
            if str((item or {}).get("keyName") or (item or {}).get("key") or "") in allowed_secret_names
        ]
    return _json(result)


@mcp.tool()
def set_secret(key: str, value: str) -> str:
    """
    Store an API key or credential in the project's secrets vault.

    Args:
        key: Secret name, e.g. "FRED_API_KEY".
        value: Plaintext secret value.
    """
    denied = _authorize_secret("set_secret", key=key)
    if denied:
        return denied
    return _json(_get_project().set_secret(key, value))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="rail-mcp", description="RAIL MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport type (default: stdio). Use 'sse' or 'streamable-http' for URL-based remote access.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host for sse/streamable-http (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8001, help="Port for sse/streamable-http (default: 8001)")
    parser.add_argument("--project", help="Override RAIL_PROJECT env var")
    parser.add_argument("--api-url", help="Override RAIL_API_URL env var")
    parser.add_argument("--api-key", help="Override RAIL_API_KEY env var")
    parser.add_argument("--local", action="store_true", help="Load project from disk (sets RAIL_LOCAL=1)")
    parser.add_argument("--path", default=".", help="Local project path (default: .)")
    args = parser.parse_args()

    if args.project:
        os.environ["RAIL_PROJECT"] = args.project
    if args.api_url:
        os.environ["RAIL_API_URL"] = args.api_url
    if args.api_key:
        os.environ["RAIL_API_KEY"] = args.api_key
    if args.local:
        os.environ["RAIL_LOCAL"] = "1"
        os.environ["RAIL_PATH"] = args.path

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    elif args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
