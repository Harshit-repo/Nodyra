# Security Policy

## Trust model (read this first)

Nodyra supports three execution postures. In the default trusted single-tenant
posture, workflow authors are trusted and Code nodes run in warm worker
subprocesses on the host at native speed. In the sandboxed posture
(`EXECUTION_SANDBOX=required` with a configured sandbox runtime), each run
executes in a disposable hardened container. Compose uses `auto` by default,
which can fall back to host subprocesses when the runtime is unavailable;
`auto` alone is not an isolation guarantee. In the multi-tenant posture,
`MULTI_TENANCY_ENABLED=true` requires
`EXECUTION_SANDBOX=required` and combines row-level security, org-scoped keys,
quotas/fairness, and per-org sandbox pools.

**Operator responsibilities**

- Require authentication (`AUTH_REQUIRED=true`) and restrict edit/deploy access
  to trusted users via roles (`viewer` / `editor` / `admin` / `owner`).
- Run trusted single-tenant deployments on hosts you treat as running your
  team's own code.
- Require `EXECUTION_SANDBOX=required` and verify the sandbox runtime whenever
  workflows execute untrusted code. Review AI-generated code before execution.
- Use the unsafe-node policy (`UNSAFE_NODE_POLICY=warn|require_approval|block`)
  to gate risky nodes (Code, Execute Command, SSH, SQL-with-expressions, HTTP to
  private IPs) on deployment activation.
- Set a strong `SECRET_KEY` and a non-empty `INTERNAL_API_TOKEN` whenever the
  API is reachable by anything other than your own machine.
- Keep credentials in the encrypted vault; never paste secrets into Code nodes.

## What Nodyra already does

- Credentials are encrypted at rest (per-credential DEK wrapped by a key derived
  from `SECRET_KEY`); the API returns only field names, never plaintext.
- Secret values are redacted from run outputs, logs, run events, and API
  responses.
- New passwords use Argon2id. Existing PBKDF2-HMAC-SHA256 hashes are verified and
  upgraded after successful sign-in; session tokens are HMAC-signed.
- Code-module discovery is **AST-only** — uploaded Python is parsed, never
  executed, in the API process. Execution happens only in the workflow's worker
  process (the same trust boundary as the Code node).
- Artifact storage keys are server-generated and path-traversal checked.

## MCP execution and approvals

Sensitive built-in MCP commands require a short-lived, single-use approval
reviewed with a separate browser session. The grant is bound to the requesting
actor and credential, command, canonical arguments, and workflow revision.
An agent-supplied `approved_by_user` flag is not an authorization decision.
This separates client credentials from review authority; it is not hardware
proof of human presence. Published workflow tools retain their existing
publication and permission checks; cancellation remains immediately available.

The optional [MCP gateway](docs/mcp-gateway.md) governs registered MCP connections
used by managed worker callbacks and authenticated gateway clients. It pins
approved tool schemas/capabilities, restricts arguments, rechecks worker leases,
and stores decisions separately from provider responses. Provider-reported
success is not independent proof that a downstream write happened.

The gateway does not intercept arbitrary Python, direct-URL integration nodes,
or external runner traffic that bypasses managed callbacks. It also does not
freeze separately stored code modules or dependencies. Keep sandbox and network
egress controls in place, restrict workflow authors, and approve new published
versions explicitly. These controls complement the trust model above.

## Pen-test checklist

Before a production release or a material security change, run the checks in
[`docs/security-pentest-checklist.md`](docs/security-pentest-checklist.md). The
checklist maps each abuse area to the automated test lane that should catch a
regression.

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue.
Email the maintainers at **sharma.har97@gmail.com** with
a description, reproduction steps, and impact. We aim to acknowledge within a few
business days and will coordinate a fix and disclosure timeline with you.

Do not include real credentials or customer data in reports.
