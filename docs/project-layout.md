# Project Layout

KRAIL v1 treats the local project repo as the source of truth. The smallest
supported project shape is:

```text
project/
  rail.yaml
  .ontology/
  topics/
  sources/
  research_plan/
  research_plan/state/
  artifacts/
```

Most scaffolded projects also include:

```text
agents/
skills/
specs/
.krail/
research_plan/workflows/
research_plan/sessions/
research_plan/audit/
```

What each area is for:

- `rail.yaml`: the manifest KRAIL loads at project boot
- `.ontology/`: ontology and hydration configuration
- `topics/`: durable topic pages plus `topics/inbox/` for raw captures
- `sources/`: dependency and source metadata
- `research_plan/`: tasks, workflows, work orders, decisions, sessions, and
  state records
- `research_plan/state/`: integrity-ledger JSON records
- `artifacts/`: outputs that can later be verified or promoted
- `.krail/`: local runtime state such as the active pack and vector index

KRAIL v1 does not require a hosted database or hidden control plane. Durable
project knowledge should live in repo-backed files, while `.krail/` and session
outputs can remain local operational state.
