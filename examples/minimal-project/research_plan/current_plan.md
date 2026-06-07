---
title: Current Plan
kind: plan
topics:
  - pilot-readiness
  - graph-smoke-test
entities:
  - Synthetic Regional Indicators
  - employment_index
entity_metadata:
  - name: Synthetic Regional Indicators
    entity_type: Dataset
  - name: employment_index
    entity_type: Metric
relations:
  - from: employment_index
    type: measured_in
    to: Synthetic Regional Indicators
---

# Current Plan

Use the synthetic observations in `topics/data/observations.csv` to validate
local project operations.
