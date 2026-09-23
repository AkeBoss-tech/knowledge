# Governed memory: when guidance stops being usable

**Required:** development source revision `15df0a9` or later. The published
`1.2.0rc2` wheel is not used as evidence for this source-only example.

**Prerequisites:** Python 3.11+ and this checkout installed with
`python -m pip install -e packages/rail-py`. No credentials, model, Core server,
or external service are used.

**Entry command, from the repository root:**

```bash
python examples/governed-memory/run.py
```

The script adapts the lifecycle asserted by
[`test_observed_procedure_candidate.py`](../../packages/rail-py/tests/test_observed_procedure_candidate.py):

```text
Observed run artifact → new desired candidate → persisted and reopened
→ explicit signed fixture review → actionable guidance
→ exact source invalidation → stale explanation and guidance withheld
```

The candidate begins without inherited review, test evidence, or activation.
After the **fixture** review, its lifecycle is `reviewed` but activation remains
absent. Invalidation of the exact document source makes guidance unavailable
while the accepted review and predecessor remain in the local history. The
script asserts each state and prints three short outcome lines.

**Qualification boundary:** This is an offline fixture. Its Core artifact,
predecessor trust, review signing, and access policy are test constructs. The
[pinned Core HTTP receipt](../../docs/testing/receipts/observed-candidate-pinned-http-20260920.json)
separately records authenticated reads of a retained native Codex artifact,
using fixture predecessor and review signatures. It does not establish a new
provider turn, actual human acceptance, production input grants, Core
activation, or rollback. KRAIL records procedure evidence and supported
guidance; it does not grant execution permission.

**Cleanup:** `run.py` uses a temporary semantic store and deletes it on exit.
The checked-in observed-run fixture is not modified.
