# Work Order 02: Runtime Enforcement Coverage

## Objective

Apply the shared authorization core to local runtime write and execute paths so
KRAIL stops enforcing permissions only on reads.

## Target Files

- `packages/rail-py/rail/knowledge.py`
- `packages/rail-py/rail/project.py`
- `packages/rail-py/rail/cli.py`
- `packages/rail-py/tests/test_cli.py`
- `packages/rail-py/tests/test_workflows.py`

## Depends On

- `specs/permission-work-orders/wo-01-authorization-core.md`

## Do Not Touch

- `packages/mcp-server/rail_mcp/server.py`
- API runner contracts unless a tiny compatibility shim is unavoidable

## Deliverables

- permission checks for `capture`
- permission checks for `topic_upsert`
- permission checks for `inbox_promote`
- permission checks for workflow execute/dispatch paths that currently bypass
  shared authorization
- clear blocked/denied result shapes for caller surfaces

## Required Behaviors

- writes should be denied when path/resource metadata disallows them
- dry runs should still perform authorization checks
- denial results should be auditable and understandable

## Verification

- `pytest packages/rail-py/tests/test_cli.py`
- `pytest packages/rail-py/tests/test_workflows.py`
- add or run targeted tests for new write denial cases

## Merge Notes

- consume the core authorization API rather than recreating policy logic
- avoid changing MCP behavior here; that belongs to Work Order 03
