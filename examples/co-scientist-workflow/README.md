# Co-scientist workflow: a hypothesis remains a candidate

This local-first project models the *workflow pattern* described in Google's
Co-Scientist research. It is not an implementation of Google's Gemini-based
product and does not establish a scientific conclusion.

**Required:** stable `krail==1.1.13` or a compatible source checkout.
**Prerequisites:** Python 3.11+, this repository, no credentials, model, or
external service for the offline replay.

**Entry command, from the repository root:**

```bash
python examples/co-scientist-workflow/run.py
```

The script validates the eight-step workflow and materializes a dry run in a
temporary copy of this project. It then checks and prints the
[hypothesis review artifact](expected/hypothesis-review.json): one candidate,
its synthetic supporting observation, a critique, the ranking rubric, and a
human decision to run an experiment only. The estimated energy benefit is not
measured. The fixture's `candidate_for_human_review` status prevents it from
being presented as an experimentally supported result.

| Stage | Local representation |
| --- | --- |
| Supervisor | `research_plan/current_plan.md` and workflow coordination |
| Generation | `generate_hypotheses` task |
| Critique and debate | `critique_and_debate` task |
| Ranking | `rank_tournament` with an explicit rubric |
| Evolution and diversity | `evolve_finalists` and `cluster_for_diversity` tasks |
| Meta-review | `meta_review` and a human decision gate |

**Limitations:** The artifact is a checked-in fictional candidate, not output
from the dry run. Dry-run materialization creates work orders without launching
agents or experiments. A live agent run is a separate path requiring a
configured runner, suitable primary sources, experimental constraints, and
qualified human review.

**Cleanup:** The entry script deletes its temporary copy on exit. It does not
alter the checked-in project.

Background: [Gottweis et al., *Nature* (2026)](https://doi.org/10.1038/s41586-026-10644-y)
and [Google DeepMind's overview](https://deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/).
