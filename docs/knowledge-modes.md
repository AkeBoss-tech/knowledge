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

