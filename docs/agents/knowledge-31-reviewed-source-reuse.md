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

To repeat locally, build exact Core and KRAIL wheels from the commits above
using `git archive` and install those wheel files into a fresh Python 3.13
environment with dependencies. From an unrelated directory with `PYTHONPATH`
unset, run:

```sh
env -u PYTHONPATH PYTHONNOUSERSITE=1 /path/to/venv/bin/python \
  /path/to/knowledge/scripts/qualify_reviewed_source_reuse.py \
  --forbid-import-root /path/to/opensaddle \
  --forbid-import-root /path/to/knowledge \
  --receipt /path/to/fresh-receipt.json
```

The script refuses an existing receipt path and cleans up its disposable
workspace, Core listeners and private KRAIL state. The independent source-free
wheel manifest from this run is at
`/tmp/opensaddle-20260919/knowledge31-wheels/manifest.json` on the
qualification host; the checked-in receipt retains the exact revisions and
hashes after that temporary directory is removed.
