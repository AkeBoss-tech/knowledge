# Security Policy

KRAIL 1.x security fixes should land on the default development branch first.
The supported v1 boundary covers the published `krail` and `rail-mcp` local
runtime packages on Python 3.11 and newer. `packages/api/`, `packages/engine/`,
hosted deployments, and experimental tools are not part of the v1 security
support promise.

## Security Boundary

KRAIL's v1 security story is intentionally narrow:

- KRAIL can enforce repo-backed access policy when callers go through its CLI,
  SDK, API, MCP server, workflow runtime, and launched runner adapters.
- Records are public by default through those surfaces unless they opt into
  restrictive metadata such as `visibility`, `allowed_*`, `denied_*`, or
  path/tool/secret scope.
- Denied access and allowed access to restricted or sensitive repo records are
  written to `research_plan/audit/access.jsonl` when KRAIL evaluates project
  policy.
- MCP/session scope can narrow write paths, tool names, and secret names for a
  launched runner. That scope is declarative and auditable.

KRAIL does not provide:

- host-level sandboxing, container isolation, or kernel-level policy
- protection against someone who already has direct shell or filesystem access
- guarantees that third-party CLIs, scripts, or model outputs are safe to run
- a managed remote security perimeter

## Reporting a Vulnerability

Do not open a public issue for secrets, credential exposure, KRAIL permission
bypasses, runner-scope bypasses, or remote execution risks.

Report privately to the repository owner through their preferred security
contact. If GitHub private vulnerability reporting is enabled, use that channel.

Include:

- affected commit, tag, or release
- reproduction steps
- impact and affected component
- whether credentials, private data, or remote execution are involved

## Secret Handling

KRAIL should never require committed secrets. Use `.env` locally and scoped
project secrets when a future remote mode is introduced.

Never commit:

- `.env`
- provider API keys
- GitHub App private keys
- OpenAI, Anthropic, Google, FRED, or other provider credentials
- private key files such as `*.pem`, `*.key`, `*.p12`, or `*.pfx`

If a secret may have been committed:

1. Revoke or rotate it at the provider.
2. Remove it from the current tree.
3. Inspect Git history before publishing.
4. Rewrite history only on branches where every collaborator agrees.

## Local Execution Risks

KRAIL can dispatch local CLI agents and run project code. Treat untrusted
projects as executable code:

- review `rail.yaml`, scripts, captures, and agent prompts before running
- prefer `--dry-run` before dispatching workers
- keep `RAIL_EXECUTE_ENABLED=false` unless Python execution is needed
- do not inject broad secret sets into agent sessions
- run pilots in a separate working directory or VM if you need stronger host
  isolation than KRAIL itself provides

## Public Release Checks

Before making a release:

```bash
git status --short
git grep -n -I -E 'AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|PRIVATE KEY' -- ':!**/package-lock.json'
git ls-files pilots generated_projects
```
