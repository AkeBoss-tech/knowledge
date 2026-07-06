# KRAIL v1 Readiness Triage

Date: 2026-07-05

## Decision

Do not cut `1.0.0` yet.

The local-first runtime now looks mostly v1-shaped: the core CLI loop, MCP
adapter, packaging, release automation, and example-project smoke path are all
substantially in place. The remaining work is mostly contract hardening rather
than broad new feature work.

## Evidence Checked

Repo/docs/tests reviewed:

- `docs/v1-gap-closure-plan.md`
- `README.md`
- `packages/rail-py/README.md`
- `packages/mcp-server/README.md`
- `RELEASE.md`
- `CHANGELOG.md`
- `SECURITY.md`
- focused CLI/MCP/runtime tests under `packages/rail-py/tests/`,
  `packages/mcp-server/tests/`, and `packages/api/tests/`

Commands run during this triage:

- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project mode active`
- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project pack active`
- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project doctor`
- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project search "employment index" --explain`
- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project think "employment index"`
- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project workflow list`
- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project workflow execute weekly_research_review --dry-run`
- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project permissions doctor`
- `PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project integrity status`
- `bash scripts/demo-minimal-project.sh`
- `PYTHONPATH=packages/rail-py:packages/mcp-server pytest -q packages/rail-py/tests/test_bootstrap.py packages/rail-py/tests/test_cli.py packages/rail-py/tests/test_knowledge_modes.py packages/rail-py/tests/test_think.py packages/mcp-server/tests/test_server.py`
- `python -m build packages/rail-py`
- `python -m build packages/mcp-server`
- `python -m twine check packages/rail-py/dist/* packages/mcp-server/dist/*`
- fresh-wheel smoke in a temp virtualenv for `krail --version` and `rail-mcp --help`

Result summary:

- example-project local smoke commands passed
- `scripts/demo-minimal-project.sh` exited zero
- focused contract-adjacent test suite passed: `109 passed`
- both distributable packages built successfully
- `twine check` passed for both sdists and wheels
- fresh-wheel install smoke passed

## Readiness Matrix

| Goal | Status | Notes |
| --- | --- | --- |
| 1. V1 product contract and docs | Closed | Root/package README, docs index, release checklist, changelog, and security policy all describe the local-runtime contract rather than a hosted-platform promise. |
| 2. First-run and demo experience | Closed | `krail init`, `doctor`, capture, search, think, and workflow dry-run are covered by `test_bootstrap.py`; `scripts/demo-minimal-project.sh` also passed end to end. |
| 3. Think contract, runner synthesis, and session traces | Closed | `krail.think.v1` is documented and well tested, including deterministic, runner dry-run, runner failure, hybrid fallback, and session trace persistence. |
| 4. Retrieval quality and reranking defaults | Closed | Search now defaults to deterministic hybrid retrieval with `local_hash`; tests cover lexical/vector/graph behavior, expected top hits, permission filtering, and embedding-provider failure handling. |
| 5. Capture-to-trusted-knowledge lifecycle | Partial | Capture, inbox promote, provenance retention, and think-result candidate registration are present, but the full v1 lifecycle is not yet enforced by one release-gated CLI smoke path. |
| 6. Integrity readiness surface | Closed | `integrity status` now returns a concise readiness summary with next-command guidance and has good state coverage in tests. |
| 7. Permissions and security boundary | Closed | The repo-mediated boundary is documented in `README.md`, `packages/rail-py/README.md`, `packages/mcp-server/README.md`, and `SECURITY.md`; permission and runner-scope tests exist. |
| 8. Packaging, versioning, and release automation | Closed for the `0.2.4` train | Package versions, dependency ranges, CI, build, `twine check`, and fresh-wheel install smoke all line up for pre-v1 release mechanics. |
| 9. MCP stable v1 contract | Partial | Stable and experimental tool sets are documented and encoded in `server.py`, but the stability boundary is still primarily documentary rather than clearly surfaced to MCP clients at runtime. |
| 10. Workflow and template ergonomics | Closed | `workflow list`, `show`, `run`/`execute`, `schedule`, and init guidance now distinguish materialized vs template-only vs invalid workflows with actionable next commands. |
| 11. Repo self-hosting and developer health | Closed | Repo-root `krail --local doctor` failure is intentional, tested, and points contributors to `examples/minimal-project` or `--path`. |

