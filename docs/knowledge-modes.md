# Knowledge Modes

KRAIL projects should declare what kind of knowledge system they are. The mode
sets the operating model; packs add domain vocabulary.

```text
KRAIL core = engine
mode = operating model
pack = domain vocabulary
topic type = shape of durable knowledge
workflow = recurring behavior
integrity policy = trust rules
```

## Built-In Modes

- `research`: papers, methods, datasets, experiments, claims, evidence, and
  open questions.
- `company`: teams, systems, policies, workflows, owners, metrics, decisions,
  and stale operational docs.
- `personal`: lightweight projects, areas, resources, ideas, documents, and
  random notes.
- `software`: services, modules, APIs, dependencies, architecture decisions,
  incidents, and risks.
- `project`: milestones, decisions, artifacts, risks, blockers, and closeout.

Initialize with a mode:

```bash
krail init robotics-kb --knowledge-mode research
krail init acme-brain --knowledge-mode company
krail init life-admin --knowledge-mode personal
krail init architecture-map --knowledge-mode software
```

Modes can choose a default pack. For example, `company` activates the
`company-brain` pack unless `--pack` is explicitly supplied.

## Inbox To Topic Flow

Raw captures are daily work records. They are useful, but they are not the final
shape of the knowledge base.

```bash
krail --local capture "PDDLStream is a useful task and motion planning baseline" \
  --topic robotics \
  --entity PDDLStream \
  --entity-type Package

krail --local inbox list
krail --local inbox promote topics/inbox/2026-06-13-abc123.md \
  --topic task-and-motion-planning \
  --type method
```

Promotion updates a stable topic page under `topics/` and marks the inbox item
as promoted. Agents should prefer this flow over writing loose dated files.

## Topic Upsert

Use `topic upsert` when the target topic is already known:

```bash
krail --local topic upsert onboarding \
  --title "Onboarding" \
  --type policy \
  --content "Start with the systems checklist and current owner map." \
  --entity "Onboarding Workflow" \
  --entity-type Workflow
```

The topic template comes from the active mode and topic type. A `software`
service topic gets sections such as interfaces and dependencies; a `company`
policy topic gets sections such as owner, applies-to, evidence, and stale
warnings.

## Agent Rules

Agents should use this command path:

```bash
krail --local doctor
krail --local mode active
krail --local search "<question>" --explain
krail --local capture "<raw note>"
krail --local inbox list
krail --local inbox promote <capture> --topic <stable-topic>
krail --local topic upsert <stable-topic> --content "<reviewed update>"
krail --local graph build
```

`research_plan/` is for operations: plans, tasks, work orders, sessions, and
workflow state. Durable domain knowledge should live under `topics/`.

## Wiki Pages

Topic pages are the editable knowledge source. Wiki pages are generated reader
views under `docs/wiki/`.

```bash
krail --local wiki plan
krail --local wiki build
krail --local wiki list
krail --local wiki check
```

Use `--source topics/example.md` to generate one page, `--include-inbox` to
include raw captures, and `--force` to overwrite an existing generated page.

The baseline wiki builder is deterministic: it preserves source notes, records
`source_path`, stamps the active `knowledge_mode`, and keeps generated pages
separate from the canonical topic files. A UI should read `topics/` for editable
source material and `docs/wiki/` for polished browsing.

For polished pages, use the agent-backed workflow:

```bash
krail --local workflow init rich_wiki_generation
krail --local workflow execute rich_wiki_generation --dry-run
krail --local workflow execute rich_wiki_generation
```

That workflow plans wiki pages, builds the deterministic baseline, dispatches a
`wiki` coding-agent role, then runs `krail --local wiki check` plus graph/vector
refresh. The wiki agent is allowed to write `docs/wiki/` and `artifacts/wiki/`
and is instructed to create concise encyclopedia-style pages with rich elements
only where they help: tables, Mermaid diagrams, self-contained HTML demos,
timelines, visual summaries, or local image/asset references.

`krail --local wiki plan` also returns a `rich_artifacts` catalog so coding
agents can see what they are allowed to add:

- `interactive_html`: self-contained local HTML demos, simulations, timelines,
  calculators, sortable views, or concept explorers.
- `svg`: inline or linked SVG explainers for concept maps, flows, architecture,
  taxonomies, and visual summaries.
- `mermaid`: editable text diagrams.
- `image_asset`: local screenshots, generated images, annotated figures, or
  exported diagrams under `docs/wiki/assets/<page-slug>/`.
- `web_image_reference`: Google Images or web image references for real-world
  examples. Prefer official or permissively licensed sources and include source
  URL, credit, and license/status when known.
- `table`, `callout`, and `study_block`: concise supporting structures for
  comparisons, caveats, definitions, quick checks, or study prompts.

Generated pages must keep `source_path` frontmatter and should treat unsupported
material as gaps, not facts. `wiki check` rejects pages with missing source
links, empty bodies, missing source files, or unresolved artifact tokens such as
`[AI_DEMO]`. It also checks that local Markdown image links point to files that
exist inside the project.

This is the KRAIL analogue of a textbook wiki pipeline:

1. source material lives in repo-backed files
2. a planner identifies pages to generate
3. a builder creates source-linked wiki pages
4. graph/vector refresh makes the pages searchable
5. later artifact generators can add diagrams, demos, quizzes, or other rich
   blocks without replacing the source-of-truth topic page
