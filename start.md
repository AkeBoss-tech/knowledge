# Start Here: KRAIL for Agents

KRAIL is a local-first knowledge runtime. The repository—not chat history—is
the durable source of truth.

From a KRAIL project, begin with:

```bash
krail --local mode active
krail --local pack active
krail --local doctor
```

Use `search` for raw evidence and `think` for a cited answer shape with gaps,
conflicts, and suggested next actions. Capture raw notes into `topics/inbox` and
promote only supported material into stable topic pages.

KRAIL 1.1 distinguishes:

- actions: typed operations with declared effects
- retrievers: read-only evidence producers
- triggers: event observers that start workflows
- workflows: explicit sequences of work
- runs: unified inspection records for workflow and agent execution

Inspect bundled guidance without loading a project:

```bash
krail docs search "retrieval evidence"
krail docs query knowledge-operations
```

For the full release model, read
[Knowledge Operations Foundations](docs/knowledge-operations.md).
