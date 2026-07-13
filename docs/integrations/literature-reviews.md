# KRAIL For Literature Reviews

Use KRAIL to keep a literature review inspectable across sessions: raw paper
captures remain separate from promoted claims, synthesis cites project evidence,
and unresolved questions stay visible rather than becoming chat-history loss.

## Start A Research Workspace

```bash
python -m pip install 'krail[local]'
krail init literature-review \
  --knowledge-mode research \
  --pack research-intelligence \
  --mode markdown_graph
cd literature-review
krail --local doctor
```

## Capture Before You Synthesize

Record a paper URL and an initial note in the inbox. Capture is deliberately
not the same as trusting a claim:

```bash
krail --local capture \
  --url 'https://doi.org/10.0000/example' \
  --title 'Example paper' \
  --topic 'long-context-genomics'

krail --local capture \
  'The paper reports a long-context regulatory benchmark; verify cell type, dataset, and metric before treating the result as comparable.' \
  --topic 'long-context-genomics' \
  --entity 'Example paper' \
  --entity-type Paper

krail --local inbox list
```

After reviewing the actual source, promote the capture into a durable topic:

```bash
krail --local inbox promote topics/inbox/<capture>.md \
  --topic long-context-genomics \
  --type method

krail --local integrity status
```

## Ask Evidence-Bounded Questions

Use search for raw project evidence and `think` for a cited answer envelope:

```bash
krail --local search 'long context regulatory benchmark' --explain
krail --local think \
  'Which studies in this project evaluate long-context genomic prediction, and what evidence gaps prevent a direct comparison?'
```

The deterministic default does not pretend a model generated an answer. It
returns evidence, citations, conflicts, gaps, and next actions. Treat any
model-backed synthesis as a draft until its cited sources are reviewed.

## A Weekly Review Routine

1. Capture papers, notes, and links as they arrive.
2. Promote only reviewed evidence into stable topics.
3. Search for conflicting claims, missing datasets, and stale sources.
4. Run `integrity status` before reporting a conclusion.
5. Record the next experiment or reading decision in the project, not only in chat.

## Use With An Agent

Connect the workspace through [Codex](codex.md), [Claude Code](claude-code.md),
or [Cursor](cursor.md), then give the agent this constraint:

> Search KRAIL first. Separate source-backed findings from hypotheses, cite
> KRAIL paths, and capture new material in the inbox rather than promoting it
> as trusted knowledge automatically.
