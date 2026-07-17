# Knowledge Operations Foundations

KRAIL 1.1 adds a small, explicit vocabulary for operating on project knowledge
without changing the local-first source of truth.

## Three Planes

KRAIL separates concerns into three planes:

1. The knowledge plane stores sources, topics, graph records, claims, evidence,
   and artifacts in the repository.
2. The retrieval plane plans queries and produces cited evidence packets from
   read-only retrievers.
3. The execution plane validates actions, observes triggers, executes workflows,
   and exposes runs for inspection.

These planes share contracts but not authority. A retriever cannot write. An
action declares its effects before it executes. A trigger begins a workflow;
it does not silently become the workflow itself.

## Vocabulary

- **Action**: a typed operation with input/output schemas, declared effects,
  capabilities, credentials, retry policy, and idempotency metadata. Actions
  that can write are dry-run by default through the public CLI and MCP surface.
- **Retriever**: a read-only evidence producer. Built-in retrieval signals
  include lexical, vector, graph, exact-code, recency, and ownership.
- **Trigger**: an event observer that can start a workflow. The existing
  `listener` command and storage layout remain supported compatibility names.
- **Workflow**: an explicit sequence of commands, actions, approvals, and agent
  work.
- **Run**: a unified read-only inspection view over workflow and agent execution
  records.
- **Evidence packet**: the portable output of retrieval: evidence, citations,
  trust and freshness labels, gaps, suggested next actions, and a trace.

## Retrieval V2

`search` remains backward compatible while adding a deterministic retrieval-v2
envelope:

```text
query -> query plan -> eligible retrievers -> reciprocal-rank fusion
      -> context expansion -> evidence packet + retrieval trace
```

The planner chooses eligible retrievers from query shape. Rank fusion uses a
deterministic reciprocal-rank algorithm with stable tie-breaking. Scores are
ranking signals, not calibrated probabilities. Each result explicitly labels
its source type, trust state, freshness, citation, and neighboring context.

## CLI

```bash
krail --local action list
krail --local action show capture-note
krail --local action run capture-note --input text='review this note'
krail --local action run capture-note --input text='approved note' --execute
krail --local retriever list
krail --local trigger list
krail --local run list
krail --local run trace <run_id>
krail docs search retrieval
krail docs query retrieval-v2
```

## MCP

The 1.1 action, retriever, evidence, trigger, and run tools are initially
experimental MCP tools. The stable MCP v1 groups remain unchanged. This lets
clients adopt the new contracts without silently expanding the existing
compatibility promise.

## Current Durability Boundary

Runs inspect existing repo-backed workflow snapshots and agent session files.
Workflow traces currently report snapshot-result durability; they are not yet
a crash-safe append-only execution event log. That distinction is returned in
the trace rather than hidden.
