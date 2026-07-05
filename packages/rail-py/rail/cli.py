import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import rail
from rail.bootstrap import bootstrap_future_project
from rail.knowledge import DEFAULT_PACKS, WORKFLOW_TEMPLATES, KnowledgeRuntime
from rail.modes import DEFAULT_MODES, get_mode

RUNNER_CHOICES = ["auto", "codex_cli", "claude_code", "gemini_cli", "cursor_cli", "copilot_cli"]

def _print_json(data: Any):
    print(json.dumps(data, indent=2, default=str))

def _parse_inputs(items: list[str] | None) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--input must be key=value: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        try:
            inputs[key] = json.loads(value)
        except json.JSONDecodeError:
            inputs[key] = value
    return inputs

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
    selected_mode = get_mode(args.knowledge_mode)
    selected_pack = args.pack or selected_mode.get("default_pack")
    root = bootstrap_future_project(target, name=name, slug=args.slug, mode=args.mode, knowledge_mode=args.knowledge_mode, pack=selected_pack)
    project = rail.local(str(root))
    materialized_workflows: list[str] = []
    if selected_pack:
        project.pack("use", selected_pack)
        if not args.no_init_pack_workflows and hasattr(project._backend, "knowledge"):
            active = project._backend.knowledge.active_pack().get("active") or {}
            for workflow_id in active.get("workflows") or []:
                if not isinstance(workflow_id, str):
                    continue
                result = project.init_workflow(workflow_id)
                if result.get("status") in {"written", "exists"}:
                    materialized_workflows.append(workflow_id)
    payload = {"status": "initialized", "path": str(root), "pack": selected_pack, "mode": args.mode, "knowledge_mode": selected_mode["id"]}
    if materialized_workflows:
        payload["materialized_workflows"] = materialized_workflows
    _print_json(payload)

def cmd_search(project: rail.Project, args: argparse.Namespace):
    if getattr(args, "federated", False):
        _print_json(project.federated_search(args.query, limit=args.limit, mounts=args.mount, explain=args.explain, rag=args.rag))
    elif hasattr(project._backend, "knowledge"):
        _print_json(project._backend.knowledge.search(args.query, limit=args.limit, explain=args.explain, rag=args.rag))
    else:
        _print_json(project.search(args.query))

def cmd_find(project: rail.Project, args: argparse.Namespace):
    if getattr(args, "federated", False):
        result = project.federated_find(
            args.query,
            limit=args.limit,
            mounts=args.mount,
            types=args.type,
            topic=args.topic,
            entity=args.entity,
            status=args.status,
            freshness=args.freshness,
            workflow=args.workflow,
            explain=args.explain,
            rag=not args.no_rag,
        )
    else:
        result = project.find(
            args.query,
            limit=args.limit,
            types=args.type,
            topic=args.topic,
            entity=args.entity,
            status=args.status,
            freshness=args.freshness,
            workflow=args.workflow,
            explain=args.explain,
            rag=not args.no_rag,
        )
    if args.paths:
        for item in result.get("results", []):
            path = item.get("path")
            if path:
                print(path)
        return
    if args.json:
        _print_json(result)
        return
    summary = result.get("summary", {})
    by_type = ", ".join(f"{kind}={count}" for kind, count in sorted((summary.get("by_type") or {}).items()))
    print(f"Found {summary.get('total', 0)} result(s)" + (f" ({by_type})" if by_type else ""))
    for item in result.get("results", []):
        label = item.get("type", "unknown")
        title = item.get("title") or item.get("id")
        path = item.get("path")
        score = item.get("score")
        status = f" status={item['status']}" if item.get("status") else ""
        print(f"[{label}] {title} score={score}{status}")
        if path:
            print(f"  {path}")
        snippet = (item.get("snippet") or "").strip()
        if snippet:
            print(f"  {snippet}")
    actions = result.get("suggested_actions") or []
    if actions:
        print("\nSuggested actions:")
        for action in actions:
            print(f"- {action}")

def cmd_think(project: rail.Project, args: argparse.Namespace):
    if getattr(args, "federated", False):
        result = project.federated_think(
            args.query,
            limit=args.limit,
            mounts=args.mount,
            mode=args.mode,
            runner=args.runner,
            dry_run=args.dry_run,
        )
    else:
        result = project.think(args.query, limit=args.limit, mode=args.mode, runner=args.runner, dry_run=args.dry_run)
    if args.output and not args.dry_run:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
        result["output_path"] = str(output)
        if args.register_integrity:
            result["integrity"] = project.register_think_result(
                result,
                artifact_path=str(output.resolve()),
                title=args.title or args.query,
            )
    _print_json(result)


def cmd_think_session(project: rail.Project, args: argparse.Namespace):
    if args.think_session_command == "list":
        _print_json(project.think_sessions(limit=args.limit))
    elif args.think_session_command == "status":
        _print_json(project.think_session(args.session_id))

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

def cmd_mode(project: rail.Project, args: argparse.Namespace):
    if args.mode_command == "list":
        _print_json(project.modes())
    elif args.mode_command == "active":
        _print_json(project.active_mode())
    elif args.mode_command == "show":
        _print_json(project.mode(args.mode_id))

def cmd_topic(project: rail.Project, args: argparse.Namespace):
    if args.topic_command == "list":
        _print_json(project.topic_list(include_inbox=args.include_inbox))
    elif args.topic_command == "upsert":
        content = args.content or ""
        if args.stdin:
            content = sys.stdin.read()
        _print_json(
            project.topic_upsert(
                args.topic,
                title=args.title,
                kind=args.type,
                content=content,
                source_path=args.source_path,
                sources=args.source,
                entities=args.entity,
                entity_type=args.entity_type,
            )
        )

def cmd_inbox(project: rail.Project, args: argparse.Namespace):
    if args.inbox_command == "list":
        _print_json(project.inbox_list(include_handled=args.include_handled))
    elif args.inbox_command == "promote":
        _print_json(
            project.inbox_promote(
                args.capture_path,
                topic=args.topic,
                title=args.title,
                kind=args.type,
                entities=args.entity,
                entity_type=args.entity_type,
            )
        )

