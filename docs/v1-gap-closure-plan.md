# V1 Gap Closure Plan

This document tracks what "v1-ready release process" means for KRAIL without
claiming that the entire product surface is already `1.0.0`.

## Goal

Make releases reproducible from:

- a clean Git checkout
- local `python -m build` artifacts
- PyPI installs of `krail` and `rail-mcp`
- CI that verifies Python 3.11, 3.12, and 3.13 support

## Completed In This Pass

- package versions aligned on the same pre-v1 release train
- package metadata/classifiers updated for both published distributions
- release workflow builds, checks, and publishes both `krail` and `rail-mcp`
- release checklist includes build, `twine check`, and fresh-wheel install smoke
- checked-in CI expectations cover Python 3.11, 3.12, and 3.13
- generated `krail ci init` workflow matches the checked-in CI baseline

## What Is V1-Ready Now

The following release-process guarantees are expected before tagging a release:

- `python -m build packages/rail-py`
- `python -m build packages/mcp-server`
- `twine check packages/rail-py/dist/* packages/mcp-server/dist/*`
- fresh virtualenv install from built wheels can run `krail --version`
- GitHub Actions validates the package/test matrix on Python 3.11, 3.12, and
  3.13

## What Remains Experimental

These areas should stay explicitly pre-v1 even if packaging and release
automation are stable:

- `packages/api/` and `packages/engine/` are still on separate `0.1.x`
  development tracks and are not part of the PyPI release contract for
  `krail`/`rail-mcp`
- workflow runner integrations and automation surfaces are useful today, but
  they do not yet promise long-term compatibility across every external runner
- hosted/runtime deployment stories outside the local-first repo workflow remain
  secondary to the local CLI + MCP path
- permission enforcement is repo-mediated and auditable, but it is not a
  substitute for host-level sandboxing

## V1 Exit Criteria

Do not bump either published package to `1.0.0` until all of the following are
true:

- release checklist passes without manual, undocumented steps
- PyPI install docs are the primary documented install path
- experimental surfaces above are either stabilized or clearly excluded from the
  v1 support contract
- changelog and release notes state the compatibility expectations for `krail`
  and `rail-mcp`
