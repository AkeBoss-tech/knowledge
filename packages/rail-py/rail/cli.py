import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import rail
from rail.bootstrap import bootstrap_future_project
from rail.knowledge import DEFAULT_PACKS, KnowledgeRuntime

def _print_json(data: Any):
    print(json.dumps(data, indent=2))

def _get_project(args: argparse.Namespace) -> rail.Project:
    if args.local:
        return rail.local(path=args.path)
    
    slug = args.project or os.environ.get("RAIL_PROJECT")
    if not slug:
        print("Error: --project or RAIL_PROJECT env var is required", file=sys.stderr)
        sys.exit(1)
        
    return rail.connect(
        slug=slug,
        api_url=args.api_url,
        api_key=args.api_key,
    )

def cmd_init(args: argparse.Namespace):
    target = Path(args.directory).resolve()
    name = args.name or target.name
    root = bootstrap_future_project(target, name=name, slug=args.slug, mode=args.mode)
    project = rail.local(str(root))
    if args.pack:
        project.pack("use", args.pack)
    _print_json({"status": "initialized", "path": str(root), "pack": args.pack, "mode": args.mode})

def cmd_search(project: rail.Project, args: argparse.Namespace):
    if hasattr(project._backend, "knowledge"):
        _print_json(project._backend.knowledge.search(args.query, limit=args.limit, explain=args.explain, rag=args.rag))
    else:
        _print_json(project.search(args.query))

def cmd_think(project: rail.Project, args: argparse.Namespace):
    _print_json(project.think(args.query, limit=args.limit))

def cmd_capture(project: rail.Project, args: argparse.Namespace):
    text = args.text or ""
    if args.stdin:
        text = sys.stdin.read()
    _print_json(
        project.capture(
            text,
            file_path=args.file,
            url=args.url,
            kind=args.type,
            workflow=args.workflow,
            title=args.title,
            topics=args.topic,
            entities=args.entity,
            entity_type=args.entity_type,
        )
    )

def cmd_doctor(project: rail.Project, args: argparse.Namespace):
    _print_json(project.doctor())

def cmd_pack(project: rail.Project, args: argparse.Namespace):
    _print_json(project.pack(args.pack_command, getattr(args, "pack_id", None)))

def cmd_agent(project: rail.Project, args: argparse.Namespace):
    if args.agent_command == "list":
        _print_json(project.agents())
    elif args.agent_command == "run":
        created = project.create_task(
            args.prompt,
            description=args.prompt,
            runner=args.runner,
            role=args.role,
        )
        _print_json(project.dispatch_task(created["task"]["id"], runner=args.runner, dry_run=args.dry_run))

def cmd_task(project: rail.Project, args: argparse.Namespace):
    if args.task_command == "list":
        _print_json(project.list_tasks())
    elif args.task_command == "create":
        _print_json(
            project.create_task(
                args.title,
                description=args.description or args.title,
                runner=args.runner,
                workflow=args.workflow,
                role=args.role,
            )
        )
    elif args.task_command == "work-order":
        _print_json(project.create_work_order(args.task_id))
    elif args.task_command == "dispatch":
        _print_json(project.dispatch_task(args.task_id, runner=args.runner, dry_run=args.dry_run))

def cmd_workflow(project: rail.Project, args: argparse.Namespace):
    if args.workflow_command == "list":
        _print_json(project.list_workflows())
    elif args.workflow_command == "run":
        _print_json(project.run_workflow(args.workflow_id, runner=args.runner, dry_run=args.dry_run))

def cmd_graph(project: rail.Project, args: argparse.Namespace):
    if args.graph_command == "build":
        _print_json(project.graph_build(write=not args.no_write))
    elif args.graph_command == "validate":
        result = project.graph_validate()
        _print_json(result)
        if not result.get("ok", False):
            sys.exit(1)
    elif args.graph_command == "check":
        result = project.graph_check()
        _print_json(result)
        if not result.get("ok", False):
            sys.exit(1)
    elif args.graph_command == "entities":
        _print_json(project.graph_entities(entity_type=args.type, limit=args.limit))
    elif args.graph_command == "edges":
        _print_json(project.graph_edges(entity=args.entity, relation_type=args.type, limit=args.limit))
    elif args.graph_command == "docs":
        _print_json(
            project.graph_docs(
                topic=args.topic,
                kind=args.kind,
                source=args.source,
                entity=args.entity,
                limit=args.limit,
            )
        )
    elif args.graph_command == "export":
        result = project.graph_export(export_format=args.format)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(result["content"], encoding="utf-8")
            _print_json({"status": "written", "format": args.format, "path": str(output)})
        else:
            print(result["content"], end="")

