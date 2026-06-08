---
title: RevOps Stack
kind: system-note
topics:
  - revenue-operations
  - systems
entities:
  - Revenue Operations
  - Pipeline Health Review
  - Support Console
  - Escalation Policy
  - Weekly Executive Brief
entity_metadata:
  - name: Revenue Operations
    entity_type: Team
  - name: Pipeline Health Review
    entity_type: Workflow
  - name: Support Console
    entity_type: System
  - name: Escalation Policy
    entity_type: Policy
  - name: Weekly Executive Brief
    entity_type: Workflow
links:
  - source: Revenue Operations
    type: OWNS
    target: Support Console
  - source: Pipeline Health Review
    type: USES
    target: Support Console
  - source: Escalation Policy
    type: GOVERNS
    target: Pipeline Health Review
---

# RevOps Stack

The fictional Revenue Operations team uses the Support Console to monitor:

- onboarding queue volume
- escalations that require product follow-up
- pipeline conversion warnings that should appear in the weekly brief

This note is useful for graph queries because it ties together a team, a
system, a workflow, and a governing policy.
