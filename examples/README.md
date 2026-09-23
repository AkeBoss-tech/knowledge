# KRAIL examples

Each example names its release lane, prerequisites, entry command, expected
result, limitations, and cleanup. Fixture sources are synthetic and offline;
they do not imply live SaaS connectors or model execution.

| Example | Lane | Outcome |
| --- | --- | --- |
| [minimal-project](minimal-project/README.md) | Stable 1.1.13 | Capture, promotion, retrieval, and integrity expose unreviewed claims. |
| [company-brain](company-brain/README.md) | Development ≥ `15df0a9` | Temporal ownership changes and exact source invalidation. |
| [software-map](software-map/README.md) | Stable 1.1.13 local inspection; development for projection extensions | A route edit points to affected architecture topics and a reviewable update. |
| [co-scientist-workflow](co-scientist-workflow/README.md) | Stable 1.1.13 workflow pattern | Candidate hypothesis, critique, rubric, and human decision remain inspectable. |
| [governed-memory](governed-memory/README.md) | Development ≥ `15df0a9` | Reviewed procedure guidance is withheld after exact evidence invalidation. |

The first run uses the [trust lifecycle smoke](../scripts/trust-lifecycle-smoke.sh).
CI runs its meaning assertions against a built wheel and runs a separate
source-development lane for development examples.
