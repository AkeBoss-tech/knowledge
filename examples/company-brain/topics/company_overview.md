---
title: Acme Metrics Company Overview
kind: company-note
topics:
  - company-context
  - operating-model
entities:
  - Acme Metrics
  - Revenue Operations
  - Product Operations
  - Support Console
  - Weekly Executive Brief
entity_metadata:
  - name: Acme Metrics
    entity_type: Organization
  - name: Revenue Operations
    entity_type: Team
  - name: Product Operations
    entity_type: Team
  - name: Support Console
    entity_type: System
  - name: Weekly Executive Brief
    entity_type: Workflow
links:
  - source: Revenue Operations
    type: OWNS
    target: Support Console
  - source: Weekly Executive Brief
    type: USES
    target: Support Console
---

# Company Overview

Acme Metrics is a fictional analytics software company used as a `company-brain`
fixture. The operating model in this example is intentionally small:

- Revenue Operations owns the support and onboarding workflow metrics.
- Product Operations tracks roadmap changes that affect customer onboarding.
- The weekly executive brief should summarize notable changes, risks, and open
  questions from those operating notes.
