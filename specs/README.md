# Specs

Detailed legacy specs were removed during the local-first cleanup because they
described the old hosted platform and UI assumptions.

Use `docs/` for the current architecture notes. Reintroduce specs only when they
describe implemented local-first behavior.

The first new implemented spec is the local brain UX skeleton:

- `search`: deterministic repo-file evidence retrieval
- `think`: deterministic answer envelope with evidence/gaps/conflicts
- `capture`: writes notes into `topics/inbox`
- `pack`: activates local knowledge pack metadata in `.krail/pack.yaml`
- `doctor`: checks manifest, core paths, pack, and capture inbox readiness
- `agent`: lists and launches local CLI agents as workers
- `task`: creates repo-backed task and work-order records
- `workflow`: turns active-pack workflow IDs into auditable tasks

Current implementation planning specs:

- `permissions-implementation-plan.md`
- `permission-work-orders/`
