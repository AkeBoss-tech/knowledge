# Agent Guide

KRAIL should be used locally first.

## Local Project

```python
import rail

project = rail.local("/path/to/project")
```

Expected project files:

- `krail.yaml` or `rail.yaml`
- `.ontology/`
- `state/`
- `artifacts/`

## Optional API

The API is an adapter, not the source of truth.

```bash
make api
```

Default URL:

```text
http://localhost:8000/api/v1
```

Operational records are stored in `.krail/store.json` unless `LOCAL_STORE_PATH`
is set.

## MCP

Install:

```bash
pip install -e packages/mcp-server
```

Run against a local project:

```bash
RAIL_LOCAL=1 RAIL_PATH=/path/to/project rail-mcp
```

Useful tool families:

- `search`: retrieve evidence
- `think`: synthesize evidence with gaps/conflicts
- `capture`: add local notes or source pointers to `topics/inbox`
- `doctor`: inspect local project health
- `pack_active`: inspect active knowledge pack
- ontology classes and entities
- SQL queries over DuckDB artifacts
- Python execution
- hydration
- integrity status

## Search vs Think

Use `search` when you need raw evidence. Use `think` when you need a cited
answer shape with explicit gaps, conflicts, and next actions. Do not promote
generated statements into trusted state until they are registered as claims with
evidence and pass integrity checks.

## Principle

Agents can explore freely, but trusted project state should be promoted into
explicit repo-backed records and pass integrity checks.
