# Work Order 04: Capability Envelope And Documentation

## Objective

Define how temporary agent capability scope is represented in work orders and
update project documentation to match the implemented permission model.

## Target Files

- `packages/api/app/runners/contracts/work_order.py`
- `packages/api/app/services/capability_router.py`
- `docs/architecture.md`
- `packages/rail-py/README.md`
- `packages/mcp-server/README.md`

## Depends On

- `specs/permission-work-orders/wo-01-authorization-core.md`

## Coordinate With

- `specs/permission-work-orders/wo-03-mcp-runner-enforcement.md`

## Do Not Touch

- local runtime enforcement logic in `knowledge.py`

## Deliverables

- a concrete capability-envelope design for runner sessions
- any minimal schema updates needed in typed work orders
- docs that explain:
  - what KRAIL-mediated permissioning protects
  - how work-order scope relates to repo policy
  - the limits of local-first permissioning

## Required Behaviors

- docs must reflect current and intended implementation truth
- work-order schema changes should remain incremental and backward-aware

## Verification

- validate any affected tests for work-order contracts or routing
- reread docs for consistency with code and plan

## Merge Notes

- prefer additive schema changes
- call out unresolved follow-ups instead of implying hosted-grade security
