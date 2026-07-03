---
title: Software Map Brief
kind: brief
topics:
  - project-brief
  - software-map
entities:
  - sample-service
entity_metadata:
  - name: sample-service
    entity_type: Service
---

# Software Map Brief

This example treats `sources/sample-service/` as the codebase under review.

## Goal

Demonstrate how KRAIL can keep a local software map up to date with:

- deterministic repo inventory
- dependency and ownership extraction
- workflow-driven topic maintenance
- listener-triggered refresh

## Important Entities

- `sample-service`: synthetic FastAPI-style service fixture
- `health endpoint`: simple route used to prove endpoint detection
- `backend team`: owner declared through CODEOWNERS

## Important Sources

- `sources/sample-service/pyproject.toml`
- `sources/sample-service/package.json`
- `sources/sample-service/.github/CODEOWNERS`
- `sources/sample-service/app/routes/health.py`
- `sources/sample-service/docs/adr/0001-http-surface.md`

## Open Questions

- How much of the architecture can be inferred deterministically from files?
- Which gaps should stay as explicit unknowns rather than guessed knowledge?
