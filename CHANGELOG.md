# Changelog

## v0.1.0-local-preview

Initial local-first preview release.

### Added

- Local project scaffolding with `krail init`
- Capture, search, and deterministic think commands
- Markdown-frontmatter graph mode
- Graph validation and stale-artifact checks
- Local SQLite vector database at `.krail/vector.sqlite`
- RAG-style retrieval with `krail search --rag`
- Optional embedding providers for local hash, OpenAI, and sentence-transformers
- MCP tools for search, graph, vector, tasks, workflows, and integrity
- Local task/workflow dispatch records
- Minimal synthetic example project
- CI template generation with `krail ci init`

### Known Limitations

- `think` does not call an LLM yet.
- Default vector embeddings are deterministic hashed embeddings, not model embeddings.
- Legacy integrity tests still need a local-first cleanup lane.
