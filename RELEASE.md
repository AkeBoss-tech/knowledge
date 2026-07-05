# Release Checklist

Target: `v1.0.0` local-runtime contract

This checklist defines the KRAIL v1 release as an honest local-runtime release.
It is about the repo-backed workflow that works today, not a claim that every
experimental surface is finished.

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

These commands should pass from a supported Python 3.11+ environment:

```bash
krail init /tmp/krail-v1-smoke --pack research-intelligence --mode markdown_graph
krail --local --path examples/minimal-project mode active
krail --local --path examples/minimal-project pack active
krail --local --path examples/minimal-project doctor
krail --local --path examples/minimal-project search "employment index" --explain
krail --local --path examples/minimal-project think "employment index"
krail --local --path examples/minimal-project workflow list
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

At minimum, the docs-adjacent and contract-adjacent tests should pass:

```bash
PYTHONPATH=packages/rail-py:packages/mcp-server pytest -q \
  packages/rail-py/tests/test_bootstrap.py \
  packages/rail-py/tests/test_cli.py \
  packages/rail-py/tests/test_knowledge_modes.py \
  packages/rail-py/tests/test_think.py \
  packages/mcp-server/tests/test_server.py
```

## Packaging Note

Version numbers, distribution metadata, build artifacts, and final tag names
must be aligned in the packaging workstream before tagging. This checklist uses
`v1.0.0` as the intended contract target, not as proof that packaging work is
already complete.
