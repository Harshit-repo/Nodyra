import { useEffect, useState } from "react";

import { api } from "./api";

/**
 * What each edition unlocks, shown next to the licence-key field.
 *
 * Before this, a Community user who hit the five-seat cap saw only the error.
 * Nothing in the product said which edition lifted it, or how to get there —
 * the upgrade path existed entirely in the vendor's head. This turns "no" into
 * "here is what would say yes".
 *
 * Data comes from `GET /billing/plans`, which reads the same TIER_DEFAULTS the
 * enforcement code reads, so what is advertised here cannot drift from what is
 * actually granted.
 */

export interface Plan {
  edition: string;
  name: string;
  price_id: string;
  features: string[];
  limits: Record<string, number>;
  current: boolean;
}

/** Human labels for the Feature enum values the API returns. */
const FEATURE_LABELS: Record<string, string> = {
  sandbox: "Sandboxed execution",
  observability: "Metrics and tracing",
  git_sync: "GitOps sync",
  multi_tenancy: "Multi-tenancy",
  sso: "Single sign-on",
  external_kms: "External KMS",
  audit_logs: "Audit logs",
  advanced_rbac: "Custom roles",
  dedicated_pools: "Dedicated runner pools",
};

export function formatCap(value: number): string {
  return value === 0 ? "Unlimited" : String(value);
}

const CAPS: Array<[string, string]> = [
  ["seats", "Seats"],
  ["environments", "Environments"],
  ["runners", "Runners"],
  ["deployments", "Deployments"],
];

export function PlanComparison() {
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listPlans()
      .then((result) => {
        if (!cancelled) setPlans(result);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Could not load plans");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    // Non-blocking: the licence-key field above still works, so a failure here
    // costs the reader a comparison table, not the ability to apply a key.
    return (
      <p className="muted nodyra-plan-error">
        Plan comparison unavailable — {error}
      </p>
    );
  }

  if (!plans) {
    return (
      <div className="nodyra-settings-skeleton" role="status" aria-label="Loading plans">
        <span />
        <span />
        <span />
      </div>
    );
  }

  const current = plans.find((plan) => plan.current);

  return (
    <section className="nodyra-plan-comparison" aria-labelledby="plan-comparison-heading">
      <h3 id="plan-comparison-heading">What each plan includes</h3>
      <div className="nodyra-plan-grid">
        {plans.map((plan) => {
          const missing =
            current && !plan.current
              ? plan.features.filter((f) => !current.features.includes(f))
              : [];
          return (
            <article
              key={plan.edition}
              className={`nodyra-plan-card${plan.current ? " is-current" : ""}`}
              aria-current={plan.current ? "true" : undefined}
            >
              <header>
                <h4>{plan.name}</h4>
                {plan.current && <span className="nodyra-plan-badge">Current plan</span>}
              </header>
              <dl className="nodyra-plan-caps">
                {CAPS.map(([key, label]) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd>{formatCap(plan.limits[key] ?? 0)}</dd>
                  </div>
                ))}
              </dl>
              {missing.length > 0 && (
                <div className="nodyra-plan-unlocks">
                  <span className="nodyra-plan-unlocks-label">Adds</span>
                  <ul>
                    {missing.map((feature) => (
                      <li key={feature}>{FEATURE_LABELS[feature] ?? feature}</li>
                    ))}
                  </ul>
                </div>
              )}
            </article>
          );
        })}
      </div>
      <p className="muted nodyra-plan-footnote">
        Paste a key above to change edition. Keys renew themselves when this
        installation is configured with a licence server; otherwise apply the new
        key before the current one expires.
      </p>
    </section>
  );
}
