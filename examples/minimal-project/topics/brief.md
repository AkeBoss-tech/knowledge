---
title: Minimal Project Brief
kind: brief
topics:
  - synthetic-data
  - local-runtime
entities:
  - Synthetic Regional Indicators
  - KRAIL
entity_metadata:
  - name: Synthetic Regional Indicators
    entity_type: Dataset
  - name: KRAIL
    entity_type: Package
relations:
  - from: KRAIL
    type: indexes
    to: Synthetic Regional Indicators
---

# Minimal Project Brief

This public fixture uses synthetic regional indicators to exercise KRAIL
manifest loading, capture, search, task, and workflow flows without shipping
generated ontology artifacts.
