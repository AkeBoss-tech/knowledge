# Company brain: who owns billing?

**Required:** development source revision `15df0a9` or later with
`rail.company_knowledge` and `rail.procedure_projection` installed. The August
`1.2.0rc2` wheel is not asserted to contain this source behavior.

**Prerequisites:** Python 3.11+, this checkout installed with
`python -m pip install -e packages/rail-py`; no credentials, model, or service.

**Entry command, from the repository root:**

```bash
python examples/company-brain/run.py
```

Three [fictional fixtures](fixtures/) represent a policy-document export, an
ownership-change ticket, and a service repository. Their labels do not imply
configured Confluence, Jira, or GitHub connectors. `run.py` reads their bytes,
binds exact source digests, constructs approved temporal records, and queries
the actual `CompanyKnowledgeService` with a signed **fixture** reader.

Expected output shows team-ledger on Monday; team-ledger for Tuesday *as known
Tuesday*; team-platform for Tuesday *as known Thursday*; and team-platform as
the current owner. The current answer cites the ticket source. The script
reopens persisted state, invalidates that exact ticket revision, and asserts
that the current answer becomes `stale` while earlier history remains
queryable. It also asserts that no owner is disclosed in the stale answer. A
separate approved policy record remains effective after ticket invalidation,
showing that unrelated knowledge is unaffected.

The [topic dependency](sources/dependencies.yaml) links the service export and
ticket to [billing ownership](topics/billing-ownership.md). The
[proposed update](expected/ownership-update.md) shows the reviewable text that
an ingestion/synthesis stage might suggest; the offline run does not write it
into the topic. This distinguishes source-change detection, candidate update,
and human promotion. A live connector or model-backed writer needs its own
adapter and authorization.

**Limitations:** The signed reader and approval labels are fixture constructs,
not a real company's authorization or human approval. The local semantic store
uses a temporary directory and the script deletes it on exit.

**Cleanup:** None beyond normal process exit. The checked-in fixtures and topic
are not modified.
