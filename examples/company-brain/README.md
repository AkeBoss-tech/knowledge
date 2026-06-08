# Company Brain Example

This example shows a local-first KRAIL company-brain project with:

- the `company-brain` pack already activated
- pack workflows already materialized under `research_plan/workflows/`
- a small fictional operating model for graph and retrieval smoke tests

It is intentionally synthetic. The notes describe a fictional company so the
fixture stays safe to share and easy to reason about in tests and docs.

```bash
cd examples/company-brain
PYTHONPATH=../../packages/rail-py python -m rail.cli --local --path . doctor
PYTHONPATH=../../packages/rail-py python -m rail.cli --local --path . workflow list
PYTHONPATH=../../packages/rail-py python -m rail.cli --local --path . graph build
PYTHONPATH=../../packages/rail-py python -m rail.cli --local --path . graph entities --type Team
PYTHONPATH=../../packages/rail-py python -m rail.cli --local --path . workflow show weekly_exec_brief
PYTHONPATH=../../packages/rail-py python -m rail.cli --local --path . workflow run company_profile_refresh --dry-run
```

From the repository root:

```bash
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/company-brain doctor
```
