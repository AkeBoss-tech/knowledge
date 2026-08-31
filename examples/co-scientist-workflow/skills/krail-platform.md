# KRAIL Platform Skill

Use this skill when creating or changing KRAIL workflows, agent roles,
prompts, skills, or project operating conventions.

## Workflow Rules

- Store durable workflow specs under `research_plan/workflows/`.
- Use command steps for deterministic checks.
- Use agent steps for research, coding, synthesis, or audit work.
- Add a verification step before any workflow claims completion.
- Prefer dry runs before cron dispatch.
