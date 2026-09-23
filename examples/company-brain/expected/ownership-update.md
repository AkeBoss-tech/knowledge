# Proposed billing ownership topic update

Status: proposed, unreviewed.

Source dependency: `fixtures/ownership-change-ticket.json` at its exact digest.

Proposed change: record team-platform as the approved billing-api owner effective
2026-09-08, known from 2026-09-10. Preserve the earlier team-ledger answer for
queries whose known time precedes the ticket. If the ticket source is
invalidated, withhold the current owner until a replacement reviewed source is
available.

The offline `run.py` demonstrates the typed temporal answer. This Markdown is
the human-review proposal for a topic/folder update; no automatic synthesis or
live connector is exercised.
