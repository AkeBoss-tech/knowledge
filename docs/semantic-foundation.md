# Semantic Foundation and Governed Ontology Induction

Phase 5 introduces a deliberately small semantic kernel for the
repository → issue → change → pull request → CI vertical. It is not a universal
ontology, graph database, arbitrary query service, or autonomous migration
engine.

## Durable kernel

`rail.semantic` defines scoped types, entities, aliases, evidence-backed facts,
explicit conflicts, and reversible entity merges. Facts distinguish observations
from claims and support only optional `valid_from` / `valid_until` intervals.
Every knowledge-bearing record binds exact `ResourceRef` evidence plus a
processing version and digest.

Memory and atomic JSON stores are the local semantic reference. The optional
Postgres adapter uses `krail_semantic_record`, installed by digest-addressed
migration `0002_semantic_records`. All stores share optimistic revision and
transaction rollback behavior. This table is a bounded domain store, not a SQL
or graph-query product surface.

## Semantic packs

A semantic pack is an immutable version of software-vertical type IDs and
source mappings. Its canonical content digest excludes only the external
signature envelope; the signature must bind that digest. Pack evaluations bind
the exact pack, processing implementation, deterministic quality metrics, and
baseline drift codes. Packs and evaluations never publish provider operations;
the Phase 5.2 layer consumes these records.

## Editable proposals, immutable publication

Ontology induction records observed structure, candidate concepts,
relationships and mappings, validation findings, and reviewer questions. Human
or delegated-agent authors create typed change-set drafts bound to an exact base
version and digest. Agent-authored drafts require a delegation ID, capability
digest, and policy digest.

Change sets can be compared, rebased into a new superseding draft, withdrawn,
or authored as rollback proposals. These actions never mutate a published
version. The lifecycle is explicit:

1. Build a change-set draft and immutable package version.
2. Propose the exact pair.
3. Begin review and resolve blocking findings/questions.
4. Record a human review digest.
5. Publish only the reviewed content digest.

Migration objects remain proposals. KRAIL does not execute them automatically,
and no agent draft, review, or semantic-pack signature bypasses enterprise
authorization or policy.
