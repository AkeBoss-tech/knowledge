# KRAIL Growth Plan

This plan turns the recurring advice from the attached notes into a concrete
execution path for KRAIL.

## Goal

Make KRAIL easier to understand, easier to try, and easier to talk about.

Success means:

- more people immediately understand what KRAIL is for
- more visitors reach a first successful run
- more early users give feedback from real usage
- more public artifacts exist that make KRAIL easy to share

## Core Positioning

### Primary promise

KRAIL is the local-first knowledge runtime for agents that need durable memory,
evidence, workflows, and trust boundaries instead of disposable chat context.

### The enemy

KRAIL should be framed against one clear pain:

- "chat-memory collapse for serious project work"

Secondary enemies:

- fragile agent workflows
- undocumented research state
- private knowledge being pushed into hosted tooling
- "just glue together five frameworks" fatigue

### Suggested short hooks

- "Local-first memory and workflows for serious AI agent projects."
- "Give your agents durable project knowledge, evidence, and task memory."
- "Beyond RAG: a repo-backed knowledge runtime for local agents."

## Target Users

Start narrow. KRAIL is too broad to market well as a general-purpose system.

Primary wedge:

- engineers building local agent workflows
- researchers who need evidence-backed project memory
- advanced AI-tool users working in Codex, Claude Code, Cursor, or MCP clients

Do not lead with:

- generic ontology language
- internal architecture terms
- "client/runtime" phrasing without a user outcome

## Plan Overview

### Phase 1: Clarify the story

Timeline: 3 to 5 days

Deliverables:

- rewrite the top of the README around outcomes, not internals
- add a `Why KRAIL?` section near the top
- explicitly explain `pip install krail` and `import rail`
- choose one headline use case and make it the default demo
- tighten the first 30 seconds of the GitHub page

Acceptance criteria:

- a new visitor can answer "what is this?" in under 10 seconds
- a new visitor can answer "why would I use this?" in under 20 seconds

Checklist:

- replace "knowledge runtime extracted from RAIL" with a user-facing hook
- move advanced thesis material lower on the page
- keep one compact quickstart above the fold
- add one visual: GIF, terminal screenshot, or architecture image
- surface links to docs instead of overloading the README

### Phase 2: Create a killer demo

Timeline: 1 week

Deliverables:

- one polished end-to-end demo project
- one short terminal GIF or video
- one "before/after" narrative that shows KRAIL solving a painful workflow

Best demo shape:

1. create a local project
2. capture notes and sources
3. run `search`
4. run `think`
5. dispatch a dry-run workflow
6. show the repo-backed outputs

Recommended demo themes:

- literature review workspace for research agents
- coding knowledge base for a live software repo
- private company-brain workflow for local documents

Acceptance criteria:

- the demo finishes in under 3 minutes
- the audience can see the output, not just the setup
- the user value is obvious without needing ontology background

### Phase 3: Reduce onboarding friction

Timeline: 1 week

Deliverables:

- verified install path on a clean machine
- one-command or near-one-command bootstrap
- better first-run diagnostics from `doctor`
- clearer pack and mode defaults

Checklist:

- test install from the perspective of a new user
- document the exact happy path for macOS and Linux
- ensure error messages point to the next fix
- create a public smoke-test script for contributors
- make the recommended starter command extremely obvious

High-value improvement ideas:

- `krail init demo-kb --pack research-intelligence`
- sample workflow that produces a satisfying visible output on first run
- ready-made sample data or topic pages

### Phase 4: Launch content, not announcements

Timeline: 2 weeks for first cycle

Deliverables:

- 3 launch posts
- 1 walkthrough article
- 1 Hacker News or Show HN post
- 3 to 5 niche community shares asking for feedback

Content strategy:

- teach through a real problem
- use KRAIL as the tool that solves it
- ask for feedback from the right audience

Do not post:

- "please star my repo"
- feature dumps without narrative
- broad claims without proof

Good content angles:

- "How I gave local agents durable project memory without a hosted backend"
- "Beyond chat history: repo-backed memory for research workflows"
- "Using MCP + local knowledge packs to make AI agents auditable"
- "Why agent workflows need evidence, not just retrieval"

### Phase 5: Build a feedback loop

Timeline: ongoing

Deliverables:

- public issue labels for onboarding pain
- structured feedback questions
- recurring review of install friction and user confusion

Questions to ask early users:

- where did you get confused first?
- what problem did you think KRAIL solved before using it?
- what made you hesitate to try it?
- what outcome did you want fastest?
- what command or concept felt unnecessary?

Operational loop:

1. publish
2. collect confusion points
3. patch docs, install flow, or demo
4. publish the improved version

## Channel Plan

### GitHub

Use GitHub as the conversion page.

Actions:

- improve README headline and first screen
- pin one demo issue or discussion
- keep releases frequent and readable
- use strong repo topics
- add screenshots/GIFs

### PyPI

Use PyPI as the search-result landing page.

Actions:

- rewrite the short description around outcomes
- call out `krail` vs `rail` naming clearly
- mirror the same primary hook as GitHub

### X / LinkedIn

Use social posts to distribute proof.

Actions:

- post short clips of actual workflows
- attach one insight per post
- tie posts to current agent-tool conversations
- reply to people already discussing agent memory, MCP, or local-first tooling

### Hacker News

Use HN for sharp feedback and potential breakout reach.

Actions:

- post only when the README and demo are ready
- lead with a concrete problem solved
- stay active in comments
- collect every confusion point into an issue

### Niche communities

Use smaller communities for feedback density.

Targets:

- MCP communities
- local-LLM communities
- AI coding assistant communities
- research tooling communities
- open-source builder groups

Approach:

- ask for feedback on a specific use case
- mention limitations honestly
- show the working demo first

## Metrics

Track only a few signals at first:

- GitHub page views
- README-to-install conversion proxies
- PyPI downloads
- stars per week
- number of successful first-run reports from users
- number of onboarding issues found
- number of external mentions or tutorial links

Health metric:

- percent of user feedback that points to messaging confusion vs product bugs

If confusion is high, fix positioning before adding more features.

## 30-Day Execution Plan

### Week 1

- rewrite README top section
- choose one primary user persona
- decide on one default demo story
- collect 3 existing user pain points into messaging

### Week 2

- record demo GIF/video
- tighten quickstart
- improve install language and diagnostics
- rewrite PyPI description

### Week 3

- publish walkthrough article
- post demo thread on X/LinkedIn
- share to 2 to 3 niche communities
- ask for specific feedback

### Week 4

- patch friction found from feedback
- prepare Show HN
- ship one visible improvement based on user comments
- publish "what changed after feedback" follow-up

## Immediate Next Actions

1. Rewrite the README headline, first paragraph, and quickstart around one user
   outcome.
2. Produce one visual demo before doing broad promotion.
3. Rewrite PyPI messaging so it matches the README and clarifies `import rail`.
4. Share KRAIL in small relevant communities to get honest feedback before a
   larger launch.
5. Treat every confusion point as a product bug, not just a docs issue.

## Notes

This plan intentionally favors clarity, proof, and feedback over broad
promotion. Graphify worked because the story was simple, the pain was obvious,
and the proof was easy to share. KRAIL can grow the same way, but only if the
first-use experience becomes much more legible than it is today.
