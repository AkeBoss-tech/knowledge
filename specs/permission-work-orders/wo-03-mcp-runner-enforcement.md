# Work Order 03: MCP And Runner Enforcement

## Objective

Ensure MCP-exposed sensitive operations and runner scope handling respect the
same permission decisions as local runtime paths.

## Target Files

- `packages/mcp-server/rail_mcp/server.py`
- `packages/mcp-server/tests/test_server.py`
- `packages/api/app/services/policy_resolver.py`
- `packages/api/app/services/role_runtime_service.py`
- `packages/api/app/runners/base.py`

## Depends On

- `specs/permission-work-orders/wo-01-authorization-core.md`

## Coordinate With

- `specs/permission-work-orders/wo-04-capability-envelope-docs.md`

## Do Not Touch

- `packages/rail-py/rail/permissions.py` beyond minimal interface adoption
- broad API router behavior unrelated to permissions

## Deliverables

- centralized MCP tool-authorization helper
- gating for sensitive MCP surfaces such as Python execution and secrets
- alignment between role policy path/tool scopes and repo permission decisions
- tests for authorization failures on MCP-sensitive tools

## Required Behaviors

- MCP denials should be structured, not silent
- allowed path/tool scope passed into runners should be explicit
- work-order execution metadata should not contradict repo policy

## Verification

- `pytest packages/mcp-server/tests/test_server.py`
- targeted API or runner tests affected by scope handling

## Merge Notes

- keep changes narrow and composable
- document any contract changes that Work Order 04 must reflect
