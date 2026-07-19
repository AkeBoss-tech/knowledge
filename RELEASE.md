# Release Checklist

Target: `v1.1.12`

Release train: stable local-runtime v1 for `krail` and `rail-mcp`.

KRAIL v1 is an honest local-runtime release: the repo-backed workflow that
works today, with unfinished platform surfaces clearly excluded from the
promise. `packages/api/` and `packages/engine/` retain their independent
development versions and are not published or supported by this checklist.

## Contract

The v1 release covers:

- local project scaffolding with `krail init`
- `rail.yaml`-backed projects with `.ontology/`, `topics/`, `sources/`,
  `research_plan/`, `research_plan/state/`, and `artifacts/`
- `doctor`, `mode active`, and `pack active`
- `capture`, `inbox list`, `inbox promote`, and `topic upsert`
- deterministic `search`, typed `find`, optional `vector build` and
  `vector search`
- deterministic `think` envelopes with citations, gaps, conflicts, freshness,
  and next actions
- repo-backed tasks, workflow templates, materialized workflow execution, and
  dry-run dispatch
- `integrity status` and related ledger views
- MCP access to the stable local project subset
- additive typed actions, read-only retrievers, retrieval-v2 evidence packets,
  trigger vocabulary, and unified run inspection

The v1 release does not promise:

- hosted platform behavior
- host-level sandbox isolation
- autonomous agent execution without review
- model-backed synthesis as the default `think` path
- mature external pack registries
- perfect semantic retrieval or reranking

## Fresh Smoke

The packages require Python 3.11 or newer. The release gate explicitly tests
Python 3.11, 3.12, and 3.13; newer compatible interpreters may be used but are
not part of this release's CI matrix. Use the `local` extra so ontology-backed
integrity surfaces are available in the same environment as the release smokes
and focused tests:

```bash
git clone https://github.com/AkeBoss-tech/knowledge.git
cd knowledge
python -m pip install --upgrade pip
pip install 'krail[local]' rail-mcp
krail --version
krail init /tmp/krail-v1-smoke --pack research-intelligence --mode markdown_graph
krail --local --path /tmp/krail-v1-smoke mode active
krail --local --path /tmp/krail-v1-smoke pack active
krail --local --path /tmp/krail-v1-smoke doctor
krail --local --path examples/minimal-project doctor
krail --local --path examples/minimal-project search "employment index" --explain
krail --local --path examples/minimal-project think "employment index"
krail --local --path examples/minimal-project permissions doctor
krail --local --path examples/minimal-project integrity status
krail --local --path examples/minimal-project action list
krail --local --path examples/minimal-project retriever list
krail --local --path examples/minimal-project trigger list
krail --local --path examples/minimal-project run list
krail docs query knowledge-operations
krail --local --path examples/minimal-project workflow list
krail --local --path examples/minimal-project workflow execute source_refresh --dry-run
krail --local --path examples/minimal-project workflow execute weekly_research_review --dry-run
krail --local --path examples/minimal-project grep "employment"
krail --local --path examples/minimal-project files list topics --recursive
krail --local --path examples/minimal-project graph summary --federated
rail-mcp --help
```

## Clean Checkout Build

```bash
python -m pip install --upgrade build twine
rm -rf packages/rail-py/dist packages/mcp-server/dist
python -m build packages/rail-py
python -m build packages/mcp-server
twine check packages/rail-py/dist/* packages/mcp-server/dist/*
```

## Fresh Wheel Install Smoke

```bash
python -m venv .venv-release
. .venv-release/bin/activate
python -m pip install --upgrade pip
pip install packages/rail-py/dist/*.whl
krail --version
krail --local --path examples/minimal-project integrity status
pip install --find-links packages/rail-py/dist packages/mcp-server/dist/*.whl
rail-mcp --help
deactivate
rm -rf .venv-release
```

Run the offline end-to-end trust-lifecycle smoke from the repository root. It
creates a temporary project and proves `init -> capture -> inbox promote/topic
update -> think registration -> integrity status`, including the expected
pending-evidence gate:

```bash
bash scripts/trust-lifecycle-smoke.sh
```

Run the separate offline nested-project smoke to verify parent/child init,
healthy mount resolution, federated search/find/think provenance, mount access
result shaping, mounted workflow dry-runs, and mounted child Codex CLI task
dispatch dry-runs:

```bash
bash scripts/nested-project-smoke.sh
```

The mount access checks cover KRAIL retrieval and proxy behavior only. They do
not claim host-level filesystem or process sandbox isolation.

## Focused Test Gate

At minimum, the docs-adjacent and contract-adjacent tests should pass from that
same environment:

```bash
PYTHONPATH=packages/rail-py:packages/mcp-server pytest -q \
  packages/rail-py/tests/test_bootstrap.py \
  packages/rail-py/tests/test_cli.py \
  packages/rail-py/tests/test_knowledge_modes.py \
  packages/rail-py/tests/test_think.py \
  packages/mcp-server/tests/test_server.py
```

## CI Expectations

- GitHub Actions `CI` passes on Python 3.11, 3.12, and 3.13
- GitHub Actions `Release Packages` verifies tests on Python 3.11, 3.12, and
  3.13 before publishing
- Release workflow builds and publishes both `krail` and `rail-mcp`
- The checked-in repo CI extends generated `krail ci init` project smoke with
  source-tree package tests, local extras, and distribution checks
- Manual `workflow_dispatch` runs may verify a ref, but publish jobs only run
  from `v*` tag refs

## Tag

```bash
git tag -a v1.1.12 -m "KRAIL v1.1.12"
git push origin v1.1.12
```
