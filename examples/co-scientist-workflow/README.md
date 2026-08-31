# Co-Scientist Workflow (KRAIL example)

This runnable, local-first project models the *workflow pattern* described in
Google's Co-Scientist research. It is a research-planning example, not an
implementation of Google's Gemini-based product and it does not establish any
scientific conclusion on its own.

The supplied toy objective is deliberately non-biomedical: identify a
testable, evidence-backed hypothesis for reducing battery-energy use in a
fictional edge-service workload. Agents first expand the evidence-backed idea
set, then critique, rank, evolve, diversify, and synthesize it for a human
scientist/engineer to review.

## Workflow mapping

| Co-Scientist component | Local KRAIL representation |
| --- | --- |
| Supervisor | `research_plan/current_plan.md` and the workflow coordinator |
| Generation | `generate_hypotheses` task |
| Reflection / debate | `critique_and_debate` critic task |
| Ranking tournament | `rank_tournament` with explicit score rubric |
| Evolution | `evolve_finalists` research task |
| Proximity | `cluster_for_diversity` deduplication task |
| Meta-review | `meta_review` research task and human decision gate |

## Run it

From the repository root:

```bash
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/co-scientist-workflow doctor
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/co-scientist-workflow workflow validate co_scientist_idea_tournament
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/co-scientist-workflow workflow execute co_scientist_idea_tournament --dry-run
```

The dry run materializes a work order without launching workers. Before a real
run, replace the synthetic seed evidence with domain-appropriate primary
sources, set the research goal and constraints, and have a qualified human
review every candidate and experimental protocol.

## Sources

- Gottweis *et al.*, “Accelerating scientific discovery with Co-Scientist,”
  *Nature* (2026), doi:10.1038/s41586-026-10644-y.
- [Google DeepMind’s Co-Scientist overview](https://deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/)
- [Google Cloud Co-Scientist documentation](https://docs.cloud.google.com/gemini/enterprise/docs/co-scientist-and-alphaevolve)
