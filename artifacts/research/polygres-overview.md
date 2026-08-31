# Polygres: architecture and operating model

Research date: 2026-07-31

## Summary

Polygres is a managed PostgreSQL service that exposes graph, vector, text, and graph-plus-vector hybrid retrieval over application-owned PostgreSQL tables. PostgreSQL remains the operational source of truth; the service adds retrieval configurations, derived indexes, a dashboard, and a project-scoped HTTP Runtime API. It does not host an LLM or generate embeddings. [Polygres overview](https://docs.evokoa.com/polygres/getting-started/what-is-polygres)

## Request and data flow

1. The application writes ordinary relational records to PostgreSQL through pooled or direct connections. [Key concepts](https://docs.evokoa.com/polygres/getting-started/key-concepts)
2. The application or a background job generates embeddings and stores them in a fixed-dimension `vector(n)` column; Polygres does not generate them. [Configure retrieval](https://docs.evokoa.com/polygres/sdk/configure-retrieval)
3. An operator registers selected tables, identifiers, relationships, vector columns, text columns, metadata, and filters as retrieval configurations. Graph and vector indexes must reach a ready state before hybrid retrieval can run. [Configure retrieval](https://docs.evokoa.com/polygres/sdk/configure-retrieval)
4. Trusted backend code calls the project-specific Runtime API with a Polygres API key. The public Python SDK is an HTTP client and does not connect directly to PostgreSQL. [SDK repository](https://github.com/Evokoa/polygres-sdk)
5. The runtime performs graph, vector, text, or graph-plus-vector retrieval and returns ranked rows with IDs, properties, scores, and relationships. The application deduplicates results, applies a token budget, preserves provenance, and passes the resulting context to its chosen LLM. [Integration patterns](https://docs.evokoa.com/polygres/sdk/retrieval-integration-patterns)

## Retrieval engines

- **Graph:** pgGraph compiles selected relational relationships into forward and reverse compressed-sparse-row adjacency structures. These are rebuildable derived artifacts optimized for bounded traversal; PostgreSQL still owns table storage, transactions, durability, ACLs, and RLS. [pgGraph README](https://github.com/evokoa/pggraph)
- **Vector:** pgContext provides exact and HNSW approximate vector search. Approximate candidates are resolved back to live rows and exactly rescored under PostgreSQL visibility and permission rules before return. [pgContext README](https://github.com/evokoa/pgcontext)
- **Text:** the managed product supports PostgreSQL `tsvector` full-text search and `pg_trgm` fuzzy matching. [Polygres overview](https://docs.evokoa.com/polygres/getting-started/what-is-polygres)
- **Hybrid:** graph-first retrieval expands from a known record before semantic scoring; vector-first finds semantic candidates and then expands their relationships; joint retrieval combines independent graph and vector rankings using reciprocal-rank fusion. [Integration patterns](https://docs.evokoa.com/polygres/sdk/retrieval-integration-patterns)

## Important boundaries

- The managed SDK's current hybrid API combines graph and vector signals. It does not expose a single text-plus-vector endpoint; applications currently run those searches separately and fuse their rankings. [Integration patterns](https://docs.evokoa.com/polygres/sdk/retrieval-integration-patterns)
- Retrieval filters are defense in depth, not the application's authorization layer. The application must authorize requests before returning retrieved records. [Integration patterns](https://docs.evokoa.com/polygres/sdk/retrieval-integration-patterns)
- The current managed service is described as beta and uses fixed organization roles rather than custom roles or per-member overrides. [Polygres overview](https://docs.evokoa.com/polygres/getting-started/what-is-polygres)
- Polygres is aimed at operational SaaS retrieval. Its own documentation recommends specialized systems for warehouse analytics, extremely large vector estates, or graph-heavy workloads that justify a dedicated graph engine. [Polygres overview](https://docs.evokoa.com/polygres/getting-started/what-is-polygres)

## Assessment

The main architectural advantage is eliminating synchronization between an operational database and separate vector, graph, and text stores. The tradeoff is consolidation: transactional and retrieval workloads share the same underlying PostgreSQL environment, retrieval depends on careful schema/configuration design, graph artifacts and vector indexes need lifecycle management, and specialized engines may scale or query better for their narrow workloads. Because the managed platform is in beta and the public SDK is young, adoption should start with a representative, permission-sensitive retrieval benchmark rather than a broad migration.
