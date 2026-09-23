# Software map: which architecture topics changed?

**Required:** stable `krail==1.1.13` for local source dependency checks, or a
compatible source checkout. **Prerequisites:** Python 3.11+, this repository,
no credentials, model, or external service.

**Entry command, from the repository root:**

```bash
python examples/software-map/run.py --keep
```

The script copies this fixture into a temporary project, records a baseline,
adds `/ready` to the copied `health.py`, then runs KRAIL's `sources check` and
`sources affected`. It asserts that the changed source is
`local:sample-service-route` and that exactly `topics/api-service.md` and
`topics/health-endpoint.md` are affected. `topics/dependency-review.md` remains
outside this change. The script writes
`artifacts/proposed-health-update.md` in the copied project for review; it does
not silently edit the durable topics. `--keep` prints the retained workspace
path so you can inspect the changed route and proposal. Omit `--keep` for
automatic cleanup.

**Limitations:** Source dependencies are declared in
[`sources/dependencies.yaml`](sources/dependencies.yaml). Change detection
identifies affected topics; the proposed update is a deterministic fixture
draft, not agent synthesis or a reviewed architecture decision. The other
repo inventory, symbols, owner, dependency, and listener commands remain
available for deeper inspection.

## Optional isolated Git inspection

The sample service is nested inside KRAIL's Git repository. If you need a Git
snapshot of that fixture alone, first copy the example to a disposable
workspace. From `examples/software-map/sources/sample-service`, four parent
directories lead back to the KRAIL repository root:

```bash
cd examples/software-map/sources/sample-service
cd ../../../..
pwd
```

The previous walkthrough used `cd ../../..` from `sample-service`, which
landed in `examples/` rather than the repository root. The source-dependency
journey above needs no nested Git repository.

**Cleanup:** Remove the temporary project printed by `--keep` after review.
The checked-in sample service is never modified by `run.py`.