All original workstreams appear at least partially landed. None look completely
untouched after the merge set.

## Remaining 1.0 Blockers

### Must-fix contract blockers

1. Promote the release train itself from pre-v1 to true v1.
   Today the docs and metadata still intentionally say `0.2.4`, pre-v1, and
   "do not tag `1.0.0` yet." That is correct for today, but it means the final
   version/metadata/release flip is still outstanding work rather than a
   no-op.

2. Make the MCP stability boundary easier for clients to consume.
   The stable subset exists in `packages/mcp-server/README.md` and
   `packages/mcp-server/rail_mcp/server.py`, but MCP clients still discover one
   flat tool surface. For a true `1.0.0`, the supported subset should be
   exposed more explicitly than "read the README and infer it."

3. Add one release-gated end-to-end contract smoke for the trust lifecycle.
   The pieces exist, but the release checklist still does not prove the whole
   path from capture/promotion through integrity-ready state in one documented
   acceptance flow.

### Nice-to-have polish

1. Mark experimental CLI surfaces more visibly in help/docs so the top-level
   `krail --help` output does not look like one undifferentiated compatibility
   promise.

2. Consolidate demo and release smoke language so README, `RELEASE.md`, and
   `docs/demo-script.md` point at the same shortest happy path.

3. Add a small contributor-facing "what is actually in v1" page or section so
   future docs changes have one canonical contract checklist to diff against.

## Recommended Next Codex Workstreams

### Workstream A: 1.0 release-contract flip

Scope:

- bump `krail` and `rail-mcp` to `1.0.0`
- update dependency ranges, changelog, release checklist, and security-policy
  wording
- keep `packages/api/` and `packages/engine/` explicitly out of the PyPI
  support promise unless intentionally promoted too

Acceptance tests:

- `rg -n "0\\.2\\.4|pre-v1|Do not tag \`1\\.0\\.0\` yet" README.md packages/rail-py/README.md packages/mcp-server/README.md RELEASE.md CHANGELOG.md SECURITY.md packages/rail-py/pyproject.toml packages/mcp-server/pyproject.toml`
- `python -m build packages/rail-py`
- `python -m build packages/mcp-server`
- `python -m twine check packages/rail-py/dist/* packages/mcp-server/dist/*`
- fresh-wheel install smoke for `krail --version` and `rail-mcp --help`

### Workstream B: MCP contract surfacing

Scope:

- expose the stable-vs-experimental MCP boundary in a way clients can inspect
  without relying only on README prose
- keep the current broad tool surface available, but make the v1 promise
  auditable and hard to misread

Acceptance tests:

- MCP tests assert the stable subset is exposed by the chosen runtime mechanism
- README still lists stable vs experimental tools
- tool errors continue returning JSON payloads rather than raw tracebacks

### Workstream C: Release-gated trust-lifecycle smoke

Scope:

- add one documented smoke path that proves
  `init -> capture -> inbox promote/topic update -> think or register_think_result -> integrity status`
- keep it fixture-based and offline by default

Acceptance tests:

- one CLI or script-based smoke command exits zero from repo root
- CI runs that smoke on at least one Python version
- `RELEASE.md` points at the exact command sequence

## If We Shipped 1.0.0 Today

The product would likely work for early adopters, but the biggest risk would
not be runtime failure. The risk would be ambiguity: users and MCP clients
could still misread which surfaces are truly covered by the long-term
compatibility promise.
