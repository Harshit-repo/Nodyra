# Security Policy

## Trust model (read this first)

Noodle supports three execution postures. In the default trusted single-tenant
posture, workflow authors are trusted and Code nodes run in warm worker
subprocesses on the host at native speed. In the sandboxed posture
(`EXECUTION_SANDBOX=auto|required`), each run executes in a disposable hardened
container. In the multi-tenant posture, `MULTI_TENANCY_ENABLED=true` requires
`EXECUTION_SANDBOX=required` and combines row-level security, org-scoped keys,
quotas/fairness, and per-org sandbox pools.

**Operator responsibilities**

- Require authentication (`AUTH_REQUIRED=true`) and restrict edit/deploy access
  to trusted users via roles (`viewer` / `editor` / `admin` / `owner`).
- Run trusted single-tenant deployments on hosts you treat as running your
  team's own code.
- Enable `EXECUTION_SANDBOX=auto|required` whenever workflows execute
  AI-generated or otherwise untrusted code.
- Use the unsafe-node policy (`UNSAFE_NODE_POLICY=warn|require_approval|block`)
  to gate risky nodes (Code, Execute Command, SSH, SQL-with-expressions, HTTP to
  private IPs) on deployment activation.
- Set a strong `SECRET_KEY` and a non-empty `INTERNAL_API_TOKEN` whenever the
  API is reachable by anything other than your own machine.
- Keep credentials in the encrypted vault; never paste secrets into Code nodes.

## What Noodle already does

- Credentials are encrypted at rest (per-credential DEK wrapped by a key derived
  from `SECRET_KEY`); the API returns only field names, never plaintext.
- Secret values are redacted from run outputs, logs, run events, and API
  responses.
- Passwords use PBKDF2-HMAC-SHA256; session tokens are HMAC-signed.
- Code-module discovery is **AST-only** — uploaded Python is parsed, never
  executed, in the API process. Execution happens only in the workflow's worker
  process (the same trust boundary as the Code node).
- Artifact storage keys are server-generated and path-traversal checked.

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue.
Email the maintainers at **security@<your-domain>** (replace before release) with
a description, reproduction steps, and impact. We aim to acknowledge within a few
business days and will coordinate a fix and disclosure timeline with you.

Do not include real credentials or customer data in reports.
