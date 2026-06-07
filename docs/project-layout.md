# Project Layout

A local knowledge project should be predictable at the top level and flexible
inside topic workspaces.

```text
project/
  krail.yaml
  .ontology/
    ontology.yaml
    sources/
    pipelines/
  docs/
  topics/
  workflows/
  agents/
  prompts/
  skills/
  state/
    sources.json
    claims.json
    assumptions.json
    dependency_edges.json
    artifact_lineage.json
    verification_runs.json
  artifacts/
  interfaces/
```

The runtime should treat `state/` and `.ontology/` as explicit project records,
not as hidden application state.

## Project Packs

Future project packs should seed this layout for common modes:

- `research-intelligence`
- `company-brain`
- `policy-compiler`
- `software-map`
- `data-analysis`

