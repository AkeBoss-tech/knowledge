# KRAIL Permissioning Implementation Plan

## Goal

Upgrade KRAIL from its current public-by-default read filtering into a
consistent local-first authorization system that gates read, write, execute,
dispatch, and secret access across SDK, CLI, MCP, and runner surfaces.

This plan intentionally starts from the existing implementation in
`packages/rail-py/rail/permissions.py` instead of replacing it wholesale.

## Current Baseline

Already implemented:

- `PermissionActor` resolved from environment variables
- `PermissionPolicy` with visibility and allowlist checks
- read filtering for `search` and `find`
- workflow execution permission checks
- restricted-read audit logging
- `permissions doctor`

Current gaps:

- no single `authorize(...)` entry point for all action types
- write paths are not consistently permission-gated
- MCP tools expose sensitive operations without centralized authorization
- agent work orders and runner policies are adjacent to, but not unified with,
  repo permission decisions
- there is no clear path from resource metadata to temporary agent capability
  scope

## Design Constraints

- Preserve local-first behavior.
- Keep backward compatibility for public projects.
- Reuse existing manifest, role policy, and work-order structures.
- Treat KRAIL permissions as KRAIL-mediated enforcement, not filesystem
  sandboxing.
- Ship enforcement coverage before richer policy syntax.

## Target Architecture

### Core authorization model

Add a single authorization API in `packages/rail-py/rail/permissions.py`:

- `authorize(actor, action, resource, context) -> AuthorizationDecision`

Key concepts:

- `actor`: human, agent, or service principal
- `action`: `read`, `write`, `execute`, `dispatch_agent`, `read_secret`,
  `set_secret`, `promote`, `admin`
- `resource`: file, topic, source, workflow, secret, tool, or work-order target
- `context`: tool name, runner, session id, dry-run flag, requested path,
  workflow id, and optional purpose

### Resource metadata layering

Authorization should merge metadata from:

1. explicit record frontmatter
2. manifest/global path rules
3. resource-type defaults
4. work-order path scope for agent sessions

### Enforcement points

All of the following should pass through the same permission API:

- document reads in search/find/graph context
- `capture`
- `topic_upsert`
- `inbox_promote`
- workflow execution and workflow-run task dispatch
- Python execution
- secret read/write
- MCP tool wrappers for sensitive surfaces
- runner-launched work orders where allowed paths and tools are scoped

## Delivery Phases

### Phase 1: Authorization core and backwards-compatible policy model

Outcome:

- Keep current behavior for public projects.
- Introduce typed decisions and normalized authorization flow.

Changes:

- extend `packages/rail-py/rail/permissions.py`
- add decision/result models
- preserve current frontmatter keys such as `visibility`, `allowed_roles`,
  `allowed_agents`, `allowed_users`, `owners`, and `sensitivity`
- add path and tool resource helpers
- centralize audit logging around decisions

Acceptance criteria:

- existing read-permission tests still pass
- new tests cover write, execute, tool, and secret decisions
- public-by-default behavior remains unchanged when no restrictive metadata is
  present

### Phase 2: Enforcement coverage in local runtime and CLI surfaces

Outcome:

- local SDK and CLI write/execute paths use the same authorization logic

Changes:

- gate `capture`, `topic_upsert`, and `inbox_promote` in
  `packages/rail-py/rail/knowledge.py`
- gate workflow dispatch and any direct execution helpers
- expose denial reasons consistently in CLI output where relevant

Acceptance criteria:

- a denied actor cannot write restricted topics or captures through KRAIL
- denied workflow execution produces a blocked result with an audited denial
- tests cover success and denial cases for write surfaces

### Phase 3: MCP and runner enforcement unification

Outcome:

- sensitive MCP tools cannot bypass repo policy
- agent runs have explicit allowed paths and capability expectations

Changes:

- add centralized MCP authorization helper in
  `packages/mcp-server/rail_mcp/server.py`
- gate `execute_python`, secret operations, and write-capable MCP tools
- align work-order/routing metadata in `packages/api/app/runners/contracts`
  and `packages/api/app/services`
- ensure runner adapters receive explicit scope and denial semantics

Acceptance criteria:

- MCP-sensitive tools return structured authorization failures
- work orders can express the minimum required capabilities for a thread
- runner path scope and repo permission decisions are no longer independent

### Phase 4: Agent capability envelopes

Outcome:

- spawned agents operate with temporary scoped authority derived from work
  orders and project policy

Changes:

- add a capability envelope format for runner sessions
- map work-order allowed paths and tool permissions into env/session context
- add audit provenance linking actor, work order, session, and sensitive action

Acceptance criteria:

- background worker sessions have a machine-readable scope record
- audit logs can reconstruct who did what, via which work order, on which path

## Recommended Parallel Workstreams

### Workstream A

Authorization core and tests.

### Workstream B

Knowledge runtime and CLI enforcement coverage.

Depends on:

- Workstream A interface shape

### Workstream C

MCP and runner-side enforcement.

Depends on:

- Workstream A interface shape

### Workstream D

Work-order capability envelope and documentation.

Depends on:

- Workstream A decision model
- coordination with Workstream C

## File Ownership Map

### Workstream A

- `packages/rail-py/rail/permissions.py`
- `packages/rail-py/tests/test_permissions.py`

### Workstream B

- `packages/rail-py/rail/knowledge.py`
- `packages/rail-py/rail/project.py`
- `packages/rail-py/rail/cli.py`
- `packages/rail-py/tests/test_cli.py`
- `packages/rail-py/tests/test_workflows.py`

### Workstream C

- `packages/mcp-server/rail_mcp/server.py`
- `packages/mcp-server/tests/test_server.py`
- `packages/api/app/services/policy_resolver.py`
- `packages/api/app/services/role_runtime_service.py`
- `packages/api/app/runners/base.py`

### Workstream D

- `packages/api/app/runners/contracts/work_order.py`
- `packages/api/app/services/capability_router.py`
- `docs/architecture.md`
- `packages/rail-py/README.md`
- `packages/mcp-server/README.md`

## Verification Plan

- targeted pytest for permission, workflow, CLI, and MCP suites
- any runner or API unit tests affected by capability routing updates
- manual review that denial reasons remain understandable
- docs sync so permission behavior and limitations are clearly stated

## Non-Goals For This Iteration

- filesystem-level hard security against users who already have raw disk access
- hosted auth, OIDC, or centralized multi-user org policy
- encrypted-at-rest restricted resources
- perfect enterprise policy language before enforcement coverage exists

## Completion Definition

This initiative is complete when:

- all KRAIL-mediated read/write/execute sensitive paths use shared
  authorization decisions
- work orders and runner scope reflect explicit capability intent
- audit logs cover denials and sensitive allows consistently
- docs explain both what KRAIL protects and what it does not protect
