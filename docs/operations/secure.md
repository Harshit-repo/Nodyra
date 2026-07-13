# Secure

## Baseline

- Set `AUTH_REQUIRED=true`.
- Set a long random `SECRET_KEY`.
- Set explicit `CORS_ORIGINS`.
- Use PostgreSQL, Redis, and object storage.
- Set `INTERNAL_API_TOKEN` for split API/worker deployments.
- Disable community registry access in locked-down environments with
  `ALLOW_REGISTRY=false`.

## Secrets And Credentials

Store `.env`, Kubernetes secrets, OAuth client secrets, SIEM webhook secrets,
and database credentials in a secret manager. Do not bake `deploy/.env` into
images. Back up `SECRET_KEY`; losing it makes stored credentials undecryptable.

## Sandboxing

Use `EXECUTION_SANDBOX=required` for multi-tenant or untrusted workflow authors.
The sandbox overlay routes Docker access through a socket proxy. Workers should
not mount the host Docker socket directly when proxy access is configured.

## Egress And SSRF

Keep private egress blocked in multi-tenant deployments. Only set
`NODYRA_ALLOW_PRIVATE_EGRESS=1` for trusted single-tenant automation that must
reach internal services.

## Audit

Use `/audit/export.ndjson` for bulk export and configure `AUDIT_WEBHOOK_URL`
plus `AUDIT_WEBHOOK_SECRET` for signed batched SIEM export. The webhook payload
is signed with `X-Nodyra-Signature: sha256=<hmac>`.

## Validation

Run the security lane and redaction tests before release:

```bash
uv run ruff check .
uv run pytest -q apps/api/tests -k "redaction or tenant_isolation or rate_limit"
```

Use [Security Pen-Test Checklist](../security-pentest-checklist.md) for manual
review.
