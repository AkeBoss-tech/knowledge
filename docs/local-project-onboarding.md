# Local project onboarding

KRAIL can inspect an ordinary local code repository before a KRAIL manifest
exists. Preview is the default and performs no writes:

```bash
krail onboard /path/to/repo
krail onboard /path/to/repo --apply
krail onboard /path/to/repo --apply --runner codex_cli --dry-run
krail onboard /path/to/repo --apply --runner claude_code --dry-run
```

On apply, KRAIL creates only missing KRAIL-owned manifest, discovery, starter
skill, and workflow files. Existing files and records are never replaced. A
repository with `rail.yaml` or `krail.yaml` is reported in `refresh` mode.

## Safety and trust

Discovery stays inside the resolved authorized root, does not follow symlinks,
honors Git ignore rules, and omits dependency, generated, cache, credential,
and likely-secret paths. Its fingerprint hashes included file paths and content,
the Git revision, and dirty state. Recommendations for tests, lint, type checks,
and builds include evidence locators with repository-relative path, revision,
line span, and SHA-256 digest.

Runner selection only writes a bounded `krail.project-onboarding-work-order/v1`
record for OpenSaddle or another privileged host to execute. KRAIL never starts
Codex or Claude during onboarding, nor does it install, activate, schedule, or
run recommended automation.

Provider output must use the versioned project-profile, pack-proposal, or
automation-recommendation contract. Claims without evidence fail validation.
All generated claims remain proposals until an identified reviewer accepts
them. Topic/semantic-graph promotion and graph/vector rebuild happen only after
that acceptance.

## Sample

Against a non-KRAIL Python repository, preview reported `mode: onboard`,
detected Python and `pyproject.toml`, recommended `pytest` with that manifest as
evidence, and listed the missing files it would create. A subsequent dry-run
apply wrote the discovery record and bounded work order but launched no process.
No generated profile from that sample is stored in this repository.
