# Release Checklist

Target: `v0.2.4`

Release train: pre-v1 packaging and automation hardening for `krail` and
`rail-mcp`.

Do not tag `1.0.0` yet. The release process is intended to be v1-ready, but the
remaining experimental surfaces are tracked in
`docs/v1-gap-closure-plan.md`.

The future KRAIL v1 release should be an honest local-runtime release: the
repo-backed workflow that works today, with unfinished platform surfaces clearly
excluded from the promise.

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

The v1 release does not promise:

- hosted platform behavior
- host-level sandbox isolation
- autonomous agent execution without review
- model-backed synthesis as the default `think` path
- mature external pack registries
- perfect semantic retrieval or reranking

## Fresh Smoke

These commands should pass from a supported Python 3.11+ environment. Use the
`local` extra so ontology-backed integrity surfaces are available in the same
environment as the release smokes and focused tests:

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
pip install --find-links packages/rail-py/dist packages/mcp-server/dist/*.whl
rail-mcp --help
deactivate
rm -rf .venv-release
```

The capture-to-topic loop should also remain working against a copied fixture:

```bash
tmpdir="$(mktemp -d)"
cp -R examples/minimal-project "$tmpdir/project"
krail --local --path "$tmpdir/project" capture "PDDLStream is useful for task and motion planning baselines." --topic robotics --entity PDDLStream --entity-type Package
krail --local --path "$tmpdir/project" inbox list
krail --local --path "$tmpdir/project" inbox promote topics/inbox/<capture>.md --topic task-and-motion-planning --type method
krail --local --path "$tmpdir/project" topic upsert task-and-motion-planning --content "Reviewed update with evidence."
krail --local --path "$tmpdir/project" graph build
krail --local --path "$tmpdir/project" vector build
krail --local --path "$tmpdir/project" integrity status
```

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
git tag -a v0.2.4 -m "KRAIL v0.2.4"
git push origin v0.2.4
```
