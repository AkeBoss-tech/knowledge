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

## Knowledge Operations Planes

KRAIL 1.1 separates the local runtime into a durable knowledge plane, a
read-only retrieval plane, and an effect-aware execution plane. Evidence
packets connect retrieval to synthesis; typed action contracts connect planned
work to execution; run views make workflow and agent records inspectable
without pretending the current snapshot format is a crash-safe event log.

See [Knowledge Operations Foundations](knowledge-operations.md) for the public
vocabulary and compatibility boundary.

## Permissioning Model

KRAIL permissioning is local-first and KRAIL-mediated. The repo remains the
source of truth, and permission decisions are applied when callers go through
KRAIL surfaces such as the SDK, CLI, MCP server, workflow execution, and
runner orchestration.

Today that protection is primarily metadata-driven and public-by-default:

- records stay readable unless they opt into restrictive metadata such as
  `visibility: private`, `allowed_roles`, `allowed_agents`, or `owners`
- denied access and allowed access to restricted or sensitive repo records are
  audited to
  `research_plan/audit/access.jsonl`
- permission checks narrow what KRAIL will return or dispatch; they do not
  replace filesystem permissions on the host machine

This distinction matters: KRAIL can mediate its own tools and agents, but it
does not claim hosted-grade isolation for someone who already has direct disk
or shell access to the repo.

## Runner Session Scope

Runner sessions are intentionally scoped through repo-backed work orders stored
under `research_plan/work_orders/`. The current contract has two layers:

- compatibility fields such as `capabilities_required` and `allowed_paths`
- an additive `capability_envelope` that groups session scope in one place for
  newer adapters and audits

The capability envelope is incremental by design:

- it narrows runner intent; it never widens repo policy
- its rule is `intersection_with_repo_policy`
- write paths are the only scope current runners broadly understand today
- read, tool, and secret scope are represented so MCP and runner adapters can
  adopt them without another top-level schema change

Routing decisions are logged to `research_plan/dispatch_log/<work_order_id>.json`
so operators can inspect both the chosen runner and the declared capability
envelope that shaped the session.

## Local State

The optional API stores operational records in `.krail/store.json` by default.
Durable project truth should stay in the project repo:

- `rail.yaml`
- `.ontology/`
- `topics/`
- `sources/`
- `research_plan/`
- `research_plan/state/`
- `artifacts/`

This is the v1 product contract: the local repo is the source of truth, while
API and MCP surfaces are adapters over that state.

## Next Refactors

- Rename packages toward `krail-core`, `krail-cli`, `krail-api`, and `krail-mcp`.
- Move economics defaults into `examples/economics-rail` or templates.
- Make workflow and project packs first-class.
