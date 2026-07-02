# Work Order 01: Authorization Core

## Objective

Refactor the current permission logic into a shared authorization core without
breaking existing public-by-default behavior.

## Target Files

- `packages/rail-py/rail/permissions.py`
- `packages/rail-py/tests/test_permissions.py`

## Allowed Touches

- import adjustments in directly dependent tests

## Do Not Touch

- `packages/rail-py/rail/knowledge.py`
- `packages/mcp-server/rail_mcp/server.py`
- runner or API files outside test-only import fixes

## Deliverables

- a normalized authorization decision model
- a shared `authorize(...)` or equivalent entry point
- compatibility adapters so existing read flows do not break
- expanded tests for read/write/execute/secret-style decisions

## Required Behaviors

- explicit deny semantics win over broad defaults
- public-by-default behavior still works when metadata is absent
- sensitivity continues to trigger audit logging
- existing frontmatter keys remain valid

## Verification

- `pytest packages/rail-py/tests/test_permissions.py`
- any nearby rail-py tests needed because of import or API changes

## Merge Notes

- this workstream defines the interface other threads should consume
- publish the exact function names and decision payload shape in the final note