def cmd_wiki(project: rail.Project, args: argparse.Namespace):
    source_paths = getattr(args, "source", None)
    if args.wiki_command == "plan":
        _print_json(project.wiki_plan(source_paths=source_paths, include_inbox=args.include_inbox))
    elif args.wiki_command == "build":
        _print_json(project.wiki_build(source_paths=source_paths, include_inbox=args.include_inbox, force=args.force))
    elif args.wiki_command == "list":
        _print_json(project.wiki_list())
    elif args.wiki_command == "check":
        result = project.wiki_check()
        _print_json(result)
        if not result.get("ok", False):
            sys.exit(1)
    elif args.wiki_command == "site":
        if args.wiki_site_command == "build":
            _print_json(project.wiki_site_build(force=args.force, title=args.title))
        elif args.wiki_site_command == "check":
            result = project.wiki_site_check()
            _print_json(result)
            if not result.get("ok", False):
                sys.exit(1)

def cmd_doctor(project: rail.Project, args: argparse.Namespace):
    _print_json(project.doctor())

def cmd_pack(project: rail.Project, args: argparse.Namespace):
    _print_json(project.pack(args.pack_command, getattr(args, "pack_id", None)))

def cmd_permissions(project: rail.Project, args: argparse.Namespace):
    if args.permissions_command == "doctor":
        _print_json(project.permissions_doctor())

def cmd_mount(project: rail.Project, args: argparse.Namespace):
    if args.mount_command == "list":
        _print_json(project.mount_list())

def cmd_grep(project: rail.Project, args: argparse.Namespace):
    result = project.grep(
        args.pattern,
        paths=args.path,
        glob=args.glob,
        ignore_case=args.ignore_case,
        fixed_strings=args.fixed_strings,
        word_regexp=args.word_regexp,
        max_count=args.max_count,
    )
    if args.json:
        _print_json(result)
        return
    for match in result.get("matches", []):
        print(f"{match['path']}:{match['line_number']}:{match['line']}")
    summary = result.get("summary", {})
    print(
        f"\n{summary.get('returned', 0)} match(es) across {summary.get('readable_files', 0)} readable file(s)"
        + (f", {summary.get('denied_files', 0)} denied" if summary.get("denied_files") else "")
    )

def cmd_files(project: rail.Project, args: argparse.Namespace):
    if args.files_command == "list":
        result = project.files_list(
            paths=args.path,
            glob=args.glob,
            recursive=args.recursive,
            include_hidden=args.hidden,
        )
        if args.json:
            _print_json(result)
            return
        for item in result.get("items", []):
            suffix = "/" if item.get("type") == "directory" else ""
            print(f"{item['path']}{suffix}")
        summary = result.get("summary", {})
        if summary.get("denied"):
            print(f"\n{summary['denied']} path(s) hidden by permissions")
    elif args.files_command == "read":
        result = project.files_read(args.path, start_line=args.start_line, lines=args.lines)
        if args.json:
            _print_json(result)
            return
        if result.get("status") == "blocked":
            _print_json(result)
            return
        sys.stdout.write(result.get("content", ""))
    elif args.files_command == "stat":
        _print_json(project.files_stat(args.path))

def cmd_agent(project: rail.Project, args: argparse.Namespace):
    if args.agent_command == "list":
        _print_json(project.agents())
    elif args.agent_command == "prompt":
        _print_json(project.agent_prompt(args.role, task=args.task or ""))
    elif args.agent_command == "scaffold-krail":
        _print_json(project.scaffold_krail_agents(force=args.force))
    elif args.agent_command == "doctor":
        prompt = args.prompt or "Audit this KRAIL project and repair platform health issues that are safe to fix."
        created = project.create_task(
            prompt,
            description=prompt,
            runner=args.runner,
            role="doctor",
        )
        _print_json(project.dispatch_task(created["task"]["id"], runner=args.runner, dry_run=args.dry_run))
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
        if getattr(args, "mount", None):
            _print_json(project.mount_list_tasks(args.mount))
        else:
            _print_json(project.list_tasks())
    elif args.task_command == "create":
        if getattr(args, "mount", None):
            _print_json(
                project.mount_create_task(
                    args.mount,
                    args.title,
                    description=args.description or args.title,
                    runner=args.runner,
                    workflow=args.workflow,
                    role=args.role,
                )
            )
        else:
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
        if getattr(args, "mount", None):
            _print_json(project.mount_dispatch_task(args.mount, args.task_id, runner=args.runner, dry_run=args.dry_run))
        else:
            _print_json(project.dispatch_task(args.task_id, runner=args.runner, dry_run=args.dry_run))

def cmd_workflow(project: rail.Project, args: argparse.Namespace):
    if args.workflow_command == "list":
        if getattr(args, "mount", None):
            _print_json(project.mount_list_workflows(args.mount))
        else:
            _print_json(project.list_workflows())
    elif args.workflow_command == "templates":
        _print_json(project.workflow_templates())
    elif args.workflow_command == "init":
        _print_json(project.init_workflow(args.workflow_id, force=args.force, template=args.template))
    elif args.workflow_command == "show":
        if getattr(args, "mount", None):
            _print_json(project.mount_show_workflow(args.mount, args.workflow_id))
        else:
            _print_json(project.show_workflow(args.workflow_id))
    elif args.workflow_command == "validate":
        result = project.validate_workflow(args.workflow_id)
        _print_json(result)
        if not result.get("ok", False):
            sys.exit(1)
    elif args.workflow_command == "run":
        if getattr(args, "mount", None):
            _print_json(project.mount_run_workflow(args.mount, args.workflow_id, runner=args.runner, dry_run=args.dry_run))
        else:
            _print_json(project.run_workflow(args.workflow_id, runner=args.runner, dry_run=args.dry_run))
    elif args.workflow_command == "execute":
        if getattr(args, "mount", None):
            _print_json(project.mount_execute_workflow(args.mount, args.workflow_id, dry_run=args.dry_run, force=args.force, inputs=_parse_inputs(args.input)))
        else:
            _print_json(project.execute_workflow(args.workflow_id, dry_run=args.dry_run, force=args.force, inputs=_parse_inputs(args.input)))
    elif args.workflow_command == "runs":
        _print_json(project.workflow_runs(limit=args.limit))
    elif args.workflow_command == "dashboard":
        _print_json(project.workflow_dashboard(limit=args.limit))
    elif args.workflow_command == "status":
        _print_json(project.workflow_status(args.run_id))
    elif args.workflow_command == "resume":
        _print_json(project.workflow_resume(args.run_id, force=args.force))

