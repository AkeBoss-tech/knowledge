# KRAIL v1 Gap Closure Plan

This guide turns the current release-readiness gaps into bounded Codex workstreams.
Each workstream should run in its own Codex worktree on `gpt-5.4`, produce a
small reviewed change set, and stop only after its acceptance tests pass or a
specific blocker is recorded.

## Decision

KRAIL v1 should not mean "all experimental surfaces are finished." It should
mean the core local-first loop is stable, documented, tested, and honest:

```text
init -> capture -> search/find -> think -> task/workflow -> verify -> promote trusted knowledge
```

The v1 contract should cover:

- repo-backed local knowledge projects
- capture inbox and durable topic promotion
- deterministic search/find and optional vector retrieval
- evidence-backed `think` envelopes with citations, gaps, conflicts, and session traces
- repo-backed tasks, workflows, work orders, and result records
- integrity status for trust, staleness, evidence, and promotion readiness
- MCP access to the stable subset of local project capabilities
- clear permission and security boundaries

The v1 contract should not promise:

- hosted production platform behavior
- host-level sandbox isolation
- autonomous agents that can safely run without human review
- perfect LLM synthesis or perfect semantic retrieval
- a mature third-party pack/plugin ecosystem

## Constraints And Invariants

- Local repo state is the source of truth. The API and MCP server are adapters.
- `krail` is the install and CLI name; `rail` remains the import namespace.
- v1 must preserve the current project layout: `rail.yaml`, `.ontology/`, `topics/`,
  `sources/`, `research_plan/`, `research_plan/state/`, and `artifacts/`.
- All user-facing v1 claims must have a matching smoke test, CLI test, or explicit limitation.
- The default path must work offline where possible. Model-backed retrieval and synthesis must be optional.
- Permissions must be described as KRAIL-mediated access policy, not a secure OS sandbox.
- Each change must avoid broad rewrites unless it removes a real v1 blocker.

## Options Considered

### Option A: Feature-complete platform v1

Finish model-backed synthesis, external pack registries, full sandboxing, polished UI,
hosted API, and deep graph retrieval before v1.

Reject this. It expands v1 into a platform release and makes the date hostage to
the least mature subsystem.

### Option B: Honest local-runtime v1

Ship the stable local project loop with a precise contract, strong onboarding,
tests, packaging, MCP subset, and explicit limits around synthesis, retrieval,
permissions, and pack extensibility.

Recommend this. It matches what already works structurally and gives users
something stable without pretending the experimental edges are done.

### Option C: Keep calling it preview

Continue adding primitives while deferring v1.

Reject this unless the goal is research-only usage. The code already has enough
surface area that delaying the contract increases documentation drift and makes
future stabilization harder.

## Recommended V1 Release Gates

KRAIL is v1-ready when all of these are true:

- A fresh clone and a PyPI install both pass the documented smoke path.
- `krail init` produces a project whose `doctor` result is green or clearly actionable.
- The minimal demo completes in under three minutes and shows real output.
- `think` has a documented contract for deterministic, runner-backed, and hybrid modes.
- Retrieval defaults and optional embedding providers are documented and tested.
- Raw capture promotion into durable topic and integrity records is tested end to end.
- `integrity status` gives a clear trust/readiness answer for sources, claims, artifacts, and stale records.
- MCP exposes a named stable v1 tool subset and labels experimental tools.
- Permission docs state what is enforced, what is audited, and what is not isolated.
- Release metadata, classifiers, changelog, tags, package versions, and checklist all agree.
- CI covers Python 3.11, 3.12, and 3.13 for `rail-py` and MCP.
- The repo has a v1 release checklist that includes `twine check`, clean build, and smoke tests.

## Codex Workstream Goals

Each goal below is suitable as the initial prompt for a separate Codex thread.
The thread should inspect the repo, implement the smallest sufficient changes,
run focused tests, and report exact verification commands.

### Goal 1: V1 Product Contract And Documentation

Create a precise v1 product contract across README, package README, docs index,
release notes, and changelog. Replace preview/alpha language only where the
feature is truly covered by tests or explicit limitations.

Acceptance tests:

- `rg -n "Alpha|preview|pilot|production platform|Still early|v1" README.md docs packages/rail-py/README.md CHANGELOG.md RELEASE.md`
- README quickstart remains accurate against `examples/minimal-project`
- no v1 claim lacks either a tested command, doc caveat, or explicit future-work label

### Goal 2: First-Run And Demo Experience

Make the first-run path feel complete: `krail init`, `doctor`, capture, search,
think, workflow dry-run, and visible repo-backed outputs. Prefer fixing CLI
ergonomics and docs over adding new concepts.

Acceptance tests:

- fresh temp project from `krail init demo-kb --pack research-intelligence`
- `krail --local doctor`
- capture/search/think/inbox/workflow smoke commands
- demo script exits zero from repo root

### Goal 3: Think Contract, Runner Synthesis, And Session Traces

Harden `think` around a stable v1 contract. Deterministic mode must stay honest;
runner/hybrid modes must save prompt, evidence packet, result, gaps, conflicts,
citations, and failure state. Do not fake model-backed synthesis.

Acceptance tests:

