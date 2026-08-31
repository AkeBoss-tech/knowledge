# KRAIL Agent Guide

Guide version: 1.1.12
Project: Co-Scientist Workflow

The repository is the durable source of truth. Start every session with:

```bash
krail --local mode active
krail --local pack active
krail --local doctor
```

Use `search` for raw evidence and `think` for cited synthesis with gaps and
next actions. Put raw captures in `topics/inbox`, then promote supported
material into stable pages under `topics/`.

KRAIL 1.1 vocabulary:

- action: a typed operation; preview writes with a dry run first
- retriever: a read-only evidence producer
- trigger: an event observer that starts a workflow (`listener` is supported)
- run: a unified read-only view of workflow and agent execution

Inspect bundled guidance with `krail docs search <query>` and
`krail docs query <path>`.
