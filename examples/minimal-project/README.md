# Minimal KRAIL Project

This is the recommended first demo fixture for KRAIL.

It is a tiny public local project with synthetic data that lets you show the
core KRAIL flow without needing private files or a large setup:

- validate a local knowledge workspace
- search local evidence
- generate a cited `think` envelope
- inspect the markdown graph
- dry-run a workflow

It uses synthetic data and does not ship generated ontology databases.

## Fast Demo

From the repository root:

```bash
./scripts/demo-minimal-project.sh
```

## Manual Walkthrough

```bash
cd examples/minimal-project
krail --local doctor
krail --local pack use research-intelligence
krail --local capture "Synthetic regional employment fixture ready for review"
krail --local search "employment index" --explain
krail --local graph build
krail --local graph entities --type Dataset
krail --local graph edges --entity "Synthetic Regional Indicators"
krail --local workflow list
krail --local workflow run weekly_literature_refresh --dry-run
```

From the repository root without installing the package:

```bash
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project doctor
```

Use this project for manifest smoke tests, docs examples, and MCP setup. Run a
hydration pipeline before using ontology or SQL query commands.