- deterministic think tests still pass
- dry-run runner think materializes reviewable session files
- unavailable runner failure is actionable
- registered think output creates integrity artifact and claim candidates

### Goal 4: Retrieval Quality And Reranking Defaults

Improve and document retrieval behavior without making network/model services
mandatory. Establish deterministic hybrid ranking as the offline default and
optional model-backed embeddings/reranking as an explicit upgrade path.

Acceptance tests:

- search/RAG tests cover lexical, vector, graph, and permission filtering
- fixture queries return expected top evidence
- embedding provider errors are clear and non-destructive
- docs explain local hash vs model embeddings

### Goal 5: Capture-To-Trusted-Knowledge Lifecycle

Make the lifecycle from raw note to trusted knowledge explicit and tested:
capture -> inbox -> promote -> topic -> evidence/claim candidate -> integrity
status -> stale review. Avoid adding a new database or hidden state model.

Acceptance tests:

- CLI test covers capture, inbox list, inbox promote, topic list/upsert
- promoted topic preserves source/capture provenance
- integrity candidate promotion works from a think result or promoted topic
- stale/unsupported material is surfaced as a gap rather than trusted knowledge

### Goal 6: Integrity Readiness Surface

Turn integrity from a large primitive set into a reader-facing readiness answer:
what can be trusted, what is stale, what lacks evidence, and what next command
should repair it.

Acceptance tests:

- `krail --local integrity status` has a concise summary shape
- source, claim, artifact, stale graph, and verification detail commands remain available
- tests cover ready, stale, missing-evidence, and conflict states
- docs show how to use integrity before promotion or release

### Goal 7: Permissions And Security Boundary

Write the v1 permission/security contract and align behavior with it. KRAIL may
mediate reads/writes through CLI/MCP and audit restricted records; it must not
claim host-level isolation.

Acceptance tests:

- permission tests cover public default, restricted metadata, deny audit, allowed audit
- MCP runner scope tests cover allowed paths, denied tools, and secrets
- SECURITY.md documents guarantees and non-guarantees
- no docs call the system a production sandbox without a caveat

### Goal 8: Packaging, Versioning, And Release Automation

Make the release process reproducible from a clean checkout and PyPI install.
Align package metadata, classifiers, versions, release checklist, build outputs,
and CI expectations.

Acceptance tests:

- `python -m build packages/rail-py`
- `python -m build packages/mcp-server`
- `twine check packages/rail-py/dist/* packages/mcp-server/dist/*`
- fresh virtualenv install from built wheel can run `krail --version`
- release checklist updated for v1

### Goal 9: MCP Stable V1 Contract

Define and test the MCP v1 surface. Stable tools should be documented as stable;
experimental tools should be labeled or excluded from the v1 promise.

Acceptance tests:

- MCP README lists stable vs experimental tools
- server tests cover stable tools for doctor, search, think, capture, tasks, workflows, integrity, and permissions
- tool failures return actionable JSON rather than raw tracebacks
- MCP package dependency points at the intended KRAIL v1 version range

### Goal 10: Workflow And Template Ergonomics

Reduce confusion around pack workflows, materialized workflows, and templates.
The user should know when a workflow is ready, when it needs `workflow init`,
and what command to run next.

Acceptance tests:

- `workflow list` output clearly distinguishes materialized, template available, and invalid
- `workflow run`/`execute` errors suggest `workflow init` when appropriate
- tests cover pack-defined workflow templates and initialized workflows
- docs include one happy path and one repair path

### Goal 11: Repo Self-Hosting And Developer Health

Decide whether the KRAIL package repo itself should be a KRAIL project. If yes,
add a root `rail.yaml` and project state intentionally. If no, improve error
messages and docs so `krail --local doctor` at repo root points to
`examples/minimal-project` or `--path`.

Acceptance tests:

- repo-root `krail --local doctor` behavior is intentional and documented
- contributor docs give the correct local smoke command
- no accidental generated state is required for source builds

## Sequencing

Run these in three waves.

Wave 1:

- Goal 1: v1 contract
- Goal 8: packaging/release
- Goal 11: repo self-hosting/developer health

Wave 2:

- Goal 2: first-run/demo
- Goal 3: think contract
- Goal 4: retrieval quality
- Goal 10: workflow ergonomics

Wave 3:

- Goal 5: promotion lifecycle
- Goal 6: integrity readiness
- Goal 7: permissions/security
- Goal 9: MCP stable contract

The integration owner should merge Wave 1 first. Wave 2 can run in parallel
after the contract language stabilizes. Wave 3 should reconcile with the final
contract before merge.

## Integration Rules

- Each workstream must update tests before claiming completion.
- Each workstream must state changed files, test commands, and remaining risks.
- No thread should silently broaden the v1 promise to include unfinished features.
- No thread should remove existing preview functionality just to simplify docs.
- If two threads touch the same API contract, the later thread must cite the
  earlier merged decision and adapt rather than redefine it.

## If We Do Nothing

KRAIL will remain a powerful preview with impressive primitives but a fuzzy
product contract. Users will hit mismatches between README claims, release
metadata, CLI behavior, and security expectations. That is exactly how a strong
local tool accidentally teaches people not to trust it.
