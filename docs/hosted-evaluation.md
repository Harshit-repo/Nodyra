# Disposable Hosted Evaluation Profile

Nodyra includes a Helm overlay for one evaluator per dedicated namespace. It is
a deployment contract for a hosted trial service, not a claim that a public
service is currently running.

## Provisioning contract

The provisioning service must create a fresh namespace, an externally managed
runtime secret, TLS hostname/certificate, managed data-service credentials, and
an RFC 3339 expiry. Example:

```bash
helm upgrade --install nodyra-eval deploy/helm/nodyra \
  --namespace "$EVAL_NAMESPACE" --create-namespace \
  -f deploy/helm/nodyra/values-evaluation.yaml \
  --set image.repository=ghcr.io/example/nodyra-api \
  --set image.tag=0.1.0 \
  --set api.corsOrigins="https://$EVAL_HOST" \
  --set api.publicApiUrl="https://$EVAL_HOST" \
  --set ingress.host="$EVAL_HOST" \
  --set evaluation.expiresAt="$EXPIRES_AT" \
  --atomic --timeout 10m
```

The overlay enforces namespace resource quotas, per-container defaults, one API
and worker replica, TLS, authentication, and egress isolation. Public IPv4 is
allowed while loopback, link-local, CGNAT, multicast, and RFC1918 ranges are
blocked; same-namespace data services and DNS remain reachable. Add private
provider CIDRs only after review.

## Abuse and cost controls

- Require verified email or an invitation before provisioning; rate-limit by
  account and organization, not IP alone.
- Use `EXECUTION_SANDBOX=required`, bounded runtime/output/artifact limits, the
  Community seat/deployment caps, one runner, and no privileged containers.
- Disable registry installs unless the hosted service supplies trusted
  publisher roots and an isolated environment-builder budget.
- Alert at 50%, 75%, and 90% of namespace CPU, memory, storage, and outbound
  transfer budgets. Stop new runs before infrastructure limits cause data loss.
- Never place two unrelated evaluators in the same single-tenant namespace.

## Expiry and deletion guarantee

The chart writes `nodyra.io/expires-at` and
`nodyra.io/delete-policy=delete-namespace` on a lifecycle ConfigMap. Kubernetes
does not act on those annotations: the hosting platform must run a reaper at
least every five minutes.

At expiry, block login and new runs, allow a short export window, drain/cancel
active work, delete the namespace and its per-evaluation database/object-store
prefixes, then verify they no longer exist. Keep only redacted billing/security
events under the published retention policy. Page when deletion is not verified
within one hour; never silently extend an expired environment.

Track invite→workspace, workspace→first successful run, time-to-value,
template/import source, week-1 return, resource cost, and deletion SLO. The
self-hosted and hosted paths use the same activation definitions.
