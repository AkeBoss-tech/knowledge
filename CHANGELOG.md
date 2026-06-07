# Changelog

## Unreleased

No changes yet.

## v0.2.0-local-preview

Second local-preview release focused on source-aware workflow orchestration.

### Added

- Local workflow specs under `research_plan/workflows/`
- Sequential workflow execution for command and agent steps
- Built-in workflow templates for doctor, weekly review, source refresh, RAG refresh, paper ingest, and release readiness
- Source dependency manifests with snapshot-based change detection and affected-document lookup
- `source_refresh` workflow template for source-aware markdown maintenance
- Markdown graph `Document depends_on Source` edges from `sources/dependencies.yaml`
- GitHub issue intake workflow for safe `/krail` commands that create tasks, run dry-run workflows, or inspect source state
- Workflow schema validation, run listing, and run status inspection
- Workflow failure policy, retries, and command timeouts
- Agent `session_result.json` template and prompt contract
- Workflow dry runs with repo-backed session records
- KRAIL doctor/platform agent prompt scaffolding
- MCP tools for workflow specs and KRAIL agent prompt rendering
- Minimal project workflow and platform-agent examples

## v0.1.0-local-preview

Initial local-first preview release.

### Added

- Local project scaffolding with `krail init`
- Capture, search, and deterministic think commands
- Markdown-frontmatter graph mode
- Graph validation and stale-artifact checks
- Local SQLite vector database at `.krail/vector.sqlite`
- RAG-style retrieval with `krail search --rag`
- Optional embedding providers for local hash, OpenAI, and sentence-transformers
- MCP tools for search, graph, vector, tasks, workflows, and integrity
- Local task/workflow dispatch records
- Minimal synthetic example project
- CI template generation with `krail ci init`

### Known Limitations

- `think` does not call an LLM yet.
- Default vector embeddings are deterministic hashed embeddings, not model embeddings.
- Legacy integrity tests still need a local-first cleanup lane.
