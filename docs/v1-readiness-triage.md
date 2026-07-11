# KRAIL v1 Readiness Triage

Date: 2026-07-11

## Decision

The contract blockers identified by this triage are closed for the 1.0.0
local-runtime release: the trust lifecycle and nested-project paths have
release smokes, MCP exposes its contract at runtime, and package metadata now
matches the supported boundary.

## Evidence Checked

Repo/docs/tests reviewed:

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
- full checked-in CLI, workflow, and MCP suite passed: `154 passed`
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
| 5. Capture-to-trusted-knowledge lifecycle | Closed | `scripts/trust-lifecycle-smoke.sh` release-gates init, capture, promotion, think-result registration, integrity state, and the pending-evidence gate. |
| 6. Integrity readiness surface | Closed | `integrity status` now returns a concise readiness summary with next-command guidance and has good state coverage in tests. |
| 7. Permissions and security boundary | Closed | The repo-mediated boundary is documented in `README.md`, `packages/rail-py/README.md`, `packages/mcp-server/README.md`, and `SECURITY.md`; permission and runner-scope tests exist. |
| 8. Packaging, versioning, and release automation | Closed for `1.0.0` | Package versions, dependency ranges, CI, build, `twine check`, and fresh-wheel install smoke align with the local-runtime release contract. |
| 9. MCP stable v1 contract | Closed | Stable and experimental tool sets are documented, encoded in `server.py`, and exposed to clients through the runtime `mcp_contract` tool. |
| 10. Workflow and template ergonomics | Closed | `workflow list`, `show`, `run`/`execute`, `schedule`, and init guidance now distinguish materialized vs template-only vs invalid workflows with actionable next commands. |
| 11. Repo self-hosting and developer health | Closed | Repo-root `krail --local doctor` failure is intentional, tested, and points contributors to `examples/minimal-project` or `--path`. |

All original release-blocking workstreams are closed. Remaining items below are
non-blocking polish or explicitly experimental surfaces outside the v1
contract.

## Release Closeout

The original three release blockers are closed:

1. Package metadata and dependency ranges identify `krail` and `rail-mcp` as
   `1.0.0` local-runtime packages.
2. MCP clients can inspect stable and experimental tool sets through
   `mcp_contract`.
3. CI release-gates offline trust-lifecycle and nested-project smoke scripts.

The remaining release-owner actions are operational, not implementation work:
run the checklist in [`RELEASE.md`](../RELEASE.md), push the reviewed branch,
create the `v1.0.0` tag, and publish through the configured release workflow.
Future work should start from an issue or a new design note rather than this
closed pre-v1 plan.
