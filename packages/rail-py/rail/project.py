from __future__ import annotations

class Project:
    def __init__(self, slug: str, backend):
        self.slug = slug
        self._backend = backend

    def hydrate(self, pipeline_slug: str | None = None, *, mode: str | None = None) -> dict:
        """Trigger hydration. Uses the project's default pipeline if not specified."""
        if mode in {"markdown_graph", "markdown_frontmatter"} and hasattr(self._backend, "knowledge"):
            graph = self._backend.knowledge.graph_build(write=True)
            return {
                "status": "hydrated",
                "mode": "markdown_graph",
                "graph": {
                    "counts": graph.get("counts"),
                    "written": graph.get("written", []),
                    "warnings": graph.get("warnings", []),
                },
            }
        if hasattr(self._backend, "hydrate_project"):
            return self._backend.hydrate_project(self.slug, pipeline_slug)
        return self._backend.hydrate(pipeline_slug)

    def reconcile(self) -> dict:
        """Reconcile repo-backed planner/session/control-plane state."""
        if hasattr(self._backend, "reconcile_project"):
            return self._backend.reconcile_project(self.slug)
        if hasattr(self._backend, "reconcile"):
            return self._backend.reconcile()
        raise RuntimeError("This backend does not support reconcile()")

    def query(self, sql: str) -> "pd.DataFrame":
        import pandas as pd
        result = self._backend.query_sql(sql)
        return pd.DataFrame(result["rows"], columns=result["columns"])

    def classes(self) -> list[dict]:
        return self._backend.get_classes()

    def entities(self, class_name: str, limit: int = 100) -> "pd.DataFrame":
        import pandas as pd
        result = self._backend.get_instances(class_name, limit=limit)
        return pd.DataFrame(result.get("items", []))

    def search(self, q: str) -> list[dict]:
        if hasattr(self._backend, "knowledge"):
            return self._backend.knowledge.search(q)["hits"]
        return self._backend.search_entities(q)

    def think(
        self,
        q: str,
        *,
        limit: int = 5,
        mode: str = "deterministic",
        runner: str = "auto",
        dry_run: bool = False,
    ) -> dict:
        if hasattr(self._backend, "knowledge"):
            return self._backend.knowledge.think(q, limit=limit, mode=mode, runner=runner, dry_run=dry_run)
        if hasattr(self._backend, "think"):
            return self._backend.think(self.slug, q, limit=limit)
        return {
            "query": q,
            "mode": mode,
            "requested_runner": runner,
            "runner": None,
            "runner_resolution": None,
            "answer": "API-backed think is not wired yet.",
            "evidence": self.search(q)[:limit],
            "confidence": "low",
            "gaps": ["Use local mode for phase-1 deterministic think."],
            "conflicts": [],
            "suggested_next_actions": [],
            "verification": {"ok": False, "checks": []},
        }

    def register_think_result(self, result: dict, *, artifact_path: str, title: str | None = None) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("think-result registration requires local mode")
        return self._backend.knowledge.register_think_result(result, artifact_path=artifact_path, title=title)

    def think_sessions(self, *, limit: int = 20) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("think-session inspection requires local mode")
        return self._backend.knowledge.list_think_sessions(limit=limit)

    def think_session(self, session_id: str) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("think-session inspection requires local mode")
        return self._backend.knowledge.get_think_session(session_id)

    def capture(
        self,
        text: str = "",
        *,
        file_path: str | None = None,
        url: str | None = None,
        kind: str = "note",
        workflow: str | None = None,
        title: str | None = None,
        topics: list[str] | None = None,
        entities: list[str] | None = None,
        entity_type: str | None = None,
    ) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("capture requires local mode")
        return self._backend.knowledge.capture(
            text=text,
            file_path=file_path,
            url=url,
            kind=kind,
            workflow=workflow,
            title=title,
            topics=topics,
            entities=entities,
            entity_type=entity_type,
        )

    def modes(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("mode commands require local mode")
        return self._backend.knowledge.list_modes()

    def mode(self, mode_id: str | None = None) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("mode commands require local mode")
        return self._backend.knowledge.show_mode(mode_id)

    def active_mode(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("mode commands require local mode")
        return self._backend.knowledge.active_mode()

    def topic_list(self, *, include_inbox: bool = False) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("topic commands require local mode")
        return self._backend.knowledge.topic_list(include_inbox=include_inbox)

    def topic_upsert(
        self,
        topic: str,
        *,
        title: str | None = None,
        kind: str = "topic",
        content: str = "",
        source_path: str | None = None,
        sources: list[str] | None = None,
        entities: list[str] | None = None,
        entity_type: str | None = None,
    ) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("topic commands require local mode")
        return self._backend.knowledge.topic_upsert(
            topic,
            title=title,
            kind=kind,
            content=content,
            source_path=source_path,
            sources=sources,
            entities=entities,
            entity_type=entity_type,
        )

    def inbox_list(self, *, include_handled: bool = False) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("inbox commands require local mode")
        return self._backend.knowledge.inbox_list(include_handled=include_handled)

    def inbox_promote(
        self,
        capture_path: str,
        *,
        topic: str,
        title: str | None = None,
        kind: str = "topic",
        entities: list[str] | None = None,
        entity_type: str | None = None,
    ) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("inbox commands require local mode")
        return self._backend.knowledge.inbox_promote(
            capture_path,
            topic=topic,
            title=title,
            kind=kind,
            entities=entities,
            entity_type=entity_type,
        )

    def doctor(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("doctor requires local mode")
        return self._backend.knowledge.doctor()

    def pack(self, command: str, pack_id: str | None = None) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("pack commands require local mode")
        knowledge = self._backend.knowledge
        if command == "active":
            return knowledge.active_pack()
        if command == "list":
            return knowledge.list_packs()
        if command == "show":
            if not pack_id:
                raise ValueError("pack id is required")
            return knowledge.show_pack(pack_id)
        if command == "use":
            if not pack_id:
                raise ValueError("pack id is required")
            return knowledge.use_pack(pack_id)
        if command == "validate":
            return knowledge.validate_pack(pack_id)
        if command == "detect":
            return knowledge.detect_pack()
        if command == "suggest":
            return knowledge.suggest_pack()
        raise ValueError(f"unknown pack command: {command}")

    def agents(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("agent commands require local mode")
        return self._backend.knowledge.list_agents()

    def scaffold_krail_agents(self, *, force: bool = False) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("agent scaffold commands require local mode")
        return self._backend.knowledge.scaffold_krail_agents(force=force)

    def agent_prompt(self, role: str, *, task: str = "") -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("agent prompt commands require local mode")
        return self._backend.knowledge.agent_prompt(role, task=task)

    def create_task(
        self,
        title: str,
        *,
        description: str = "",
        runner: str = "auto",
        workflow: str | None = None,
        role: str = "research",
    ) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("task commands require local mode")
        return self._backend.knowledge.create_task(
            title,
            description=description,
            runner=runner,
            workflow=workflow,
            role=role,
        )

    def list_tasks(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("task commands require local mode")
        return self._backend.knowledge.list_tasks()

    def dispatch_task(self, task_id: str, *, runner: str | None = None, dry_run: bool = False) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("task dispatch requires local mode")
        return self._backend.knowledge.dispatch_task(task_id, runner=runner, dry_run=dry_run)

    def create_work_order(self, task_id: str) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("work-order creation requires local mode")
        return self._backend.knowledge.create_work_order(task_id)

    def list_workflows(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_list()

    def init_workflow(self, workflow_id: str, *, force: bool = False, template: str | None = None) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_init(workflow_id, force=force, template=template)

    def show_workflow(self, workflow_id: str) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_show(workflow_id)

    def validate_workflow(self, workflow_id: str) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_validate(workflow_id)

    def workflow_templates(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_templates()

    def workflow_runs(self, *, limit: int = 20) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_runs(limit=limit)

    def workflow_status(self, run_id: str) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_status(run_id)

    def run_workflow(self, workflow_id: str, *, runner: str = "auto", dry_run: bool = False) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_run(workflow_id, runner=runner, dry_run=dry_run)

    def execute_workflow(self, workflow_id: str, *, dry_run: bool = False, force: bool = False) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("workflow commands require local mode")
        return self._backend.knowledge.workflow_execute(workflow_id, dry_run=dry_run, force=force)

    def schedule_install(self, workflow_id: str, *, system: str = "cron", schedule: str | None = None, dry_run: bool = False) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("schedule commands require local mode")
        return self._backend.knowledge.schedule_install(workflow_id, system=system, schedule=schedule, dry_run=dry_run)

    def schedule_list(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("schedule commands require local mode")
        return self._backend.knowledge.schedule_list()

    def schedule_remove(self, workflow_id: str) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("schedule commands require local mode")
        return self._backend.knowledge.schedule_remove(workflow_id)

    def graph_build(self, *, write: bool = True) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("graph commands require local mode")
        return self._backend.knowledge.graph_build(write=write)

    def graph_validate(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("graph commands require local mode")
        return self._backend.knowledge.graph_validate()

    def graph_check(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("graph commands require local mode")
        return self._backend.knowledge.graph_check()

    def graph_entities(self, *, entity_type: str | None = None, limit: int = 100) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("graph commands require local mode")
        return self._backend.knowledge.graph_entities(entity_type=entity_type, limit=limit)

    def graph_edges(
        self,
        *,
        entity: str | None = None,
        relation_type: str | None = None,
        limit: int = 100,
    ) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("graph commands require local mode")
        return self._backend.knowledge.graph_edges(entity=entity, relation_type=relation_type, limit=limit)

    def graph_docs(
        self,
        *,
        topic: str | None = None,
        kind: str | None = None,
        source: str | None = None,
        entity: str | None = None,
        limit: int = 100,
    ) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("graph commands require local mode")
        return self._backend.knowledge.graph_docs(topic=topic, kind=kind, source=source, entity=entity, limit=limit)

    def graph_export(self, *, export_format: str = "json") -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("graph commands require local mode")
        return self._backend.knowledge.graph_export(export_format=export_format)

    def vector_build(self, *, provider: str | None = None, model: str | None = None) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("vector commands require local mode")
        return self._backend.knowledge.vector_build(provider=provider, model=model)

    def vector_search(self, query: str, *, limit: int = 10) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("vector commands require local mode")
        return self._backend.knowledge.vector_search(query, limit=limit)

    def sources_validate(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("sources commands require local mode")
        return self._backend.knowledge.sources_validate()

    def sources_list(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("sources commands require local mode")
        return self._backend.knowledge.sources_list()

    def sources_check(self, *, write: bool = True) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("sources commands require local mode")
        return self._backend.knowledge.sources_check(write=write)

    def sources_changed(self) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("sources commands require local mode")
        return self._backend.knowledge.sources_changed()

    def sources_affected(self, *, source_ids: list[str] | None = None) -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("sources commands require local mode")
        return self._backend.knowledge.sources_affected(source_ids=source_ids)

    def ci_init(self, *, path: str = ".github/workflows/krail-local-preview.yml") -> dict:
        if not hasattr(self._backend, "knowledge"):
            raise RuntimeError("ci commands require local mode")
        return self._backend.knowledge.ci_init(path=path)

    def series(self, series_id: str) -> "pd.DataFrame":
        import pandas as pd
        data = self._backend.get_series(series_id)
        return pd.DataFrame(data)

    def execute(self, code: str, timeout: int = 60) -> dict:
        return self._backend.execute_python(code, timeout=timeout)

    def run_analysis(self, plugin_slug: str, **kwargs) -> dict:
        return self._backend.run_analysis(plugin_slug, config=kwargs)

    def discover(self, q: str, tags: list[str] | None = None) -> list[dict]:
        """Search for connector templates (Census, FRED, etc)."""
        return self._backend.discover_templates(q, tags=tags)

    def search_registry(self, q: str, provider: str | None = None, geography: str | None = None) -> list[dict]:
        """Search the platform's data catalog."""
        return self._backend.search_registry(q, provider=provider, geography=geography)

    def list_secrets(self) -> list[dict]:
        return self._backend.list_secrets(self.slug)

    def set_secret(self, key: str, value: str) -> dict:
        return self._backend.set_secret(self.slug, key, value)

    def delete_secret(self, key: str) -> dict:
        return self._backend.delete_secret(self.slug, key)

    @property
    def agent(self) -> "AgentClient":
        """Access the research agent for this project."""
        from rail.agent import AgentClient
        if not hasattr(self._backend, "base_url"):
            raise RuntimeError("Agent access requires API mode (rail.connect())")
        return AgentClient(
            base_url=self._backend.base_url,
            project_slug=self.slug,
            api_key=getattr(self._backend, "api_key", ""),
        )

    def ontology(self) -> "OntologyView":
        """Access the owlready2 ontology directly (local mode or local onto.db path)."""
        from rail.ontology import OntologyView
        if hasattr(self._backend, "artifact_db_path"):
            # Local mode — use manifest-driven .ontology/ path
            db_path = str(self._backend.artifact_db_path)
        else:
            raise RuntimeError("ontology() requires local mode or a local onto.db path")
        return OntologyView(db_path)

    def integrity_status(self) -> dict:
        """Fetch the full integrity state of the project."""
        return self._backend.get_integrity_status(self.slug)

    def integrity_assumptions(self) -> list[dict]:
        """Fetch recorded assumptions and their status."""
        return self._backend.get_integrity_assumptions(self.slug)

    def integrity_sources(self) -> list[dict]:
        """Fetch recorded evidence sources."""
        return self._backend.get_integrity_sources(self.slug)

    def integrity_claims(self) -> list[dict]:
        """Fetch recorded empirical claims."""
        return self._backend.get_integrity_claims(self.slug)

    def integrity_claim_candidates(self) -> list[dict]:
        return self._backend.get_integrity_claim_candidates(self.slug)

    def integrity_artifact_lineage(self) -> list[dict]:
        return self._backend.get_integrity_artifact_lineage(self.slug)

    def integrity_source_detail(self, source_key: str) -> dict:
        return self._backend.get_integrity_source_detail(self.slug, source_key)

    def integrity_claim_detail(self, claim_key: str) -> dict:
        return self._backend.get_integrity_claim_detail(self.slug, claim_key)

    def integrity_artifact_detail(self, artifact_path: str) -> dict:
        return self._backend.get_integrity_artifact_detail(self.slug, artifact_path)

    def integrity_dependency_graph(self) -> dict:
        return self._backend.get_integrity_dependency_graph(self.slug)

    def integrity_stale_graph(self) -> dict:
        return self._backend.get_integrity_stale_graph(self.slug)

    def integrity_verification_runs(self) -> dict:
        return self._backend.get_integrity_verification_runs(self.slug)

    def integrity_benchmark(self, *, retrieval_limit: int = 10) -> dict:
        return self._backend.get_integrity_benchmark(self.slug, retrieval_limit=retrieval_limit)

    def integrity_rerun_plan(self, assumption_key: str) -> dict:
        """Preview the rerun plan for a given assumption change."""
        return self._backend.get_integrity_rerun_plan(self.slug, assumption_key)

    def apply_integrity_rerun_plan(self, assumption_key: str) -> dict:
        """Create rerun tasks based on the current rerun plan for an assumption change."""
        return self._backend.apply_integrity_rerun_plan(self.slug, assumption_key)

    def apply_integrity_reproducibility_rerun(
        self,
        outputs: dict[str, str],
        *,
        run_id: str = "rerun-verification",
        scope: str = "health",
    ) -> dict:
        return self._backend.apply_integrity_reproducibility_rerun(self.slug, outputs, run_id=run_id, scope=scope)

    def apply_integrity_freshness_evaluation(self, *, as_of: str | None = None) -> dict:
        return self._backend.apply_integrity_freshness_evaluation(self.slug, as_of=as_of)

    def apply_integrity_artifact_promotion(self, artifact_path: str, *, target_state: str) -> dict:
        return self._backend.apply_integrity_artifact_promotion(self.slug, artifact_path, target_state=target_state)

    def apply_integrity_source_candidate_promotion(self, candidate_key: str, *, source_type: str = "dataset") -> dict:
        return self._backend.apply_integrity_source_candidate_promotion(self.slug, candidate_key, source_type=source_type)

    def apply_integrity_claim_candidate_promotion(self, candidate_key: str, *, status: str) -> dict:
        return self._backend.apply_integrity_claim_candidate_promotion(self.slug, candidate_key, status=status)

    def apply_integrity_conflict_resolution(self, conflict_key: str, *, resolution: str) -> dict:
        return self._backend.apply_integrity_conflict_resolution(self.slug, conflict_key, resolution=resolution)

    def integrity_retrieve(
        self,
        query: str,
        *,
        limit: int = 10,
        artifact_types: list[str] | None = None,
        claim_statuses: list[str] | None = None,
        source_freshness: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        include_stale: bool = False,
        include_blocked: bool = False,
    ) -> dict:
        return self._backend.integrity_retrieve(
            self.slug,
            query,
            limit=limit,
            artifact_types=artifact_types,
            claim_statuses=claim_statuses,
            source_freshness=source_freshness,
            date_from=date_from,
            date_to=date_to,
            include_stale=include_stale,
            include_blocked=include_blocked,
        )

    def get_state(self) -> dict:
        """Fetch a structured context snapshot (classes, schema, sources, pipelines)."""
        return self._backend.get_project_state(self.slug)

    def get_work_order(self, work_order_id: str | None = None) -> dict:
        """Fetch a typed WorkOrder. If work_order_id is None, tries to resolve from environment."""
        if work_order_id is None:
            import os
            work_order_id = os.environ.get("RAIL_WORK_ORDER_ID")
        if not work_order_id:
            raise ValueError("work_order_id is required or must be set in RAIL_WORK_ORDER_ID env var")
        return self._backend.get_work_order(self.slug, work_order_id)

    def submit_session_result(self, result: dict, session_id: str | None = None) -> dict:
        """Submit the final session result."""
        if session_id is None:
            import os
            session_id = os.environ.get("RAIL_SESSION_ID")
        if not session_id:
            raise ValueError("session_id is required or must be set in RAIL_SESSION_ID env var")
        return self._backend.submit_session_result(self.slug, session_id, result)

    def ask(self, question: str, session_id: str | None = None) -> dict:
        """Ask a question to the planner/human mid-session."""
        if session_id is None:
            import os
            session_id = os.environ.get("RAIL_SESSION_ID")
        if not session_id:
            raise ValueError("session_id is required or must be set in RAIL_SESSION_ID env var")
        return self._backend.ask_question(self.slug, session_id, question)

    def __repr__(self):
        mode = "api" if hasattr(self._backend, "base_url") else "local"
        return f"Project(slug={self.slug!r}, mode={mode!r})"
