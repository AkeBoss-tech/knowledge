# Minimal KRAIL Project

This is the recommended first demo fixture for KRAIL.

It is a tiny public local project with synthetic data that lets you show the
core KRAIL flow without needing private files or a large setup:

- validate a local knowledge workspace
- search local evidence
- generate a cited `think` envelope
- inspect the markdown graph
- dry-run a workflow

It uses synthetic data and does not ship generated ontology databases.

## Fast Demo

From the repository root:

```bash
./scripts/demo-minimal-project.sh
```

The script copies this fixture into a temp directory first, so you can run the
demo without dirtying the checked-in example.

## Manual Walkthrough

```bash
cd examples/minimal-project
krail --local doctor
krail --local pack use research-intelligence
krail --local capture "Synthetic regional employment fixture ready for review"
krail --local inbox list
krail --local search "employment index" --explain
krail --local think "How does the synthetic employment index differ by region?"
krail --local graph build
krail --local graph entities --type Dataset
krail --local graph edges --entity "Synthetic Regional Indicators"
krail --local workflow list
krail --local workflow run weekly_research_review --dry-run
```

From the repository root without installing the package:

```bash
PYTHONPATH=packages/rail-py python -m rail.cli --local --path examples/minimal-project doctor
```

Use this project for manifest smoke tests, docs examples, and MCP setup. Run a
hydration pipeline before using ontology or SQL query commands.

## Retrieval Notes

Search defaults to deterministic hybrid retrieval in this fixture:

- lexical matching over repo files
- graph boosts from Markdown frontmatter relations
- offline `local_hash` embeddings stored in `.krail/vector.sqlite`

The first hybrid search will build the local vector index automatically. Use
`--no-rag` if you want lexical plus graph ranking only.

To opt into model-backed embeddings instead of `local_hash`, rebuild the vector
index explicitly:

```bash
krail --local vector build --provider openai --model text-embedding-3-small
```

Or install the optional local embedding dependency:

```bash
pip install 'krail[embeddings]'
krail --local vector build --provider sentence_transformers
```
