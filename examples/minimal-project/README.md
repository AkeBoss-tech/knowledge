# Minimal KRAIL Project

This is a tiny public fixture for local-mode development and documentation. It
uses synthetic data and does not ship generated ontology databases.

```bash
cd examples/minimal-project
krail --local doctor
krail --local pack use research-intelligence
krail --local capture "Synthetic regional employment fixture ready for review"
krail --local search "employment index" --explain
krail --local workflow list
krail --local workflow run weekly_literature_refresh --dry-run
```

From the repository root without installing the package:

```bash
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project doctor
```

Use this project for manifest smoke tests, docs examples, and MCP setup. Run a
hydration pipeline before using ontology or SQL query commands.