def cmd_vector(project: rail.Project, args: argparse.Namespace):
    if args.vector_command == "build":
        _print_json(project.vector_build(provider=args.provider, model=args.model))
    elif args.vector_command == "search":
        _print_json(project.vector_search(args.query, limit=args.limit))

def cmd_ci(project: rail.Project, args: argparse.Namespace):
    if args.ci_command == "init":
        _print_json(project.ci_init(path=args.ci_path))

def cmd_query(project: rail.Project, args: argparse.Namespace):
    if args.command == "sql":
        _print_json(project.query(args.sql).to_dict(orient="records"))
    elif args.command == "classes":
        _print_json(project.classes())
    elif args.command == "entities":
        _print_json(project.entities(args.class_name, limit=args.limit).to_dict(orient="records"))

def cmd_hydrate(project: rail.Project, args: argparse.Namespace):
    _print_json(project.hydrate(pipeline_slug=args.pipeline, mode=args.mode))


def cmd_reconcile(project: rail.Project, args: argparse.Namespace):
    _print_json(project.reconcile())

def cmd_series(project: rail.Project, args: argparse.Namespace):
    _print_json(project.series(args.series_id).to_dict(orient="records"))

def cmd_secrets(project: rail.Project, args: argparse.Namespace):
    if args.command == "list":
        _print_json(project.list_secrets())
    elif args.command == "set":
        _print_json(project.set_secret(args.key, args.value))

def cmd_integrity(project: rail.Project, args: argparse.Namespace):
    command = getattr(args, "integrity_command", None) or getattr(args, "command", None)
    if command == "status":
        _print_json(project.integrity_status())
    elif command == "assumptions":
        _print_json(project.integrity_assumptions())
    elif command == "sources":
        _print_json(project.integrity_sources())
    elif command == "claims":
        _print_json(project.integrity_claims())
    elif command == "source":
        _print_json(project.integrity_source_detail(args.source_key))
    elif command == "claim":
        _print_json(project.integrity_claim_detail(args.claim_key))
    elif command == "verification-runs":
        _print_json(project.integrity_verification_runs())
    elif command == "stale-graph":
        _print_json(project.integrity_stale_graph())
    elif command == "graph":
        _print_json(project.integrity_dependency_graph())
    elif command == "retrieve":
        _print_json(
            project.integrity_retrieve(
                args.query_text,
                limit=args.limit,
                artifact_types=args.artifact_types,
                claim_statuses=args.claim_statuses,
                source_freshness=args.source_freshness,
                date_from=args.date_from,
                date_to=args.date_to,
                include_stale=args.include_stale,
                include_blocked=args.include_blocked,
            )
        )
    elif command == "promote":
        _print_json(project.apply_integrity_artifact_promotion(args.artifact_path, target_state=args.target_state))
    elif command == "rerun":
        if args.apply:
            _print_json(project.apply_integrity_rerun_plan(args.assumption_key))
        else:
            _print_json(project.integrity_rerun_plan(args.assumption_key))

def cmd_work_order(project: rail.Project, args: argparse.Namespace):
    """Fetch and print the current work order."""
    wo_id = getattr(args, "id", None)
    _print_json(project.get_work_order(work_order_id=wo_id))

def cmd_result(project: rail.Project, args: argparse.Namespace):
    """Submit a session result."""
    if not args.file:
        print("Error: --file is required", file=sys.stderr)
        sys.exit(1)
    with open(args.file) as f:
        result = json.load(f)
    _print_json(project.submit_session_result(result, session_id=getattr(args, "session", None)))

def cmd_ask(project: rail.Project, args: argparse.Namespace):
    """Ask a question."""
    if not args.question:
        print("Error: --question is required", file=sys.stderr)
        sys.exit(1)
    _print_json(project.ask(args.question, session_id=getattr(args, "session", None)))

