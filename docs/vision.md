# KRAIL: an adaptable knowledge and data backbone

Authority: Akash's explicit direction on October 17, 2026:

> “the only difference with knowledge is i want it to be an adaptable and extensible knowledge and data backbone”

This is the product north star. It is broader than agent memory, a research-note
repository, a particular ontology, or one application's context store. KRAIL
should be useful as a standalone foundation and as the knowledge/data backbone
for Floating Keys and other tools, people, and agent systems.

## Direction for design

The following are implications of that direction, grounded in the existing
repository's capabilities and contracts; they do not introduce a mandatory
technology stack or claim universal support today.

- Support different domains and kinds of information: documents and conversations,
  structured datasets, entities and relationships, claims and evidence, temporal
  records, decisions, procedures, and artifacts. Do not narrow the product to
  the first research, software, company, or robotics example.
- Adapt schemas, domain packs, ingestion, retrieval, processing, and integrations
  through explicit extension boundaries. Existing built-in modes should be
  useful starting points, not a closed list of possible domains.
- Preserve where information came from, its versions, ownership, access, and
  uncertainty. Corrections and changed sources should be able to affect derived
  knowledge; a generated statement should not gain authority from repetition.
- Let consumers use knowledge and data through appropriate interfaces, including
  the existing SDK, CLI, MCP and provider contracts. Local files, hosted storage,
  datasets, and rebuildable indexes have different authority and lifecycle roles.
- Keep the backbone usable independently of a particular UI, model, harness,
  orchestration system, or deployment. Local-first is an existing supported
  starting point, not a prohibition on suitable hosted or other adapters.

“Data backbone” does not by itself adopt a universal database, arbitrary query
execution, a new canonical writer, or a replacement for current permission
boundaries. Concrete expansions need deliberate contracts and verification.
Likewise, an old release's narrow limitations should not be treated as permanent
product non-goals.

## Relationship to Floating Keys

Floating Keys aims to be an adaptive, multiplayer AI co-founder and autonomous
back office. It needs continuity across conversations, projects, resources,
experiments, and changing operating procedures. KRAIL should support that work
without making Floating Keys its only consumer or absorbing every execution
and spending responsibility into the knowledge layer.

The [Floating Keys vision](https://github.com/AkeBoss-tech/opensaddle/blob/codex/plugin-code-workspace-20261013/docs/vision.md)
records the broader product direction. Existing KRAIL workflow/runner surfaces
retain their published contracts; their presence does not require duplicating
another system's authority or orchestration state.

## Source authority and current evidence

The direct source is [Record Floating Keys vision](codex://threads/01a14c35-b1c5-73d1-8f11-8d5384b5964f),
including Akash's explicit Knowledge clarification and instruction that his
project-chat statements outrank generated documentation. This document does
not claim an exhaustive review of every historical Knowledge chat.

Use [the documentation map](README.md) to reach the actual storage, extension,
dataset, semantic, and provider references. Their implementation scopes and
release limitations remain important evidence; generated growth plans, pilots,
or refactor suggestions are not automatically adopted requirements.
