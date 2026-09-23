# KRAIL

[![PyPI version](https://badge.fury.io/py/krail.svg)](https://pypi.org/project/krail/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Durable, evidence-backed memory for AI agents.**

KRAIL turns a project workspace into reusable knowledge. Capture source material,
organize it into topics and claims, retrieve evidence with citations, and review
proposed changes before promoting them into the project's working record.

Use KRAIL through its CLI, Python SDK, or MCP server. Start with a local project;
connect an agent or execution system when you need synthesis and repeatable
workflows.

## Release and support status

| Lane | What it means | Start here |
| --- | --- | --- |
| **Stable** | Published `krail==1.1.13` local-runtime contract. The matching MCP adapter is available from source tag `v1.1.13`; there is no `rail-mcp==1.1.13` PyPI release. | [1.1.13 notes](docs/releases/1.1.13.md), [local example](examples/minimal-project/README.md) |
| **Preview** | Published matching `krail==1.2.0rc2` and `rail-mcp==1.2.0rc2` prereleases. A prerelease is not a promise that every main-branch feature is in its wheel. | [preview package](https://pypi.org/project/krail/1.2.0rc2/), [MCP package](https://pypi.org/project/rail-mcp/1.2.0rc2/) |
| **Development** | Source behavior at a pinned Git revision; this checkout may contain changes after the August prerelease even though package metadata still says `1.2.0rc2`. | [examples index](examples/README.md), [qualification evidence](docs/observed-run-artifact.md) |

Check `git rev-parse HEAD` before following a development example. Its README
states the minimum source revision and the fixture or integration boundary.
The [package README](packages/rail-py/README.md) and [docs index](docs/README.md)
describe the stable contract and deeper surfaces.

## How knowledge becomes usable

```text
Source material
    ↓
Captured evidence and candidate knowledge
    ↓ review and promotion
Project topics, claims, decisions, and procedures
    ↓
Retrieval, evidence packets, and context for agents

Source revisions and invalidations
    → identify affected knowledge
    → refresh it, mark it stale, or withhold unsupported guidance
```

The stable local path covers capture, inbox promotion, retrieval, and integrity
inspection. Development-only typed temporal projections and reviewed procedure
memory add more precise answers to *what changed*, *what was known at the time*,
and *which guidance is still supported*. See [company-brain](examples/company-brain/README.md)
and [governed-memory](examples/governed-memory/README.md). Each ingestion route
implements only the stages stated in its example; live source adapters and
runner-backed synthesis are separate integrations.

KRAIL helps an agent understand what supports a memory and whether that memory
is still appropriate to use.

## Basic concepts

- A **project** is a directory with a `rail.yaml` manifest. Its `topics/`,
  `sources/`, `research_plan/`, and `artifacts/` files hold the working record.
- A **source** is material the project depends on. **Evidence** connects a
  source or capture to a claim or answer; source paths and exact revisions matter
  when that support changes.
- A **capture** is raw input in `topics/inbox/`. **Promotion** organizes a useful
  capture into a durable **topic** page. Promotion does not verify every claim
  extracted from it.
- A **claim candidate** needs evidence review before it becomes a supported
  claim. An **artifact** is a generated output that can be registered and
  checked without automatically making its claims trusted.
- **Integrity** reports missing evidence, stale dependencies, and review
  readiness. **Tasks and workflows** record repeatable work; a dry run can
  prepare work orders without launching an agent.

See [project layout](docs/project-layout.md) for the folders and
[knowledge operations](docs/knowledge-operations.md) for the deeper contracts.

## CLI, Python API, and MCP primitives

All three entry points operate on a selected KRAIL project. The CLI is useful
in a terminal or script; `rail.local(path)` exposes the same local project to
Python; `rail-mcp` lets an MCP client call project tools over local stdio.

| Intent | CLI after `krail --local --path my-project` | Python `project = rail.local("my-project")` | MCP tool |
| --- | --- | --- | --- |
| Check the project | `doctor` | `project.doctor()` | `doctor` |
| Capture raw material | `capture "note"` | `project.capture("note")` | `capture` |
| Triage and promote | `inbox list`; `inbox promote <path> --topic <slug>` | `project.inbox_list()`; `project.inbox_promote(path, topic=slug)` | `inbox_list`; `inbox_promote` |
| Retrieve evidence | `search "question"` | `project.search_evidence("question")` | `search` |
| Build an evidence envelope | `think "question"` | `project.think("question")` | `think` |
| Inspect trust gaps | `integrity status` | `project.integrity_status()` | `integrity_status` |

`search` returns matching project material; `find` locates typed records such
as claims or workflow runs. Default `think` returns a deterministic evidence
envelope with citations and gaps. Runner-backed synthesis is an explicit
additional mode. `mcp_contract` lets an MCP client discover its supported tool
boundary before calling project tools; `provider_v1` is the
storage-independent resource and evidence interface.

For example, from Python after installing KRAIL and creating a project:

```python
import rail

project = rail.local("my-project")
capture = project.capture("Production releases require a reviewer.")
hits = project.search_evidence("release reviewer")["hits"]
readiness = project.integrity_status()["summary"]["status"]
```

`capture["path"]` names an inbox file; `hits` contain cited project paths;
`readiness` can still report a review gap. The optional local HTTP adapter is
described in [API runtime](docs/api-runtime.md); it is a separate integration
surface from this local Python API.

## Install

Python 3.11 or newer is required. Choose one published release lane:

```bash
# Stable local runtime
python -m pip install "krail==1.1.13"
```

```bash
# Matching published preview runtime and MCP adapter
python -m pip install "krail==1.2.0rc2" "rail-mcp==1.2.0rc2"
```

The distribution is `krail`, its Python import is `rail`, and the CLI is
`krail` (`rail` remains an alias). The MCP distribution and executable are
`rail-mcp`. It is published on PyPI for the **preview** line. For stable MCP,
install the adapter from the matching source tag:

```bash
python -m pip install "krail==1.1.13"
python -m pip install 'git+https://github.com/AkeBoss-tech/knowledge.git@v1.1.13#subdirectory=packages/mcp-server'
```

For current main source, see [Developing KRAIL](#developing-krail).

## First run: a deployment runbook note

The runbook says production releases require a reviewer. This offline journey
captures the note, promotes it into a topic, retrieves it, registers a
deterministic `think` artifact, and checks what still needs review. It needs no
model account or external service. From this repository checkout, in an
environment with KRAIL installed:

```bash
KRAIL_KEEP_WORKDIR=1 bash scripts/trust-lifecycle-smoke.sh
```

The script creates a temporary project and prints its path. Inspect the
following files there after it completes:

| Step | On disk | What to check |
| --- | --- | --- |
| Initialize and capture | `rail.yaml`, `topics/inbox/<capture>.md` | `capture.path` points into the inbox. |
| Promote and update | `topics/deployment-runbook.md` | `promote.topic.path` names the durable topic; this does not approve every extracted claim. |
| Retrieve and think | `artifacts/deployment-review-think.json` | `think.status=done`; the deterministic result is an evidence envelope with citations and gaps, not a model-written answer. |
| Inspect integrity | `research_plan/state/` | `verification_run.status=passed`, while `summary.status=missing_evidence` and claim candidates remain. |

The expected final lines include `integrity: missing_evidence` and a positive
claim-candidate count. The command succeeded and the artifact exists; the
candidate claims have **not** automatically become reviewed knowledge. Remove
the printed temporary directory when done. The complete command sequence and
meaning checks live in [trust-lifecycle-smoke.sh](scripts/trust-lifecycle-smoke.sh).

For a shorter fixture replay, see [minimal-project](examples/minimal-project/README.md).
Runner-backed `think` is an explicit additional path that writes a reviewable
session trace; it is not the expected output of this offline tutorial.

## Python and MCP entry points

The Python SDK opens the same local project:

```python
import rail

project = rail.local("/absolute/path/to/project")
print(project.doctor())
```

For an MCP client, install the matching `rail-mcp` release and configure a
local stdio server:

```json
{
  "mcpServers": {
    "krail": {
      "command": "rail-mcp",
      "args": ["--local", "--path", "/absolute/path/to/project"]
    }
  }
}
```

Client config formats vary; copy the relevant [integration guide](docs/integrations/README.md).
Once connected, call `mcp_contract` to discover the compatibility boundary,
`doctor` to inspect project health, and `search` for a known note such as
`deployment runbook reviewer`. The `provider_v1` tools expose a
storage-independent resource and evidence contract. The [MCP guide](docs/integrations/mcp-server.md)
explains the exact tool set and verification sequence.

## Examples

The [examples index](examples/README.md) names each example's release lane,
entry command, expected outcome, and limits. The paths are deliberately
different: a first-run local trust loop; historical company ownership;
source-linked architecture knowledge; evidence-backed hypothesis review; and
development-only governed procedure memory.

## Integration and security boundary

KRAIL owns project evidence, reviewed knowledge records, retrieval, provenance,
and supported knowledge operations. Local tasks, workflows, and runner adapters
also exist for repeatable project work. An external execution system such as
OpenSaddle may use KRAIL's evidence contracts, while retaining operational
authority over identity, approvals, activation, and execution. It is optional
for the local experience. See [architecture](docs/architecture.md) and
[procedure memory](docs/procedural-memory.md).

Local records are project-readable by default unless marked with restrictions.
KRAIL applies mediated permissions through its CLI, SDK, and MCP paths, but
ordinary direct filesystem access can bypass them. Signed authorization in
newer development surfaces does not turn a local project into an isolated
company deployment. A package published on PyPI is an installable client/server
adapter, not a publicly accessible knowledge server.

## Developing KRAIL

To try behavior from this source checkout, record its exact revision and use a
separate environment:

```bash
git rev-parse HEAD
python -m pip install -e 'packages/rail-py[local]' -e packages/mcp-server
python -m rail.cli --local --path examples/minimal-project doctor
```

The source-tree smoke can also run without relying on an installed KRAIL wheel:

```bash
KRAIL_SOURCE_TREE=1 bash scripts/trust-lifecycle-smoke.sh
```

See [contributing](CONTRIBUTING.md), [docs](docs/README.md), and the
[release checklist](RELEASE.md). KRAIL is MIT licensed; see [LICENSE](LICENSE).
