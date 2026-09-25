import {
  ArrowSquareOut,
  CheckCircle,
  Info,
  ShieldCheck,
  WarningCircle,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import {
  api,
  errorMessage,
  type ProductionAttestationCheck,
  type RuntimeModeStatus,
} from "./api";
import { queryKeys } from "./queries";

const DEPLOYMENT_DOCS =
  "https://github.com/Harshit-repo/noodle/blob/main/docs/deployment.md";
const ATTESTATION_DOCS =
  "https://github.com/Harshit-repo/noodle/blob/main/docs/operations/production-attestation.md";

type ReadinessFix = {
  title: string;
  fix: string;
  docsHref: string;
  docsLabel: string;
};

type ReadinessItem = ReadinessFix & {
  id: string;
  warning: string;
};

const WARNING_FIXES: Array<{
  match: (warning: string) => boolean;
  copy: ReadinessFix;
}> = [
  {
    match: (warning) => warning.includes("sqlite database_url"),
    copy: {
      title: "SQLite is not production storage",
      fix: "Set DATABASE_URL to PostgreSQL before serving production traffic.",
      docsHref: `${DEPLOYMENT_DOCS}#configuration-flags`,
      docsLabel: "Database docs",
    },
  },
  {
    match: (warning) => warning.includes("local artifact storage"),
    copy: {
      title: "Artifacts are local to this host",
      fix: "Set ARTIFACT_STORAGE_BACKEND=s3 and use shared object storage.",
      docsHref: `${DEPLOYMENT_DOCS}#artifacts`,
      docsLabel: "Artifact docs",
    },
  },
  {
    match: (warning) => warning.includes("artifact_storage_backend=s3"),
    copy: {
      title: "S3 bucket is not configured",
      fix: "Set ARTIFACT_S3_BUCKET before enabling S3-backed artifact uploads.",
      docsHref: `${DEPLOYMENT_DOCS}#artifacts`,
      docsLabel: "Artifact docs",
    },
  },
  {
    match: (warning) =>
      warning.includes("no shared queue backend") ||
      warning.includes("replica_safe=false") ||
      warning.includes("without redis"),
    copy: {
      title: "Replica coordination needs Redis",
      fix: "Set QUEUE_BACKEND=redis so queues, events, caches, and limits are shared.",
      docsHref: `${DEPLOYMENT_DOCS}#queue--dispatch`,
      docsLabel: "Queue docs",
    },
  },
  {
    match: (warning) => warning.includes("webhook_role=inline"),
    copy: {
      title: "Webhooks share the API process",
      fix: "Set WEBHOOK_ROLE=ingress for dedicated, queue-backed webhook intake.",
      docsHref: `${DEPLOYMENT_DOCS}#execution-topology-dispatch_role`,
      docsLabel: "Topology docs",
    },
  },
  {
    match: (warning) => warning.includes("wildcard origin"),
    copy: {
      title: "CORS allows every browser origin",
      fix: "Set CORS_ORIGINS to the exact frontend URLs allowed to call this API.",
      docsHref: `${DEPLOYMENT_DOCS}#auth--security`,
      docsLabel: "Security docs",
    },
  },
  {
    match: (warning) => warning.includes("secret_key is the default"),
    copy: {
      title: "SECRET_KEY is still the development value",
      fix: "Set SECRET_KEY to a strong random secret and restart the API.",
      docsHref: `${DEPLOYMENT_DOCS}#auth--security`,
      docsLabel: "Security docs",
    },
  },
  {
    match: (warning) => warning.includes("auth_required=false"),
    copy: {
      title: "Authentication is disabled",
      fix: "Set AUTH_REQUIRED=true before exposing Nodyra beyond a trusted local host.",
      docsHref: `${DEPLOYMENT_DOCS}#auth--security`,
      docsLabel: "Auth docs",
    },
  },
  {
    match: (warning) => warning.includes("internal_api_token is empty"),
    copy: {
      title: "Worker internal API token is empty",
      fix: "Set INTERNAL_API_TOKEN to a strong shared secret for production deployments.",
      docsHref: `${DEPLOYMENT_DOCS}#auth--security`,
      docsLabel: "Security docs",
    },
  },
  {
    match: (warning) => warning.includes("dispatch_role=disabled"),
    copy: {
      title: "Agent and Kubernetes pools will not dispatch",
      fix: "Use DISPATCH_ROLE=control on API replicas when those runner pools are enabled.",
      docsHref: `${DEPLOYMENT_DOCS}#execution-topology-dispatch_role`,
      docsLabel: "Topology docs",
    },
  },
];

function fallbackFix(): ReadinessFix {
  return {
    title: "Runtime warning",
    fix: "Review the runtime-mode warning and align the deployment settings before production use.",
    docsHref: `${DEPLOYMENT_DOCS}#configuration-flags`,
    docsLabel: "Deployment docs",
  };
}

function readinessFixFor(warning: string): ReadinessFix {
  const normalized = warning.toLowerCase();
  return WARNING_FIXES.find((entry) => entry.match(normalized))?.copy ?? fallbackFix();
}

function readinessItems(status: RuntimeModeStatus): ReadinessItem[] {
  const warnings = new Set(status.warnings);
  if (!status.replica_safe) {
    for (const reason of status.replica_unsafe_reasons) {
      warnings.add(`replica_safe=False: ${reason}. Set queue_backend=redis.`);
    }
  }
  return Array.from(warnings).map((warning, index) => ({
    id: `${index}-${warning}`,
    warning,
    ...readinessFixFor(warning),
  }));
}

function runtimeFacts(status: RuntimeModeStatus): Array<{ label: string; value: string }> {
  return [
    { label: "Mode", value: status.mode },
    { label: "Database", value: status.database_dialect },
    { label: "Queue", value: status.queue_backend },
    { label: "Artifacts", value: status.artifact_backend },
    { label: "Webhooks", value: status.webhook_role },
    { label: "Replicas", value: status.replica_safe ? "safe" : "needs Redis" },
    { label: "Tracing", value: status.otel_enabled ? "enabled" : "off" },
  ];
}

function ReadinessCardFrame({ children }: { children: ReactNode }) {
  return (
    <section
      id="readiness"
      className="nodyra-settings-card"
      aria-labelledby="readiness-title"
    >
      <div className="nodyra-settings-card-head">
        <span className="nodyra-settings-card-icon" aria-hidden="true">
          <ShieldCheck size={18} />
        </span>
        <div>
          <h2 id="readiness-title">Readiness</h2>
          <p>Production posture from the active runtime-mode probe.</p>
        </div>
      </div>
      <div className="nodyra-settings-card-body">{children}</div>
    </section>
  );
}

export function ReadinessPanel() {
  const runtimeQuery = useQuery({
    queryKey: queryKeys.runtimeMode,
    queryFn: api.getRuntimeMode,
    retry: false,
    refetchInterval: 15000,
  });
  const attestationQuery = useQuery({
    queryKey: ["production-attestation"],
    queryFn: api.productionAttestation,
    enabled: runtimeQuery.data?.mode === "production",
    retry: false,
    refetchInterval: 60000,
  });

  if (runtimeQuery.isError) {
    return (
      <ReadinessCardFrame>
        <div className="nodyra-settings-inline-error" role="alert">
          <WarningCircle size={18} aria-hidden="true" />
          <span>{errorMessage(runtimeQuery.error)}</span>
          <button className="btn btn-sm" type="button" onClick={() => void runtimeQuery.refetch()}>
            Retry
          </button>
        </div>
      </ReadinessCardFrame>
    );
  }

  const status = runtimeQuery.data;
  if (!status) {
    return (
      <ReadinessCardFrame>
        <div role="status" className="nodyra-settings-skeleton" aria-label="Loading readiness status">
          <span />
          <span />
          <span />
        </div>
      </ReadinessCardFrame>
    );
  }

  const items = readinessItems(status);
  const attestation = attestationQuery.data;
  const attestationIssues = attestation?.checks.filter((check) => check.status !== "pass") ?? [];
  const isProduction = status.mode === "production";
  const ready = isProduction && (attestation ? attestation.production_ready : items.length === 0);
  const issueCount = attestation ? attestation.summary.failed + attestation.summary.warnings : items.length;
  const postureClass = !isProduction ? "is-local" : ready ? "is-ready" : "is-warning";

  return (
    <ReadinessCardFrame>
      <div className={`nodyra-readiness-state ${postureClass}`}>
        <span className="nodyra-readiness-state-icon" aria-hidden="true">
          {!isProduction ? (
            <Info size={24} />
          ) : ready ? (
            <CheckCircle size={24} />
          ) : (
            <WarningCircle size={24} />
          )}
        </span>
        <div>
          <strong>
            {!isProduction
              ? "Local development mode"
              : ready
                ? "Production-ready"
                : `${issueCount} readiness issue${issueCount === 1 ? "" : "s"}`}
          </strong>
          <span>
            {!isProduction
              ? "This workspace is optimized for local use. Switch to production mode to run the full readiness assessment."
              : ready
                ? "The active production posture reports no readiness issues."
                : "Resolve these settings before relying on this deployment for production traffic."}
          </span>
        </div>
      </div>

      <dl className="nodyra-readiness-facts" aria-label="Runtime mode details">
        {runtimeFacts(status).map((fact) => (
          <div key={fact.label}>
            <dt>{fact.label}</dt>
            <dd>{fact.value}</dd>
          </div>
        ))}
      </dl>

      {status.allow_insecure && (
        <div className="nodyra-settings-inline-warning">
          <Info size={17} aria-hidden="true" />
          RUNTIME_ALLOW_INSECURE is enabled; startup guardrails are being bypassed.
        </div>
      )}

      {attestation && (
        <div className="nodyra-attestation-summary" role="status">
          <span>{attestation.summary.passed} passed</span>
          <span>{attestation.summary.warnings} warnings</span>
          <span>{attestation.summary.failed} failed</span>
          <small>Verified {new Date(attestation.generated_at).toLocaleString()}</small>
        </div>
      )}

      {attestationIssues.length > 0 && (
        <ul className="nodyra-readiness-list" aria-label="Production attestation findings">
          {attestationIssues.map((check: ProductionAttestationCheck) => (
            <li key={check.id} className="nodyra-readiness-item">
              <span className="nodyra-readiness-item-icon" aria-hidden="true">
                <WarningCircle size={18} />
              </span>
              <div>
                <strong>{check.title}</strong>
                <p>{check.remediation}</p>
                <details className="nodyra-attestation-evidence">
                  <summary>Evidence</summary>
                  <code>{JSON.stringify(check.evidence)}</code>
                </details>
              </div>
              <a className="btn btn-sm btn-ghost" href={ATTESTATION_DOCS} target="_blank" rel="noreferrer">
                <ArrowSquareOut size={14} aria-hidden="true" />
                Runbook
              </a>
            </li>
          ))}
        </ul>
      )}

      {!attestation && items.length > 0 && (
        <ul className="nodyra-readiness-list" aria-label="Production readiness warnings">
          {items.map((item) => (
            <li key={item.id} className="nodyra-readiness-item">
              <span className="nodyra-readiness-item-icon" aria-hidden="true">
                <WarningCircle size={18} />
              </span>
              <div>
                <strong>{item.title}</strong>
                <p>{item.warning}</p>
                <small>{item.fix}</small>
              </div>
              <a
                className="btn btn-sm btn-ghost"
                href={item.docsHref}
                target="_blank"
                rel="noreferrer"
              >
                <ArrowSquareOut size={14} aria-hidden="true" />
                {item.docsLabel}
              </a>
            </li>
          ))}
        </ul>
      )}
    </ReadinessCardFrame>
  );
}
