# Changelog

## v1.0.0 - 2026-07-11

### Changed

- Clarified the KRAIL v1 product contract as a local-runtime release rather
  than a hosted platform promise.
- Documented the stable local loop across the root README, package README, docs
  index, and release checklist.
- Made the current boundaries explicit around `rail.yaml`, deterministic
  retrieval, deterministic `think`, workflow/task records, integrity views, and
  MCP access to the stable subset.
- Labeled model-backed synthesis, mature pack registries, host-level
  sandboxing, and perfect semantic retrieval as future work rather than current
  guarantees.
- Aligned `krail` and `rail-mcp` at `1.0.0`, with `rail-mcp`
  depending on the compatible `krail>=1.0.0,<2.0.0` range.
- Marked the two published local-runtime distributions stable while keeping
  `packages/api/`, `packages/engine/`, and documented experimental surfaces
  outside the v1 support contract.
- Updated CI and release automation to verify Python 3.11, 3.12, and 3.13 and
  to build/publish both PyPI distributions.
- Added offline release smokes for the trust lifecycle and nested mounted
  projects, plus a runtime `mcp_contract` tool for auditing the stable MCP
  subset.
- Added a supported-use-cases guide and removed completed pre-v1 planning
  artifacts that no longer described current work.

## v0.2.3-local-preview

Third local-preview release focused on permission-aware local tooling,
federated mounted knowledge bases, and software-map repo inspection.

### Added

- Permission-aware local file tooling with `krail grep`, `krail files list`,
  `krail files read`, and `krail files stat`.
- Mounted child KRAIL projects via `mounts:` in `rail.yaml`, plus `krail mount
  list` for health inspection.
- Federated `search`, `find`, `think`, and graph summary across the local
  project and selected mounted child projects.
- Mount-targeted task and workflow proxy operations so the root project can
  create, inspect, and dispatch work inside child knowledge bases without
  flattening their state.
- MCP tools for mount listing, federated retrieval, federated think, and
  federated graph summaries.
- Deterministic repo snapshot, inventory, owner, dependency, symbol, and change
  inspection commands for software-map workflows.
- Git repository listener support and a built-in `git_change_monitor` template.
- Software knowledge mode workflow materialization for `sync_recent_changes`.
- Bundled `examples/software-map` project fixture and related bootstrap/tests.

### Fixed

- Permission filters are now reused by local file inspection commands instead of
  only by search/find surfaces.
- Federated read results preserve mount provenance in returned paths and
  citations.
- Listener polling can now detect local Git working-tree changes and trigger
  dry-run workflows deterministically.

### Added

- Deterministic `think` now returns citations, source freshness, affected documents, and source-refresh next actions.
- Local scheduler wrapper generation with `krail --local schedule install/list/remove`.
- Manual self-hosted GitHub Actions workflow for explicit KRAIL workflow execution.
- Pack workflows now expose materialization guidance, company-brain workflow templates, and `auto` runner fallback.

### Fixed

- Capture frontmatter now writes `captured_at` as a string and graph ingestion normalizes legacy YAML timestamps.
- Markdown graph JSON and summary artifacts are deterministic across identical rebuilds.
- CLI JSON output now stringifies datetime-like values instead of crashing.
- `company-brain` markdown-graph scaffolds no longer require the research panel dataset during verification.
- Workflow show/execute/schedule behavior now explains when a pack workflow must be initialized before use.

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
