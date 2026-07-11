# KRAIL Use Cases

KRAIL is a local-first operating layer for agent work that needs durable
project context, evidence, repeatable routines, and human review. It is not a
hosted collaboration suite or an autonomous agent platform.

## What It Adds

Without KRAIL, agent work is often split between chat history, loose notes,
untracked scripts, and undocumented decisions. A KRAIL project keeps the
working record in a repository:

```text
raw captures -> durable topics -> cited retrieval -> reviewed tasks/workflows -> integrity gate
```

That gives an agent context it can search and reuse across sessions, while
keeping the evidence, work orders, and promotion state inspectable by a human.

## Supported Workflows

| Use case | Practical flow | Value |
| --- | --- | --- |
| Research knowledge base | Capture papers and notes, promote them to topics, run `search`/`think`, then review integrity candidates. | Preserves sources, claims, limitations, and open questions beyond one chat. |
| Literature refresh | Materialize and run `weekly_literature_refresh` or `weekly_research_review`. | Makes recurring review and source freshness a repeatable routine. |
| Experiment record | Use `register_experiment` with linked topics, artifacts, and verification. | Keeps hypotheses, outputs, and evidence connected for later review. |
| Company brain | Use the `company` mode for teams, policies, systems, owners, decisions, and metrics. | Gives agents durable internal context without moving it to a hosted service. |
| Executive brief | Run `weekly_exec_brief` against a company project. | Produces a source-backed summary with known gaps and stale records visible. |
| Competitor or source review | Run `competitor_scan`, `company_profile_refresh`, or `source_review`. | Keeps company intelligence reviewable instead of becoming scattered research. |
| Software map | Use the `software` mode and `map_codebase` to inspect inventory, symbols, endpoints, dependencies, and owners. | Gives coding agents a durable architecture map instead of rediscovering the codebase each session. |
| Dependency/change review | Run `dependency_review` or `sync_recent_changes`. | Turns code changes into recorded maintenance risks, ownership gaps, and follow-up work. |
| Architecture decisions | Run `capture_architecture_decision` and promote the outcome to a topic. | Records rationale, alternatives, and consequences for future contributors. |
| Auditable Codex work | Create a task, inspect its work order, dispatch with `--dry-run`, then review the result session. | Makes agent scope and outputs reviewable rather than relying on an opaque chat run. |
| Parent/child project programs | Mount child KRAIL projects and use federated `search`, `find`, `think`, or mounted workflows. | Shares selected subproject knowledge with provenance while keeping project state separate. |
| Knowledge publishing | Build topic pages and a static wiki site. | Turns maintained local knowledge into a browsable reader view without changing the source of truth. |
| High-volume ingestion | Create an inventory queue, claim batches, run a workflow, then review evidence before promotion. | Coordinates repeatable ingestion with checkpoints and retries. |

## Start With An Example

The shipped examples correspond to the most common adoption paths:

- [`examples/minimal-project/`](../examples/minimal-project/README.md): research capture, retrieval, `think`, graph, and workflow dry-run.
- [`examples/company-brain/`](../examples/company-brain/README.md): company profiles, source review, competitor work, and executive briefs.
- [`examples/software-map/`](../examples/software-map/README.md): architecture mapping, dependency review, ownership, and change analysis.

From the repository root, validate one without changing the checked-in fixture:

```bash
PYTHON_BIN=python3 bash scripts/trust-lifecycle-smoke.sh
PYTHON_BIN=python3 bash scripts/nested-project-smoke.sh
```

Use Python 3.11 or newer. The nested-project smoke covers mount health,
federated retrieval, access shaping, child workflows, and a Codex CLI task
dispatch dry-run.

## Operating Boundaries

KRAIL's stable v1 contract is local and repo-backed. It does not provide
host-level sandboxing, managed multi-user hosting, or unattended autonomous
agent execution. Review promotions, workflow changes, and runner output before
treating them as trusted knowledge or shipping them.