def cmd_approval(project: rail.Project, args: argparse.Namespace):
    if args.approval_command == "list":
        _print_json(project.approval_list(status=args.status))
    elif args.approval_command == "show":
        _print_json(project.approval_show(args.approval_id))
    elif args.approval_command in {"approve", "reject", "request-changes"}:
        decision = {
            "approve": "approved",
            "reject": "rejected",
            "request-changes": "changes_requested",
        }[args.approval_command]
        _print_json(project.approval_decide(args.approval_id, decision=decision, comment=args.comment or "", resume=args.resume))

def cmd_schedule(project: rail.Project, args: argparse.Namespace):
    if args.schedule_command == "install":
        _print_json(
            project.schedule_install(
                args.workflow_id,
                system=args.system,
                schedule=args.schedule,
                dry_run=args.dry_run,
            )
        )
    elif args.schedule_command == "list":
        _print_json(project.schedule_list())
    elif args.schedule_command == "remove":
        _print_json(project.schedule_remove(args.workflow_id))

def cmd_listener(project: rail.Project, args: argparse.Namespace):
    if args.listener_command == "list":
        _print_json(project.listener_list())
    elif args.listener_command == "templates":
        _print_json(project.listener_templates())
    elif args.listener_command == "init":
        _print_json(project.listener_init(args.template, listener_id=args.id, force=args.force))
    elif args.listener_command == "validate":
        result = project.listener_validate(args.listener_id)
        _print_json(result)
        if not result.get("ok", False):
            sys.exit(1)
    elif args.listener_command == "doctor":
        result = project.listener_doctor()
        _print_json(result)
        if not result.get("ok", False):
            sys.exit(1)
    elif args.listener_command == "show":
        _print_json(project.listener_show(args.listener_id))
    elif args.listener_command == "test":
        _print_json(project.listener_test(args.listener_id))
    elif args.listener_command == "poll":
        _print_json(project.listener_poll(args.listener_id, dry_run=args.dry_run, execute=not args.no_execute))
    elif args.listener_command == "daemon":
        _print_json(project.listener_daemon(once=args.once, interval_seconds=args.interval_seconds))
    elif args.listener_command == "serve":
        _print_json(project.listener_serve(host=args.host, port=args.port))

def cmd_event(project: rail.Project, args: argparse.Namespace):
    if args.event_command == "list":
        _print_json(project.event_list(limit=args.limit, listener_id=args.listener_id))
    elif args.event_command == "show":
        _print_json(project.event_show(args.event_id))
    elif args.event_command == "replay":
        _print_json(project.event_replay(args.event_id, dry_run=args.dry_run))

def cmd_queue(project: rail.Project, args: argparse.Namespace):
    if args.queue_command == "init":
        _print_json(project.queue_init(args.queue_id, source=args.source, key=args.key, force=args.force))
    elif args.queue_command == "status":
        _print_json(project.queue_status(args.queue_id))
    elif args.queue_command == "claim":
        _print_json(project.queue_claim(args.queue_id, limit=args.limit, where=args.where, owner=args.owner, lease_minutes=args.lease_minutes))
    elif args.queue_command in {"complete", "fail", "skip"}:
        status = {"complete": "done", "fail": "failed", "skip": "skipped"}[args.queue_command]
        _print_json(project.queue_update_batch(args.queue_id, args.batch_id, status=status))
    elif args.queue_command == "release":
        _print_json(project.queue_release(args.queue_id, stale=args.stale))

def cmd_graph(project: rail.Project, args: argparse.Namespace):
    if args.graph_command == "build":
        result = project.graph_build(write=not args.no_write)
        if args.quiet:
            return
        if args.json:
            _print_json(result)
        else:
            _print_json({"counts": result.get("counts", {}), "warnings": result.get("warnings", []), "written": result.get("written", [])})
    elif args.graph_command == "summary":
        if getattr(args, "federated", False):
            _print_json(project.federated_graph_summary(mounts=args.mount))
        else:
            _print_json(project.graph_summary())
    elif args.graph_command == "diff":
        _print_json(project.graph_diff())
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

def cmd_sources(project: rail.Project, args: argparse.Namespace):
    if args.sources_command == "validate":
        result = project.sources_validate()
        _print_json(result)
        if not result.get("ok", False):
            sys.exit(1)
    elif args.sources_command == "list":
        _print_json(project.sources_list())
    elif args.sources_command == "check":
        _print_json(project.sources_check(write=not args.no_write))
    elif args.sources_command == "changed":
        _print_json(project.sources_changed())
    elif args.sources_command == "affected":
        _print_json(project.sources_affected(source_ids=args.source_id))

def cmd_evidence(project: rail.Project, args: argparse.Namespace):
    if args.evidence_command == "candidates":
        _print_json({"sources": project.integrity_source_candidates(), "claims": project.integrity_claim_candidates()})
    elif args.evidence_command == "promote-source":
        _print_json(project.apply_integrity_source_candidate_promotion(args.candidate_key, source_type=args.source_type, source_key=args.source_key))
    elif args.evidence_command == "promote-claim":
        _print_json(project.apply_integrity_claim_candidate_promotion(args.candidate_key, claim_key=args.claim_key, status=args.status, artifact_path=args.artifact_path))

def cmd_repo(project: rail.Project, args: argparse.Namespace):
    if args.repo_command == "inspect":
        _print_json(project.repo_inspect(args.path_or_url))
    elif args.repo_command == "snapshot":
        _print_json(project.repo_snapshot(args.path_or_url))
    elif args.repo_command == "inventory":
        _print_json(project.repo_inventory(args.path_or_url))
    elif args.repo_command == "owners":
        _print_json(project.repo_owners(args.path_or_url))
    elif args.repo_command == "dependencies":
        _print_json(project.repo_dependencies(args.path_or_url))
    elif args.repo_command == "symbols":
        _print_json(project.repo_symbols(args.path_or_url, languages=args.language))
    elif args.repo_command == "changed":
        _print_json(project.repo_changed(args.path_or_url, base_ref=args.base_ref))

def cmd_ci(project: rail.Project, args: argparse.Namespace):
    if args.ci_command == "init":
        _print_json(project.ci_init(path=args.ci_path))

