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
from typing import Any

from mcp.server.fastmcp import FastMCP

import rail

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
def search(query: str, limit: int = 10, explain: bool = False) -> str:
    """
    Search local project evidence. Returns ranked records, not synthesis.

    Args:
        query: Search query.
        limit: Maximum number of hits.
        explain: Include ranking-signal notes when available.
    """
    project = _get_project()
    if hasattr(project._backend, "knowledge"):
        return _json(project._backend.knowledge.search(query, limit=limit, explain=explain))
    return _json({"query": query, "hits": project.search(query)[:limit]})


@mcp.tool()
def think(query: str, limit: int = 5) -> str:
    """
    Synthesize from retrieved project evidence with explicit gaps and conflicts.

    Args:
        query: Question to answer from the project knowledge base.
        limit: Maximum evidence records to include.
    """
    return _json(_get_project().think(query, limit=limit))


@mcp.tool()
def capture(text: str = "", file_path: str = "", url: str = "", type: str = "note", workflow: str = "") -> str:
    """
    Capture a note, local file, or URL into the local project's topics/inbox.

    This is local-write only. Remote deployments should expose a narrower
    propose/candidate flow before allowing writes.
    """
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
def ci_init(path: str = ".github/workflows/krail-local-preview.yml") -> str:
    """Write a GitHub Actions workflow that runs KRAIL local-preview checks."""
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
def create_task(title: str, description: str = "", runner: str = "codex_cli", role: str = "research") -> str:
    """
    Create a repo-backed task without dispatching it.

    Args:
        title: Short task title.
        description: Detailed task description.
        runner: Preferred local runner, e.g. codex_cli or claude_code.
        role: Worker role label.
    """
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
    return _json(_get_project().dispatch_task(task_id, runner=runner or None, dry_run=dry_run))


@mcp.tool()
def list_workflows() -> str:
    """List workflow IDs declared by the active knowledge pack."""
    return _json(_get_project().list_workflows())


@mcp.tool()
def run_workflow(workflow_id: str, runner: str = "codex_cli", dry_run: bool = True) -> str:
    """
    Create and optionally dispatch a pack-defined workflow task.

    Defaults to dry_run=True for safety when called by agents.
    """
    return _json(_get_project().run_workflow(workflow_id, runner=runner, dry_run=dry_run))


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
    Execute arbitrary Python code in the project's sandbox and return stdout,
    stderr, any returned dataframes, and figures.

    The sandbox has access to pandas, numpy, matplotlib, statsmodels, and
    a pre-connected DuckDB database via the `db` variable.

    Args:
        code: Python source code to execute.
        timeout: Max seconds before the sandbox kills the process (default 60).
    """
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
    return _json(_get_project().list_secrets())


@mcp.tool()
def set_secret(key: str, value: str) -> str:
    """
    Store an API key or credential in the project's secrets vault.

    Args:
        key: Secret name, e.g. "FRED_API_KEY".
        value: Plaintext secret value.
    """
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