def main():
    parser = argparse.ArgumentParser(prog="rail", description="KRAIL CLI")
    parser.add_argument("--project", help="Project slug (overrides RAIL_PROJECT)")
    parser.add_argument("--api-url", help="API URL (overrides RAIL_API_URL)")
    parser.add_argument("--api-key", help="API Key (overrides RAIL_API_KEY)")
    parser.add_argument("--local", action="store_true", help="Load from local path")
    parser.add_argument("--path", default=".", help="Local project path")
    parser.add_argument("--version", action="store_true", help="Print KRAIL version and exit")

    subparsers = parser.add_subparsers(dest="command")

    # Init
    init_parser = subparsers.add_parser("init", help="Initialize a local knowledge project")
    init_parser.add_argument("directory", help="Project directory")
    init_parser.add_argument("--name", help="Project display name")
    init_parser.add_argument("--slug", help="Project slug")
    init_parser.add_argument("--pack", choices=["research-intelligence", "company-brain", "software-architecture", "policy-compiler"], help="Activate a knowledge pack")
    init_parser.add_argument("--mode", choices=["ontology_first", "markdown_graph"], default="ontology_first", help="Project scaffold mode")

    # Search
    s_parser = subparsers.add_parser("search", help="Search local project evidence")
    s_parser.add_argument("query", help="Search query")
    s_parser.add_argument("--limit", type=int, default=10)
    s_parser.add_argument("--explain", action="store_true", help="Explain local ranking signals")
    s_parser.add_argument("--rag", action="store_true", help="Use the local SQLite vector index for RAG-style retrieval")

    # Think
    t_parser = subparsers.add_parser("think", help="Synthesize from local evidence with gaps/conflicts")
    t_parser.add_argument("query", help="Question to answer")
    t_parser.add_argument("--limit", type=int, default=5)

    # Capture
    c_parser = subparsers.add_parser("capture", help="Capture a note, file, URL, or stdin into topics/inbox")
    c_parser.add_argument("text", nargs="?", help="Text to capture")
    c_parser.add_argument("--file", help="File to capture")
    c_parser.add_argument("--url", help="URL to record")
    c_parser.add_argument("--stdin", action="store_true", help="Read capture text from stdin")
    c_parser.add_argument("--type", default="note", help="Capture type")
    c_parser.add_argument("--workflow", help="Workflow hint for later triage")
    c_parser.add_argument("--title", help="Frontmatter title")
    c_parser.add_argument("--topic", action="append", help="Frontmatter topic; can be repeated")
    c_parser.add_argument("--entity", action="append", help="Frontmatter entity; can be repeated")
    c_parser.add_argument("--entity-type", help="Entity type for captured entities")

    # Doctor
    subparsers.add_parser("doctor", help="Check local project health")

    # Packs
    p_parser = subparsers.add_parser("pack", help="Manage knowledge packs")
    p_subs = p_parser.add_subparsers(dest="pack_command")
    p_subs.add_parser("active", help="Show active pack")
    p_subs.add_parser("list", help="List built-in packs")
    show_p = p_subs.add_parser("show", help="Show a pack")
    show_p.add_argument("pack_id")
    use_p = p_subs.add_parser("use", help="Activate a pack")
    use_p.add_argument("pack_id")
    validate_p = p_subs.add_parser("validate", help="Validate active pack or named pack")
    validate_p.add_argument("pack_id", nargs="?")
    p_subs.add_parser("detect", help="Detect active and suggested packs")
    p_subs.add_parser("suggest", help="Suggest a pack from project files")

    # Agents
    a_parser = subparsers.add_parser("agent", help="Run local CLI agents as KRAIL workers")
    a_subs = a_parser.add_subparsers(dest="agent_command")
    a_subs.add_parser("list", help="List configured local CLI agents")
    ar = a_subs.add_parser("run", help="Create and dispatch a one-off local agent task")
    ar.add_argument("prompt", help="Task prompt")
    ar.add_argument("--runner", default="codex_cli", choices=["codex_cli", "claude_code", "gemini_cli", "cursor_cli", "copilot_cli"])
    ar.add_argument("--role", default="research")
    ar.add_argument("--dry-run", action="store_true", help="Create session files and show command without launching")

    # Tasks
    task_parser = subparsers.add_parser("task", help="Manage repo-backed tasks and work orders")
    task_subs = task_parser.add_subparsers(dest="task_command")
    task_subs.add_parser("list", help="List local tasks")
    tc = task_subs.add_parser("create", help="Create a local task")
    tc.add_argument("title")
    tc.add_argument("--description", default="")
    tc.add_argument("--runner", default="codex_cli", choices=["codex_cli", "claude_code", "gemini_cli", "cursor_cli", "copilot_cli"])
    tc.add_argument("--workflow")
    tc.add_argument("--role", default="research")
    two = task_subs.add_parser("work-order", help="Create a work order for a task")
    two.add_argument("task_id")
    td = task_subs.add_parser("dispatch", help="Dispatch a task to a local CLI runner")
    td.add_argument("task_id")
    td.add_argument("--runner", choices=["codex_cli", "claude_code", "gemini_cli", "cursor_cli", "copilot_cli"])
    td.add_argument("--dry-run", action="store_true")

    # Workflows
    wf_parser = subparsers.add_parser("workflow", help="Run pack-defined workflow stubs")
    wf_subs = wf_parser.add_subparsers(dest="workflow_command")
    wf_subs.add_parser("list", help="List workflows from active pack")
    wr = wf_subs.add_parser("run", help="Create and dispatch a workflow task")
    wr.add_argument("workflow_id")
    wr.add_argument("--runner", default="codex_cli", choices=["codex_cli", "claude_code", "gemini_cli", "cursor_cli", "copilot_cli"])
    wr.add_argument("--dry-run", action="store_true")

    # Markdown graph
    graph_parser = subparsers.add_parser("graph", help="Build and query markdown-frontmatter graphs")
    graph_subs = graph_parser.add_subparsers(dest="graph_command")
    gb = graph_subs.add_parser("build", help="Build graph artifacts from markdown frontmatter")
    gb.add_argument("--no-write", action="store_true", help="Return the graph without writing artifacts")
    graph_subs.add_parser("validate", help="Validate markdown graph frontmatter")
    graph_subs.add_parser("check", help="Fail-style check for stale graph artifacts")
    ge = graph_subs.add_parser("entities", help="List markdown-derived entities")
    ge.add_argument("--type", help="Filter by entity type")
    ge.add_argument("--limit", type=int, default=100)
    gedge = graph_subs.add_parser("edges", help="List markdown-derived graph edges")
    gedge.add_argument("--entity", help="Filter to edges involving an entity label")
    gedge.add_argument("--type", help="Filter by relation type")
    gedge.add_argument("--limit", type=int, default=100)
    gd = graph_subs.add_parser("docs", help="List documents with graph frontmatter")
    gd.add_argument("--topic", help="Filter by topic")
    gd.add_argument("--kind", help="Filter by document kind")
    gd.add_argument("--source", help="Filter by source URL/text")
    gd.add_argument("--entity", help="Filter by entity label")
    gd.add_argument("--limit", type=int, default=100)
    gx = graph_subs.add_parser("export", help="Export the current graph as json, mermaid, or summary")
    gx.add_argument("--format", choices=["json", "mermaid", "summary"], default="json")
    gx.add_argument("--output", help="Write export content to a file instead of stdout")

    # Vector / RAG
    vector_parser = subparsers.add_parser("vector", help="Build and query the local SQLite vector database")
    vector_subs = vector_parser.add_subparsers(dest="vector_command")
    vb = vector_subs.add_parser("build", help="Build .krail/vector.sqlite from local project documents")
    vb.add_argument("--provider", choices=["local_hash", "openai", "sentence_transformers"], help="Embedding provider")
    vb.add_argument("--model", help="Embedding model name")
    vs = vector_subs.add_parser("search", help="Search local vector chunks")
    vs.add_argument("query")
    vs.add_argument("--limit", type=int, default=10)

    # CI templates
    ci_parser = subparsers.add_parser("ci", help="Generate local-preview CI templates")
    ci_subs = ci_parser.add_subparsers(dest="ci_command")
    ci_init = ci_subs.add_parser("init", help="Write a GitHub Actions KRAIL local-preview workflow")
    ci_init.add_argument("--path", dest="ci_path", default=".github/workflows/krail-local-preview.yml")

    # Query
    q_parser = subparsers.add_parser("query", help="Query the project knowledge graph")
    q_subs = q_parser.add_subparsers(dest="query_command")
    
    sql_p = q_subs.add_parser("sql", help="Run SQL query")
    sql_p.add_argument("sql", help="SQL statement")
    
    cls_p = q_subs.add_parser("classes", help="List classes")
    
    ent_p = q_subs.add_parser("entities", help="List entities of a class")
    ent_p.add_argument("class_name", help="Class name")
    ent_p.add_argument("--limit", type=int, default=20)

    # Hydrate
    h_parser = subparsers.add_parser("hydrate", help="Trigger hydration")
    h_parser.add_argument("--pipeline", help="Pipeline slug")
    h_parser.add_argument("--mode", choices=["markdown_graph", "markdown_frontmatter"], help="Run lightweight markdown graph hydration")

    # Reconcile
    subparsers.add_parser("reconcile", help="Reconcile repo-backed planner/session/control-plane state")

    # Series
    ts_parser = subparsers.add_parser("series", help="Fetch time-series data")
    ts_parser.add_argument("series_id", help="Series identifier")

    # Secrets
    sec_parser = subparsers.add_parser("secrets", help="Manage project secrets")
    sec_subs = sec_parser.add_subparsers(dest="command")
    sec_subs.add_parser("list", help="List secret keys")
    set_sec = sec_subs.add_parser("set", help="Set a secret")
    set_sec.add_argument("key", help="Secret name")
    set_sec.add_argument("value", help="Secret value")

    # Integrity
    i_parser = subparsers.add_parser("integrity", help="Research integrity tools")
    i_subs = i_parser.add_subparsers(dest="command")
    i_subs.add_parser("status", help="Show integrity status")
    i_subs.add_parser("assumptions", help="List assumptions")
    i_subs.add_parser("sources", help="List sources")
    i_subs.add_parser("claims", help="List claims")
    ir = i_subs.add_parser("rerun", help="Preview or apply rerun plan for an assumption")
    ir.add_argument("assumption_key", help="The assumption that changed")
    ir.add_argument("--apply", action="store_true", help="Actually create the rerun tasks")

    # Work Order
    wo_parser = subparsers.add_parser("work-order", help="Fetch the current work order")
    wo_parser.add_argument("--id", help="Work order ID (optional if RAIL_WORK_ORDER_ID set)")

    # Result
    res_parser = subparsers.add_parser("result", help="Submit a session result")
    res_parser.add_argument("--file", help="Path to session_result.json")
    res_parser.add_argument("--session", help="Session ID (optional if RAIL_SESSION_ID set)")

    # Ask
    ask_parser = subparsers.add_parser("ask", help="Ask a question")
    ask_parser.add_argument("question", help="The question to ask")
    ask_parser.add_argument("--session", help="Session ID (optional if RAIL_SESSION_ID set)")

    args = parser.parse_args()
    if args.version:
        _print_json({"name": "KRAIL", "version": rail.__version__})
        return
    if not args.command:
        parser.print_help()
        return

    if args.command == "init":
        cmd_init(args)
        return

    if args.command == "pack" and args.pack_command in {"list", "show"}:
        if args.pack_command == "list":
            _print_json({"packs": list(DEFAULT_PACKS.values())})
        else:
            pack = DEFAULT_PACKS.get(args.pack_id)
            if not pack:
                print(f"Error: unknown pack {args.pack_id}", file=sys.stderr)
                sys.exit(1)
            _print_json(pack)
        return

    if args.command == "pack" and args.pack_command == "validate" and getattr(args, "pack_id", None):
        _print_json(KnowledgeRuntime(".").validate_pack(args.pack_id))
        return

    project = _get_project(args)

    if args.command == "search":
        cmd_search(project, args)
    elif args.command == "think":
        cmd_think(project, args)
    elif args.command == "capture":
        cmd_capture(project, args)
    elif args.command == "doctor":
        cmd_doctor(project, args)
    elif args.command == "pack":
        cmd_pack(project, args)
    elif args.command == "agent":
        cmd_agent(project, args)
    elif args.command == "task":
        cmd_task(project, args)
    elif args.command == "workflow":
        cmd_workflow(project, args)
    elif args.command == "graph":
        cmd_graph(project, args)
    elif args.command == "vector":
        cmd_vector(project, args)
    elif args.command == "ci":
        cmd_ci(project, args)
    elif args.command == "query":
        cmd_query(project, args)
    elif args.command == "hydrate":
        cmd_hydrate(project, args)
    elif args.command == "series":
        cmd_series(project, args)
    elif args.command == "secrets":
        cmd_secrets(project, args)
    elif args.command == "integrity":
        cmd_integrity(project, args)
    elif args.command == "work-order":
        cmd_work_order(project, args)
    elif args.command == "result":
        cmd_result(project, args)
    elif args.command == "ask":
        cmd_ask(project, args)
    elif args.command == "reconcile":
        cmd_reconcile(project, args)

if __name__ == "__main__":
    main()