def cmd_query(project: rail.Project, args: argparse.Namespace):
    query_command = getattr(args, "query_command", None)
    if query_command == "sql":
        _print_json(project.query(args.sql).to_dict(orient="records"))
    elif query_command == "classes":
        _print_json(project.classes())
    elif query_command == "entities":
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
    elif command == "claim-candidates":
        _print_json(project.integrity_claim_candidates())
    elif command == "artifacts":
        _print_json(project.integrity_artifact_lineage())
    elif command == "promote-source-candidate":
        _print_json(
            project.apply_integrity_source_candidate_promotion(
                args.candidate_key,
                source_key=getattr(args, "source_key", None),
                source_type=getattr(args, "source_type", None),
            )
        )
    elif command == "promote-claim-candidate":
        _print_json(
            project.apply_integrity_claim_candidate_promotion(
                args.candidate_key,
                claim_key=getattr(args, "claim_key", None),
                status=getattr(args, "status", None),
                artifact_path=getattr(args, "artifact_path", None),
            )
        )
    elif command == "resolve-conflict":
        _print_json(
            project.apply_integrity_conflict_resolution(
                args.conflict_key,
                status=args.status,
                favored_claim_key=getattr(args, "favored_claim_key", None),
                explanation=getattr(args, "explanation", None),
            )
        )
    elif command == "source":
        _print_json(project.integrity_source_detail(args.source_key))
    elif command == "claim":
        _print_json(project.integrity_claim_detail(args.claim_key))
    elif command == "artifact":
        _print_json(project.integrity_artifact_detail(args.artifact_path))
    elif command == "verification-runs":
        _print_json(project.integrity_verification_runs())
    elif command == "benchmark":
        _print_json(project.integrity_benchmark(retrieval_limit=args.limit))
    elif command == "compile":
        _print_json(project.integrity_compile(write_files=not args.no_write, alignment_paths=args.alignment_path))
    elif command == "reproduce":
        outputs = json.loads(Path(args.outputs_json).read_text(encoding="utf-8"))
        _print_json(project.apply_integrity_reproducibility_rerun(outputs, run_id=args.run_id, scope=args.scope))
    elif command == "freshness-evaluate":
        _print_json(project.apply_integrity_freshness_evaluation(as_of=args.as_of))
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
    init_parser.add_argument("--knowledge-mode", choices=sorted(DEFAULT_MODES), default="research", help="Knowledge operating mode")
    init_parser.add_argument(
        "--no-init-pack-workflows",
        action="store_true",
        help="Skip materializing workflow specs for the selected pack during init",
    )

    # Search
    s_parser = subparsers.add_parser("search", help="Search local project evidence")
    s_parser.add_argument("query", help="Search query")
    s_parser.add_argument("--limit", type=int, default=10)
    s_parser.add_argument("--explain", action="store_true", help="Explain local ranking signals")
    search_rag = s_parser.add_mutually_exclusive_group()
    search_rag.add_argument("--rag", dest="rag", action="store_true", help="Use deterministic hybrid retrieval with local vector signals (default)")
    search_rag.add_argument("--no-rag", dest="rag", action="store_false", help="Disable local vector retrieval and use lexical plus graph ranking only")
    s_parser.set_defaults(rag=True)
    s_parser.add_argument("--federated", action="store_true", help="Search the local project and configured mounted child projects")
    s_parser.add_argument("--mount", action="append", help="Limit federated search to a specific mount id; repeatable")

    find_parser = subparsers.add_parser("find", help="Find typed knowledge records across docs, graph, integrity, sessions, queues, and artifacts")
    find_parser.add_argument("query", help="Find query")
    find_parser.add_argument("--limit", type=int, default=10)
    find_parser.add_argument("--type", action="append", help="Filter by result type; repeat for multiple types")
    find_parser.add_argument("--topic", help="Filter results containing a topic")
    find_parser.add_argument("--entity", help="Filter results containing an entity")
    find_parser.add_argument("--status", help="Filter results containing a status")
    find_parser.add_argument("--freshness", help="Filter results containing a freshness value")
    find_parser.add_argument("--workflow", help="Filter results containing a workflow")
    find_parser.add_argument("--no-rag", action="store_true", help="Skip local vector retrieval")
    find_parser.add_argument("--explain", action="store_true", help="Explain searched surfaces and ranking signals")
    find_parser.add_argument("--json", action="store_true", help="Print the full JSON envelope")
    find_parser.add_argument("--paths", action="store_true", help="Print matching paths only")
    find_parser.add_argument("--federated", action="store_true", help="Search the local project and configured mounted child projects")
    find_parser.add_argument("--mount", action="append", help="Limit federated find to a specific mount id; repeatable")

    perm_parser = subparsers.add_parser("permissions", help="Inspect local permission metadata and access policy")
    perm_subs = perm_parser.add_subparsers(dest="permissions_command")
    perm_subs.add_parser("doctor", help="Check permission metadata and audit configuration")

    mount_parser = subparsers.add_parser("mount", help="Inspect configured mounted child projects")
    mount_subs = mount_parser.add_subparsers(dest="mount_command")
    mount_subs.add_parser("list", help="List configured mounts and their health")

    grep_parser = subparsers.add_parser("grep", help="Permission-aware grep across readable project files")
    grep_parser.add_argument("pattern", help="Regex pattern to search")
    grep_parser.add_argument("path", nargs="*", help="Optional repo-relative file or directory roots")
    grep_parser.add_argument("--glob", action="append", help="Limit searched files to glob patterns")
    grep_parser.add_argument("--ignore-case", action="store_true", help="Ignore case distinctions")
    grep_parser.add_argument("--fixed-strings", action="store_true", help="Treat pattern as a literal string")
    grep_parser.add_argument("--word-regexp", action="store_true", help="Match whole words only")
    grep_parser.add_argument("--max-count", type=int, help="Stop after this many matching lines")
    grep_parser.add_argument("--json", action="store_true", help="Print the full JSON envelope")

    files_parser = subparsers.add_parser("files", help="Permission-aware file inspection tools")
    files_subs = files_parser.add_subparsers(dest="files_command")
    files_list = files_subs.add_parser("list", help="List readable files and directories")
    files_list.add_argument("path", nargs="*", help="Optional repo-relative file or directory roots")
    files_list.add_argument("--glob", action="append", help="Filter returned paths by glob")
    files_list.add_argument("--recursive", action="store_true", help="Walk directories recursively")
    files_list.add_argument("--hidden", action="store_true", help="Include hidden dot-paths")
    files_list.add_argument("--json", action="store_true", help="Print the full JSON envelope")
    files_read = files_subs.add_parser("read", help="Read a permitted file")
    files_read.add_argument("path", help="Repo-relative file path")
    files_read.add_argument("--start-line", type=int, default=1, help="1-based starting line")
    files_read.add_argument("--lines", type=int, help="Maximum number of lines to print")
    files_read.add_argument("--json", action="store_true", help="Print the full JSON envelope")
    files_stat = files_subs.add_parser("stat", help="Show metadata for a permitted path")
    files_stat.add_argument("path", help="Repo-relative file or directory path")

    # Think
    t_parser = subparsers.add_parser("think", help="Synthesize from local evidence with gaps/conflicts")
    t_parser.add_argument("query", help="Question to answer")
    t_parser.add_argument("--limit", type=int, default=5)
    t_parser.add_argument("--mode", choices=["deterministic", "runner", "hybrid"], default="deterministic")
    t_parser.add_argument("--runner", default="auto", choices=RUNNER_CHOICES)
    t_parser.add_argument("--dry-run", action="store_true", help="Prepare runner-backed think session files without launching a local runner")
    t_parser.add_argument("--output", help="Write the think result envelope to a JSON file")
    t_parser.add_argument("--register-integrity", action="store_true", help="Register the written think output as an integrity artifact with claim candidates")
    t_parser.add_argument("--title", help="Optional artifact title when registering a think result")
    t_parser.add_argument("--federated", action="store_true", help="Use the local project plus configured mounted child projects for retrieval")
    t_parser.add_argument("--mount", action="append", help="Limit federated think to a specific mount id; repeatable")

    ts_parser = subparsers.add_parser("think-session", help="Inspect runner-backed think sessions")
    ts_subs = ts_parser.add_subparsers(dest="think_session_command")
    tsl = ts_subs.add_parser("list", help="List think sessions")
    tsl.add_argument("--limit", type=int, default=20)
    tss = ts_subs.add_parser("status", help="Show a think session")
    tss.add_argument("session_id", help="Think session id")

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

    # Knowledge modes
    mode_parser = subparsers.add_parser("mode", help="Inspect KRAIL knowledge modes")
    mode_subs = mode_parser.add_subparsers(dest="mode_command")
    mode_subs.add_parser("list", help="List built-in knowledge modes")
    mode_subs.add_parser("active", help="Show the active knowledge mode")
    mode_show = mode_subs.add_parser("show", help="Show a built-in knowledge mode")
    mode_show.add_argument("mode_id", help="Mode id, e.g. research, company, personal, software, project")

    # Topics
    topic_parser = subparsers.add_parser("topic", help="Manage durable topic pages")
    topic_subs = topic_parser.add_subparsers(dest="topic_command")
    topic_list = topic_subs.add_parser("list", help="List topic pages")
    topic_list.add_argument("--include-inbox", action="store_true", help="Include topics/inbox captures")
    topic_upsert = topic_subs.add_parser("upsert", help="Create or update a durable topic page")
    topic_upsert.add_argument("topic", help="Topic slug or title")
    topic_upsert.add_argument("--title", help="Topic page title")
    topic_upsert.add_argument("--type", default="topic", help="Topic type, e.g. paper, system, project")
    topic_upsert.add_argument("--content", help="Content to append to the topic")
    topic_upsert.add_argument("--stdin", action="store_true", help="Read content to append from stdin")
    topic_upsert.add_argument("--source-path", help="Repo-relative source capture path")
    topic_upsert.add_argument("--source", action="append", help="Source URL or path; can be repeated")
    topic_upsert.add_argument("--entity", action="append", help="Entity to attach; can be repeated")
    topic_upsert.add_argument("--entity-type", help="Entity type for attached entities")

    # Inbox
    inbox_parser = subparsers.add_parser("inbox", help="Triage captured notes from topics/inbox")
    inbox_subs = inbox_parser.add_subparsers(dest="inbox_command")
    inbox_list = inbox_subs.add_parser("list", help="List unhandled inbox captures")
    inbox_list.add_argument("--include-handled", action="store_true", help="Include promoted or archived captures")
    inbox_promote = inbox_subs.add_parser("promote", help="Promote an inbox capture into a stable topic page")
    inbox_promote.add_argument("capture_path", help="Repo-relative capture path")
    inbox_promote.add_argument("--topic", required=True, help="Target topic slug or title")
    inbox_promote.add_argument("--title", help="Target topic title")
    inbox_promote.add_argument("--type", default="topic", help="Target topic type")
    inbox_promote.add_argument("--entity", action="append", help="Entity to attach; can be repeated")
    inbox_promote.add_argument("--entity-type", help="Entity type for attached entities")

    # Wiki
    wiki_parser = subparsers.add_parser("wiki", help="Generate and inspect reader wiki pages")
    wiki_subs = wiki_parser.add_subparsers(dest="wiki_command")
    wiki_plan = wiki_subs.add_parser("plan", help="Preview wiki pages that would be generated")
    wiki_plan.add_argument("--source", action="append", help="Repo-relative markdown source path; can be repeated")
    wiki_plan.add_argument("--include-inbox", action="store_true", help="Include topics/inbox captures")
    wiki_build = wiki_subs.add_parser("build", help="Generate wiki pages under docs/wiki")
    wiki_build.add_argument("--source", action="append", help="Repo-relative markdown source path; can be repeated")
    wiki_build.add_argument("--include-inbox", action="store_true", help="Include topics/inbox captures")
    wiki_build.add_argument("--force", action="store_true", help="Overwrite existing generated wiki pages")
    wiki_subs.add_parser("list", help="List generated wiki pages")
    wiki_subs.add_parser("check", help="Validate generated wiki pages")
    wiki_site = wiki_subs.add_parser("site", help="Build and inspect the static wiki app")
    wiki_site_subs = wiki_site.add_subparsers(dest="wiki_site_command")
    wiki_site_build = wiki_site_subs.add_parser("build", help="Build a GitHub Pages-ready static wiki app")
    wiki_site_build.add_argument("--force", action="store_true", help="Overwrite docs/wiki-site")
    wiki_site_build.add_argument("--title", help="Override the browser title and app heading")
    wiki_site_subs.add_parser("check", help="Validate the static wiki app")

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
    ap = a_subs.add_parser("prompt", help="Render a project role prompt")
    ap.add_argument("role", help="Role name, e.g. doctor, platform, research, coding")
    ap.add_argument("--task", help="Optional task text to include in the rendered prompt")
    aks = a_subs.add_parser("scaffold-krail", help="Write KRAIL doctor/platform prompts and skills")
    aks.add_argument("--force", action="store_true", help="Overwrite existing KRAIL prompt files")
    ad = a_subs.add_parser("doctor", help="Create and dispatch a KRAIL doctor agent task")
    ad.add_argument("--prompt", help="Override the default doctor task prompt")
    ad.add_argument("--runner", default="auto", choices=RUNNER_CHOICES)
    ad.add_argument("--dry-run", action="store_true", help="Create session files and show command without launching")
    ar = a_subs.add_parser("run", help="Create and dispatch a one-off local agent task")
    ar.add_argument("prompt", help="Task prompt")
    ar.add_argument("--runner", default="auto", choices=RUNNER_CHOICES)
    ar.add_argument("--role", default="research")
    ar.add_argument("--dry-run", action="store_true", help="Create session files and show command without launching")

    # Tasks
    task_parser = subparsers.add_parser("task", help="Manage repo-backed tasks and work orders")
    task_subs = task_parser.add_subparsers(dest="task_command")
    tl = task_subs.add_parser("list", help="List local tasks")
    tl.add_argument("--mount", help="Target a mounted child project instead of the local project")
    tc = task_subs.add_parser("create", help="Create a local task")
    tc.add_argument("title")
    tc.add_argument("--description", default="")
    tc.add_argument("--runner", default="auto", choices=RUNNER_CHOICES)
    tc.add_argument("--workflow")
    tc.add_argument("--role", default="research")
    tc.add_argument("--mount", help="Create the task inside a mounted child project")
    two = task_subs.add_parser("work-order", help="Create a work order for a task")
    two.add_argument("task_id")
    td = task_subs.add_parser("dispatch", help="Dispatch a task to a local CLI runner")
    td.add_argument("task_id")
    td.add_argument("--runner", choices=RUNNER_CHOICES)
    td.add_argument("--dry-run", action="store_true")
    td.add_argument("--mount", help="Dispatch a task inside a mounted child project")

    # Workflows
    wf_parser = subparsers.add_parser("workflow", help="Run pack-defined workflow stubs")
    wf_subs = wf_parser.add_subparsers(dest="workflow_command")
    wl = wf_subs.add_parser("list", help="List workflows from active pack")
    wl.add_argument("--mount", help="List workflows from a mounted child project")
    wf_subs.add_parser("templates", help="List built-in workflow templates")
    wi = wf_subs.add_parser("init", help="Create a local workflow spec under research_plan/workflows")
    wi.add_argument("workflow_id")
    wi.add_argument("--template", choices=sorted(WORKFLOW_TEMPLATES))
    wi.add_argument("--force", action="store_true")
    ws = wf_subs.add_parser("show", help="Show a local workflow spec")
    ws.add_argument("workflow_id")
    ws.add_argument("--mount", help="Show the workflow from a mounted child project")
    wv = wf_subs.add_parser("validate", help="Validate a local workflow spec")
    wv.add_argument("workflow_id")
    wr = wf_subs.add_parser("run", help="Create and dispatch a workflow task")
    wr.add_argument("workflow_id")
    wr.add_argument("--runner", default="auto", choices=RUNNER_CHOICES)
    wr.add_argument("--dry-run", action="store_true")
    wr.add_argument("--mount", help="Run the workflow in a mounted child project")
    wx = wf_subs.add_parser("execute", help="Execute a local workflow spec")
    wx.add_argument("workflow_id")
    wx.add_argument("--dry-run", action="store_true")
    wx.add_argument("--force", action="store_true", help="Bypass workflow lock")
    wx.add_argument("--input", action="append", help="Workflow input as key=value; JSON values are accepted")
    wx.add_argument("--mount", help="Execute the workflow in a mounted child project")
    wrl = wf_subs.add_parser("runs", help="List local workflow runs")
    wrl.add_argument("--limit", type=int, default=20)
    wdash = wf_subs.add_parser("dashboard", help="Summarize workflow and agent session status")
    wdash.add_argument("--limit", type=int, default=50)
    wst = wf_subs.add_parser("status", help="Show a local workflow run result")
    wst.add_argument("run_id")
    wre = wf_subs.add_parser("resume", help="Resume a workflow run paused on an approval")
    wre.add_argument("run_id")
    wre.add_argument("--force", action="store_true", help="Bypass workflow lock")

    # Approvals
    approval_parser = subparsers.add_parser("approval", help="Review repo-backed workflow approvals")
    approval_subs = approval_parser.add_subparsers(dest="approval_command")
    al = approval_subs.add_parser("list", help="List approvals")
    al.add_argument("--status", help="Filter by status, e.g. pending")
    ash = approval_subs.add_parser("show", help="Show an approval")
    ash.add_argument("approval_id")
    aa = approval_subs.add_parser("approve", help="Approve an approval request")
    aa.add_argument("approval_id")
    aa.add_argument("--comment", default="")
    aa.add_argument("--resume", action="store_true")
    arj = approval_subs.add_parser("reject", help="Reject an approval request")
    arj.add_argument("approval_id")
    arj.add_argument("--comment", default="")
    arj.add_argument("--resume", action="store_true")
    arc = approval_subs.add_parser("request-changes", help="Request changes for an approval")
    arc.add_argument("approval_id")
    arc.add_argument("--comment", default="")
    arc.add_argument("--resume", action="store_true")

    # Schedules
    schedule_parser = subparsers.add_parser("schedule", help="Generate local scheduler wrappers for workflows")
    schedule_subs = schedule_parser.add_subparsers(dest="schedule_command")
    si = schedule_subs.add_parser("install", help="Write a scheduler wrapper and install hint")
    si.add_argument("workflow_id")
    si.add_argument("--system", choices=["cron", "launchd"], default="cron")
    si.add_argument("--schedule", help="Cron expression; launchd currently writes a daily 8am plist")
    si.add_argument("--dry-run", action="store_true", help="Schedule workflow dry-runs instead of full execution")
    schedule_subs.add_parser("list", help="List generated scheduler wrappers")
    sr = schedule_subs.add_parser("remove", help="Remove generated scheduler wrapper files")
    sr.add_argument("workflow_id")

    # Listeners and events
    listener_parser = subparsers.add_parser("listener", help="Poll local event listeners and trigger workflows")
    listener_subs = listener_parser.add_subparsers(dest="listener_command")
    listener_subs.add_parser("list", help="List listener specs under research_plan/listeners")
    listener_subs.add_parser("templates", help="List built-in listener templates")
    linit = listener_subs.add_parser("init", help="Create a listener spec from a template or type")
    linit.add_argument("template", help="Template name or listener type")
    linit.add_argument("--id", help="Override listener id")
    linit.add_argument("--force", action="store_true")
    lval = listener_subs.add_parser("validate", help="Validate one listener or all listeners")
    lval.add_argument("listener_id", nargs="?")
    listener_subs.add_parser("doctor", help="Diagnose listener health, state, and events")
    lshow = listener_subs.add_parser("show", help="Show a listener spec")
    lshow.add_argument("listener_id")
    ltest = listener_subs.add_parser("test", help="Observe a listener without writing events or state")
    ltest.add_argument("listener_id")
    lpoll = listener_subs.add_parser("poll", help="Poll one listener or all listeners")
    lpoll.add_argument("listener_id", nargs="?")
    lpoll.add_argument("--all", action="store_true", help="Poll all enabled listeners")
    lpoll.add_argument("--dry-run", action="store_true", help="Preview events without writing state or running workflows")
    lpoll.add_argument("--no-execute", action="store_true", help="Record events without invoking workflow triggers")
    ldaemon = listener_subs.add_parser("daemon", help="Continuously poll all enabled listeners")
    ldaemon.add_argument("--once", action="store_true", help="Poll all listeners once and exit")
    ldaemon.add_argument("--interval-seconds", type=int, default=30, help="Sleep interval between polling passes")
    lserve = listener_subs.add_parser("serve", help="Run a local webhook receiver that records events")
    lserve.add_argument("--host", default="127.0.0.1")
    lserve.add_argument("--port", type=int, default=8787)

    event_parser = subparsers.add_parser("event", help="Inspect and replay listener events")
    event_subs = event_parser.add_subparsers(dest="event_command")
    elist = event_subs.add_parser("list", help="List recorded listener events")
    elist.add_argument("--limit", type=int, default=20)
    elist.add_argument("--listener-id", help="Filter by listener id")
    eshow = event_subs.add_parser("show", help="Show a recorded listener event")
    eshow.add_argument("event_id")
    ereplay = event_subs.add_parser("replay", help="Replay an event's workflow trigger")
    ereplay.add_argument("event_id")
    ereplay.add_argument("--dry-run", action="store_true")

    queue_parser = subparsers.add_parser("queue", help="Manage repo-backed batch queues over inventories")
    queue_subs = queue_parser.add_subparsers(dest="queue_command")
    qinit = queue_subs.add_parser("init", help="Create a queue from CSV, JSON, or JSONL inventory")
    qinit.add_argument("queue_id")
    qinit.add_argument("--source", required=True)
    qinit.add_argument("--key", required=True)
    qinit.add_argument("--force", action="store_true")
    qstatus = queue_subs.add_parser("status", help="Show queue counts and recent claims")
    qstatus.add_argument("queue_id")
    qclaim = queue_subs.add_parser("claim", help="Reserve a batch of queue items")
    qclaim.add_argument("queue_id")
    qclaim.add_argument("--limit", type=int, default=10)
    qclaim.add_argument("--where", action="append", help="Filter as key=value; can be repeated")
    qclaim.add_argument("--owner")
    qclaim.add_argument("--lease-minutes", type=int, default=120)
    for qcmd in ["complete", "fail", "skip"]:
        qp = queue_subs.add_parser(qcmd, help=f"Mark a claimed batch {qcmd}")
        qp.add_argument("queue_id")
        qp.add_argument("batch_id")
    qrel = queue_subs.add_parser("release", help="Release reserved/running queue items back to pending")
    qrel.add_argument("queue_id")
    qrel.add_argument("--stale", action="store_true", help="Only release expired leases")

    # Markdown graph
    graph_parser = subparsers.add_parser("graph", help="Build and query markdown-frontmatter graphs")
    graph_subs = graph_parser.add_subparsers(dest="graph_command")
    gb = graph_subs.add_parser("build", help="Build graph artifacts from markdown frontmatter")
    gb.add_argument("--no-write", action="store_true", help="Return the graph without writing artifacts")
    gb.add_argument("--quiet", action="store_true", help="Build without printing the graph JSON")
    gb.add_argument("--summary", action="store_true", help="Print only counts, warnings, and written paths")
    gb.add_argument("--json", action="store_true", help="Print the full graph JSON")
    gsummary = graph_subs.add_parser("summary", help="Print graph counts and warnings without dumping graph JSON")
    gsummary.add_argument("--federated", action="store_true", help="Include configured mounted child projects")
    gsummary.add_argument("--mount", action="append", help="Limit federated graph summary to a specific mount id; repeatable")
    graph_subs.add_parser("diff", help="Compare current markdown graph to the saved graph artifact")
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

    # Source dependencies
    sources_parser = subparsers.add_parser("sources", help="Manage source dependencies and freshness snapshots")
    sources_subs = sources_parser.add_subparsers(dest="sources_command")
    sources_subs.add_parser("validate", help="Validate sources/dependencies.yaml")
    sources_subs.add_parser("list", help="List dependency sources and affected documents")
    sc = sources_subs.add_parser("check", help="Snapshot dependency sources and detect changes")
    sc.add_argument("--no-write", action="store_true", help="Do not update research_plan/state/source_snapshots.json")
    sources_subs.add_parser("changed", help="List sources marked changed by the last check")
    sa = sources_subs.add_parser("affected", help="List documents affected by changed sources")
    sa.add_argument("--source-id", action="append", help="Limit affected-doc lookup to a source id; can be repeated")

    evidence_parser = subparsers.add_parser("evidence", help="Review and promote candidate evidence")
    evidence_subs = evidence_parser.add_subparsers(dest="evidence_command")
    evidence_subs.add_parser("candidates", help="List source and claim candidates")
    eps = evidence_subs.add_parser("promote-source", help="Promote a source candidate")
    eps.add_argument("candidate_key")
    eps.add_argument("--source-key")
    eps.add_argument("--source-type", default="dataset")
    epc = evidence_subs.add_parser("promote-claim", help="Promote a claim candidate")
    epc.add_argument("candidate_key")
    epc.add_argument("--claim-key")
    epc.add_argument("--status", default="needs_evidence")
    epc.add_argument("--artifact-path")

    repo_parser = subparsers.add_parser("repo", help="Inspect repositories for ingestion evidence")
    repo_subs = repo_parser.add_subparsers(dest="repo_command")
    rinspect = repo_subs.add_parser("inspect", help="Inspect a local repository path")
    rinspect.add_argument("path_or_url")
    rsnapshot = repo_subs.add_parser("snapshot", help="Capture repo git/head/dirty metadata for a local repository path")
    rsnapshot.add_argument("path_or_url", nargs="?", default=".")
    rinventory = repo_subs.add_parser("inventory", help="Build a structural inventory for a local repository path")
    rinventory.add_argument("path_or_url", nargs="?", default=".")
    rowners = repo_subs.add_parser("owners", help="Parse CODEOWNERS metadata for a local repository path")
    rowners.add_argument("path_or_url", nargs="?", default=".")
    rdeps = repo_subs.add_parser("dependencies", help="Extract dependency manifests from a local repository path")
    rdeps.add_argument("path_or_url", nargs="?", default=".")
    rsymbols = repo_subs.add_parser("symbols", help="Extract lightweight symbol inventories for a local repository path")
    rsymbols.add_argument("path_or_url", nargs="?", default=".")
    rsymbols.add_argument("--language", action="append", help="Limit extraction to a language such as python, typescript, or javascript")
    rchanged = repo_subs.add_parser("changed", help="Summarize working tree and recent git changes for a local repository path")
    rchanged.add_argument("path_or_url", nargs="?", default=".")
    rchanged.add_argument("--base-ref", help="Optional git base ref to diff against, for example origin/main")

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
    i_subs = i_parser.add_subparsers(dest="integrity_command")
    i_subs.add_parser("status", help="Show integrity status")
    i_subs.add_parser("assumptions", help="List assumptions")
    i_subs.add_parser("sources", help="List sources")
    i_subs.add_parser("claims", help="List claims")
    i_subs.add_parser("claim-candidates", help="List claim candidates")
    i_subs.add_parser("artifacts", help="List artifact lineage records")
    ips = i_subs.add_parser("promote-source-candidate", help="Promote a source candidate into a canonical source")
    ips.add_argument("candidate_key", help="Source candidate key")
    ips.add_argument("--source-key", help="Canonical source key override")
    ips.add_argument("--source-type", default="dataset", help="Canonical source type")
    ipc = i_subs.add_parser("promote-claim-candidate", help="Promote a claim candidate into a canonical claim")
    ipc.add_argument("candidate_key", help="Claim candidate key")
    ipc.add_argument("--claim-key", help="Canonical claim key override")
    ipc.add_argument("--status", default="needs_evidence", help="Target canonical claim status")
    ipc.add_argument("--artifact-path", help="Artifact path to associate with the claim")
    irc = i_subs.add_parser("resolve-conflict", help="Resolve a recorded claim conflict")
    irc.add_argument("conflict_key", help="Conflict key")
    irc.add_argument("--status", default="resolved", help="Resolution status")
    irc.add_argument("--favored-claim-key", help="Favored claim key")
    irc.add_argument("--explanation", help="Resolution explanation")
    isrc = i_subs.add_parser("source", help="Show source integrity detail")
    isrc.add_argument("source_key", help="Source key")
    iclaim = i_subs.add_parser("claim", help="Show claim integrity detail")
    iclaim.add_argument("claim_key", help="Claim key")
    iartifact = i_subs.add_parser("artifact", help="Show artifact integrity detail")
    iartifact.add_argument("artifact_path", help="Artifact path")
    i_subs.add_parser("verification-runs", help="List verification runs")
    i_subs.add_parser("stale-graph", help="Show stale dependency graph")
    i_subs.add_parser("graph", help="Show integrity dependency graph")
    iret = i_subs.add_parser("retrieve", help="Retrieve integrity records")
    iret.add_argument("query_text", help="Retrieval query")
    iret.add_argument("--limit", type=int, default=10)
    iret.add_argument("--artifact-types", action="append")
    iret.add_argument("--claim-statuses", action="append")
    iret.add_argument("--source-freshness", action="append")
    iret.add_argument("--date-from")
    iret.add_argument("--date-to")
    iret.add_argument("--include-stale", action="store_true")
    iret.add_argument("--include-blocked", action="store_true")
    ipromote = i_subs.add_parser("promote", help="Promote an artifact to a target trust state")
    ipromote.add_argument("artifact_path", help="Artifact path")
    ipromote.add_argument("--target-state", default="verified", help="Target promotion state")
    ibench = i_subs.add_parser("benchmark", help="Run integrity retrieval benchmark")
    ibench.add_argument("--limit", type=int, default=10)
    icompile = i_subs.add_parser("compile", help="Compile integrity truth report")
    icompile.add_argument("--no-write", action="store_true")
    icompile.add_argument("--alignment-path", action="append")
    irepro = i_subs.add_parser("reproduce", help="Apply reproducibility rerun results")
    irepro.add_argument("--outputs-json", required=True)
    irepro.add_argument("--run-id", default="rerun-verification")
    irepro.add_argument("--scope", default="health")
    ifresh = i_subs.add_parser("freshness-evaluate", help="Evaluate source freshness")
    ifresh.add_argument("--as-of")
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
    elif args.command == "find":
        cmd_find(project, args)
    elif args.command == "think":
        cmd_think(project, args)
    elif args.command == "think-session":
        cmd_think_session(project, args)
    elif args.command == "capture":
        cmd_capture(project, args)
    elif args.command == "mode":
        cmd_mode(project, args)
    elif args.command == "topic":
        cmd_topic(project, args)
    elif args.command == "inbox":
        cmd_inbox(project, args)
    elif args.command == "wiki":
        cmd_wiki(project, args)
    elif args.command == "doctor":
        cmd_doctor(project, args)
    elif args.command == "pack":
        cmd_pack(project, args)
    elif args.command == "permissions":
        cmd_permissions(project, args)
    elif args.command == "mount":
        cmd_mount(project, args)
    elif args.command == "grep":
        cmd_grep(project, args)
    elif args.command == "files":
        cmd_files(project, args)
    elif args.command == "agent":
        cmd_agent(project, args)
    elif args.command == "task":
        cmd_task(project, args)
    elif args.command == "workflow":
        cmd_workflow(project, args)
    elif args.command == "approval":
        cmd_approval(project, args)
    elif args.command == "schedule":
        cmd_schedule(project, args)
    elif args.command == "listener":
        cmd_listener(project, args)
    elif args.command == "event":
        cmd_event(project, args)
    elif args.command == "queue":
        cmd_queue(project, args)
    elif args.command == "graph":
        cmd_graph(project, args)
    elif args.command == "vector":
        cmd_vector(project, args)
    elif args.command == "sources":
        cmd_sources(project, args)
    elif args.command == "evidence":
        cmd_evidence(project, args)
    elif args.command == "repo":
        cmd_repo(project, args)
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
