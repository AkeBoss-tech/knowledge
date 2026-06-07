# Architecture

KRAIL is a headless knowledge runtime. The core should work locally from a
project repository without requiring a hosted database or a bundled web app.

The durable pieces are:

- `packages/engine`: YAML-driven source fetching, ontology building, hydration,
  transforms, and analysis hooks.
- `packages/rail-py`: Python SDK and CLI-facing project primitives.
- `packages/mcp-server`: agent-facing MCP tools.
- `packages/api`: optional HTTP adapter for local tools or replaceable UIs.

Preferred dependency direction:

```text
engine/core logic -> SDK/CLI -> optional API/MCP -> optional interfaces
```

Interfaces should live in projects, examples, or separate clients. They should
consume the SDK, API, or MCP layer rather than becoming the product center.

## Local State

The optional API stores operational records in `.krail/store.json` by default.
Durable project truth should stay in the project repo:

- `krail.yaml`
- `.ontology/`
- `workflows/`
- `agents/`
- `prompts/`
- `state/`
- `artifacts/`

## Next Refactors

- Rename packages toward `krail-core`, `krail-cli`, `krail-api`, and `krail-mcp`.
- Move economics defaults into `examples/economics-rail` or templates.
- Make workflow and project packs first-class.

