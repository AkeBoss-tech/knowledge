> **Scoped implementation evidence.** Status and assignments below belong to
> the recorded checkpoint. Recheck current source before relying on them as
> current readiness. See the [documentation map](../README.md).

# Reviewed source reuse across two Runs

On 2026-09-19, the [Knowledge #31 acceptance fixture](../../scripts/qualify_reviewed_source_reuse.py)
ran from a fresh virtual environment containing only installed wheels built
from Core `2f80acb` and KRAIL `ba98652`. Both package imports resolved from
that environment's `site-packages`; the Core and Knowledge checkouts were
forbidden import roots. The isolated workspace and state were created anew.
The [sanitized receipt](receipts/knowledge-31-reviewed-source-reuse-20260919.json)
records exact wheel hashes and checks. The local Core owning suites
`tests/test_agent_memory.py` and `tests/test_personal_knowledge.py` passed
10/10 after the new reuse/restart case.

The fixture committed a synthetic Git document, captured it through retained
evidence, reviewed its exact revision, and pinned its KRAIL ResourceRef in one
published generic Participant. Two separate admitted tasks explicitly selected
that same source. Each worker-facing packet contained the pinned ref and exact
fixture phrase, while a raw unreviewed capture and a genuinely reviewed source
from another Project were denied. A deterministic worker published a separate
provenance-bearing artifact and execution event for each Run. After stopping
the first Core listener and recomposing Core/KRAIL from disk, both completed
Runs still exposed their original packet, artifact bytes and publication event.

Withdrawing the selected source then caused both packet reads and both
protected artifact-content reads to return content-free HTTP 409. The same
denials held after another Core listener restart and fresh Core/KRAIL
recomposition. Replaying the second task after withdrawal returned HTTP 403
without creating a third Run. This proves live release checks against the
canonical source state; it does not erase bytes that a provider previously
received. The fixture used a deterministic worker and no model/provider turn,
so the artifacts demonstrate Core authority and persistence rather than
quality of an AI-generated answer. The two service restarts occurred within
one qualification process, not an OS-process crash recovery test.

## Current source-bound candidate qualification

The script now emits `krail.reviewed-source-reuse-qualification.v2`. The historical
v1 receipt above remains unchanged and must not be relabelled as a current run.
The current SDK/MCP source candidate is `1.2.0rc3`; publication and deployment
are separate actions.

Build exact Core, SDK and MCP wheels with the existing package build backends.
Install those wheel files and their declared dependencies into a fresh Python
environment outside the source checkouts. From an unrelated working directory,
with `PYTHONPATH` unset, run:

```sh
env -u PYTHONPATH PYTHONNOUSERSITE=1 /absolute/venv/bin/python -I \
  /absolute/knowledge/scripts/qualify_reviewed_source_reuse.py \
  --knowledge-source-root /absolute/knowledge \
  --core-source-root /absolute/opensaddle \
  --krail-wheel /absolute/artifacts/krail-1.2.0rc3-py3-none-any.whl \
  --mcp-wheel /absolute/artifacts/rail_mcp-1.2.0rc3-py3-none-any.whl \
  --core-wheel /absolute/artifacts/opensaddle-CORE_VERSION-py3-none-any.whl \
  --receipt /absolute/fresh-receipt.json
```

`CORE_VERSION` must match the selected Core metadata; it need not equal the
SDK/MCP release number. Both source roots are forbidden import roots automatically;
additional `--forbid-import-root` values remain available. A receipt path must not
already exist.

Before and after the lifecycle, the qualifier checks all three wheel hashes,
source and distribution versions, SDK/MCP public module versions, the actual
installed MCP dependency requirement, every packaged source/installed file, and
that every source Python module is present in its wheel. Missing modules, changed
installed bytes and source drift fail with an assertion and no success receipt.
The source Git revision and dirty flag are recorded alongside an exact digest of
the compared package bytes. This binds local source, artifact and installation;
it is not a signed build receipt, reproducibility proof or publication check.

The real HTTP fixture remains the two-Run reuse/recomposition/withdrawal journey
described above. It cleans up its synthetic repositories, listeners and private
KRAIL state. It does not mutate the supplied source repositories, install
packages, access model credentials or dispatch real provider work. The obsolete
MCP source-string minimum-version assertion is subsumed by these actual installed
metadata and compatibility checks.


### October 28 local v2 qualification

A fresh Python 3.13 environment outside both source repositories installed exact
SDK/MCP `1.2.0rc3` and Core `1.2.0rc6` wheels. The first v2 candidate run and a
restored follow-up both passed the real HTTP lifecycle, including two independent
Runs, two service recompositions and source withdrawal after reopening. The
artifact check compared 103 SDK package files, 2 MCP package files and 324 Core
package/contract files. Core resources were resolved through its declared Hatch
`force-include` mappings; dependency names were compared using canonical Python
package naming. Both source repositories were dirty, explicitly recorded in the
receipt; this is not a clean committed release receipt.

Three actual fault-injection runs failed with the expected assertions and wrote
no success receipt: same-version changed wheel content, mismatched wheel version,
and an altered installed module. The installed module was restored to its exact
original SHA-256 before the second positive run. These mutate only disposable
artifact copies/the dedicated qualification environment, not source repositories.
The 100 owning context/MCP tests also passed. The weaker source-string assertion
about the MCP dependency minimum was removed; actual installed wheel metadata
and compatibility now supply that evidence.

Raw logs/receipts are retained locally under the integration task's ignored
`.private/back-office-8h-20261028/knowledge/` evidence directory. Later package
source changes require rebuilding the corresponding wheel and rerunning the v2
qualifier; these receipts do not qualify a later snapshot. Publication, signed
installer distribution and live provider work remain separate.
