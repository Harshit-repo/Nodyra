# Security Policy

## Trust model (read this first)

Noodle is designed for **single-tenant, self-hosted deployments with trusted
workflow authors**. Code nodes and uploaded code modules execute **arbitrary
Python** inside warm, long-lived worker processes on the Noodle host. Anyone who
can add or edit a workflow can therefore run code with the privileges of the
worker process: read host files, open outbound network connections, and use any
installed package.

This is deliberate — it is what makes Noodle a fast, Python-native automation
platform — and it is the correct model for a team running its own instance. It
is **not** safe to expose Noodle as a multi-tenant service to untrusted users.

**Operator responsibilities**

- Require authentication (`AUTH_REQUIRED=true`) and restrict edit/deploy access
  to trusted users via roles (`viewer` / `editor` / `admin` / `owner`).
- Run Noodle on hosts you treat as running your team's own code.
- Use the unsafe-node policy (`UNSAFE_NODE_POLICY=warn|require_approval|block`)
  to gate risky nodes (Code, Execute Command, SSH, SQL-with-expressions, HTTP to
  private IPs) on deployment activation.
- Set a strong `SECRET_KEY` and a non-empty `INTERNAL_API_TOKEN` whenever the
  API is reachable by anything other than your own machine.
- Keep credentials in the encrypted vault; never paste secrets into Code nodes.

**Out of scope for v1:** untrusted / multi-tenant execution. Per-run disposable
isolation (containers, gVisor, Firecracker) is the intended role of the
remote-runner seam (`packages/runner` + `app/services/remote_dispatch.py`), so a
runner pool can later execute in its own sandbox without giving up the warm-pool
performance model for trusted local runs.

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
