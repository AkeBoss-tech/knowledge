# Minimal project: from note to review gap

**Required:** published stable `krail==1.1.13` or a compatible source checkout.
**Prerequisites:** Python 3.11+, this repository, no credentials or model.
**Entry command, from the repository root:**

```bash
KRAIL_KEEP_WORKDIR=1 bash scripts/trust-lifecycle-smoke.sh
```

The script creates a fresh temporary project, captures a deployment-runbook
note, promotes it to `topics/deployment-runbook.md`, retrieves it, and writes
`artifacts/deployment-review-think.json`. Its final summary reports
`integrity: missing_evidence` and at least one claim candidate. Registration
and verification of a generated artifact do not approve its claims.

Inspect the temporary path printed by the script: `topics/inbox/` contains the
raw note; `topics/deployment-runbook.md` is the organized page; `artifacts/`
contains the deterministic evidence envelope; `research_plan/state/` holds
integrity records. The test checks JSON meaning, not the wording of an answer.

**Limitations:** This demonstrates a local review boundary. The promoted topic
is not an independently verified release policy, and deterministic `think`
does not call a model. Runner-backed synthesis is a separate explicit path.

**Cleanup:** Remove the printed temporary directory after inspection. Omit
`KRAIL_KEEP_WORKDIR=1` to have the script remove it automatically.

The checked-in project here remains a separate synthetic regional-indicators
fixture for search, graph, and MCP exploration. Its existing short replay is
`bash scripts/demo-minimal-project.sh`; that script copies the fixture into a
temporary workspace before modifying it.
