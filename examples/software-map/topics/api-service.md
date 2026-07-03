---
title: API Service
kind: service
topics:
  - software-map
  - services
entities:
  - sample-service
entity_metadata:
  - name: sample-service
    entity_type: Service
---

# API Service

## Summary

`sample-service` is the synthetic application this example tracks. It exposes a
small HTTP surface and includes enough manifests to exercise repo inventory.

## Interfaces

- `GET /health`

## Dependencies

- Python runtime from `pyproject.toml`
- small frontend/dev dependency surface from `package.json`

## Owners

- `@backend-team` for the application route tree

## Risks

- no nested Git history by default, so `repo changed` reports file-level state
  without commit-range context until a local repo is initialized

## Decisions

- HTTP surface and ownership are documented locally rather than inferred only
  from prompts
