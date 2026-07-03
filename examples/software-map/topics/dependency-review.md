---
title: Dependency Review
kind: decision
topics:
  - software-map
  - dependencies
entities:
  - sample-service
entity_metadata:
  - name: sample-service
    entity_type: Service
---

# Dependency Review

## Context

The fixture intentionally mixes Python and Node manifests so KRAIL can exercise
multi-ecosystem dependency extraction in one local example.

## Decision

Treat backend and frontend manifests as first-class software evidence and store
their extracted summaries under `research_plan/state/`.

## Consequences

- dependency reviews can be deterministic before any agent synthesis
- software topics can cite manifests directly
- missing ownership or stale dependency risks can be recorded as explicit gaps

## Evidence

- `sources/sample-service/pyproject.toml`
- `sources/sample-service/package.json`
- `sources/sample-service/.github/CODEOWNERS`

## Follow Up

- add symbol-level extraction for Python and TypeScript in a later phase
