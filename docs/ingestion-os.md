# Knowledge Ingestion Operating System

KRAIL supports repo-backed ingestion loops for inventories that need parallel
workers, checkpoints, retries, and review before promotion.

```text
inventory -> queue -> claimed batch -> workflow inputs -> typed result -> evidence review
```

## Batch Queues

Create a queue from CSV, JSON, or JSONL:

```bash
krail --local queue init corptech_repos --source repos.csv --key repo_url
krail --local queue status corptech_repos
```

Reserve work for one worker:

```bash
krail --local queue claim corptech_repos \
  --limit 10 \
  --where family=application_or_api \
  --owner worker-1
```

Each claim writes a durable batch file under:

```text
research_plan/queues/<queue>/claims/<batch_id>.json
```

Finish or retry batches:

```bash
krail --local queue complete corptech_repos <batch_id>
krail --local queue fail corptech_repos <batch_id>
krail --local queue release corptech_repos --stale
```

## Parameterized Workflows

Pass explicit workflow inputs instead of relying on phrases like "newest
batch":

```bash
krail --local workflow execute corptech_repo_architecture_intake \
  --input batch_path=research_plan/queues/corptech_repos/claims/batch_123.json \
  --input family=application_or_api
```

Workflow steps can reference inputs:

```yaml
steps:
  - id: inspect_batch
    kind: command
    run: python scripts/inspect_batch.py "${{ inputs.batch_path }}"
```

## Typed Results

Workflows can declare a lightweight result schema:

```yaml
outputs:
  repositories_reviewed: steps.inspect.output.repositories_reviewed
outputs_schema:
  type: object
  required: [repositories_reviewed]
  properties:
    repositories_reviewed:
      type: array
```

Runs with missing required output fields are marked failed.

## Operator Dashboard

```bash
krail --local workflow dashboard
```

The dashboard summarizes workflow and agent sessions, including status, result
presence, failed steps, schema errors, and last log lines.

## Graph Ergonomics

`graph build` now prints a compact summary by default:

```bash
krail --local graph build
krail --local graph build --json
krail --local graph build --quiet
krail --local graph summary
krail --local graph diff
```

## Repo Inspection

Start repo ingestion with a local inspection pass:

```bash
krail --local repo inspect ./services/billing-api
```

The first implementation detects common manifest files, broad framework
families, and Python endpoint files. Clone/update, deeper dependency maps, and
language-specific endpoint extraction can build on this command.

## Evidence Review

Candidate evidence stays separate from trusted state:

```bash
krail --local evidence candidates
krail --local evidence promote-source <candidate_key>
krail --local evidence promote-claim <candidate_key>
```
