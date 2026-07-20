import {
  ArrowRight,
  CheckCircle,
  Circle,
  DownloadSimple,
  Key,
  ListChecks,
  Play,
  RocketLaunch,
  SelectionForeground,
  Sparkle,
  X,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  ACTIVATION_EVENT_NAME,
  activationTelemetryEnabled,
  applyActivationEvent,
  readActivationProgress,
  readActivationTelemetry,
  recordActivationVisit,
  setActivationTelemetryEnabled,
  writeActivationProgress,
  type ActivationProgress,
  type ActivationStepId,
  type ActivationTelemetryEvent,
} from "./activation";
import type { AuthState } from "./types";

type FirstRunStep = {
  id: ActivationStepId;
  title: string;
  body: string;
  icon: typeof Sparkle;
  label: string;
  to: string;
  optional?: boolean;
};

export function firstRunStorageKey(auth: AuthState): string {
  return auth.user?.id ?? (auth.auth_required ? "signed-out" : "local");
}

const STEPS: FirstRunStep[] = [
  {
    id: "template",
    title: "Choose a credential-free template",
    body: "Start with deterministic sample data so setup cannot block the first result.",
    icon: Sparkle,
    label: "Choose template",
    to: "/?starter=datasetref-filter-export",
  },
  {
    id: "run",
    title: "Run with sample data",
    body: "Execute the workflow and wait for a successful terminal result.",
    icon: Play,
    label: "Open workflows",
    to: "/",
  },
  {
    id: "inspect",
    title: "Inspect output or an artifact",
    body: "Preview the produced data and verify its checksum and lineage.",
    icon: SelectionForeground,
    label: "Open artifacts",
    to: "/artifacts",
  },
  {
    id: "edit_rerun",
    title: "Make one edit and rerun",
    body: "Change a parameter, save it, and confirm the second run still succeeds.",
    icon: ListChecks,
    label: "Open workflows",
    to: "/",
  },
  {
    id: "publish",
    title: "Publish a version",
    body: "Review the draft and publish an immutable version for deployment.",
    icon: RocketLaunch,
    label: "Open workflows",
    to: "/",
  },
  {
    id: "credential",
    title: "Connect a real credential",
    body: "Add external access only after the credential-free journey works.",
    icon: Key,
    label: "Open credentials",
    to: "/credentials",
    optional: true,
  },
];

const FUNNEL_EVENTS = [
  ["install_completed", "Install usable"],
  ["first_page", "First page"],
  ["template_selected", "Template selected"],
  ["run_succeeded", "First successful run"],
  ["output_inspected", "Output inspected"],
  ["workflow_published", "Workflow published"],
  ["first_return", "Returned in a new session"],
] as const;

function exportTelemetry(): void {
  const blob = new Blob([JSON.stringify(readActivationTelemetry(), null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "nodyra-activation-milestones.json";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function FirstRunWizard({ auth }: { auth: AuthState }) {
  const scope = useMemo(
    () => firstRunStorageKey(auth),
    [auth.auth_required, auth.user?.id],
  );
  const [progress, setProgress] = useState<ActivationProgress>(() =>
    readActivationProgress(scope),
  );
  const [telemetryEnabled, setTelemetryEnabled] = useState(() =>
    activationTelemetryEnabled(),
  );

  useEffect(() => {
    setProgress(readActivationProgress(scope));
  }, [scope]);

  useEffect(() => {
    recordActivationVisit();
  }, [telemetryEnabled]);

  useEffect(() => {
    function onProgress(event: Event): void {
      const detail = (event as CustomEvent<ActivationTelemetryEvent>).detail;
      if (!detail?.event) return;
      setProgress((current) => {
        const next = applyActivationEvent(current, detail.event, detail.occurred_at);
        writeActivationProgress(scope, next);
        return next;
      });
    }
    window.addEventListener(ACTIVATION_EVENT_NAME, onProgress);
    return () => window.removeEventListener(ACTIVATION_EVENT_NAME, onProgress);
  }, [scope]);

  function setCollapsed(collapsed: boolean): void {
    setProgress((current) => {
      const next = { ...current, collapsed };
      writeActivationProgress(scope, next);
      return next;
    });
  }

  const required = STEPS.filter((step) => !step.optional);
  const completedRequired = required.filter((step) => progress.completed[step.id]).length;
  const complete = completedRequired === required.length;
  const telemetry = telemetryEnabled ? readActivationTelemetry() : [];
  const failureCounts = telemetry.reduce<Record<string, number>>((counts, event) => {
    if (event.event !== "run_failed") return counts;
    const reason = event.failure_reason ?? "unknown";
    counts[reason] = (counts[reason] ?? 0) + 1;
    return counts;
  }, {});

  if (progress.collapsed) {
    return (
      <button
        className="activation-reopen"
        type="button"
        onClick={() => setCollapsed(false)}
        aria-label={`Open activation checklist, ${completedRequired} of ${required.length} complete`}
      >
        <ListChecks size={18} aria-hidden="true" />
        <span>{complete ? "Setup complete" : `${completedRequired}/${required.length} setup`}</span>
      </button>
    );
  }

  return (
    <aside className="activation-checklist" aria-labelledby="activation-title">
      <header className="activation-checklist-head">
        <div>
          <span className="activation-progress-copy">
            {complete ? "Core journey complete" : `${completedRequired} of ${required.length} complete`}
          </span>
          <h2 id="activation-title">Get to a trusted first run</h2>
        </div>
        <button
          className="btn btn-sm btn-ghost"
          type="button"
          onClick={() => setCollapsed(true)}
          aria-label="Collapse activation checklist"
        >
          <X size={14} weight="bold" aria-hidden="true" />
        </button>
      </header>

      <div
        className="activation-progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={required.length}
        aria-valuenow={completedRequired}
        aria-label="Activation progress"
      >
        <span style={{ width: `${(completedRequired / required.length) * 100}%` }} />
      </div>

      <ol className="activation-steps">
        {STEPS.map((step) => {
          const done = Boolean(progress.completed[step.id]);
          const Icon = step.icon;
          return (
            <li key={step.id} className={done ? "is-complete" : ""}>
              <span className="activation-step-status" aria-hidden="true">
                {done ? <CheckCircle size={18} weight="fill" /> : <Circle size={18} />}
              </span>
              <Icon className="activation-step-icon" size={17} aria-hidden="true" />
              <div>
                <strong>
                  {step.title}
                  {step.optional && <small>Optional</small>}
                </strong>
                <p>{step.body}</p>
                {!done && (
                  <Link className="activation-step-link" to={step.to}>
                    {step.label}
                    <ArrowRight size={13} weight="bold" aria-hidden="true" />
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      <footer className="activation-privacy">
        <label>
          <input
            type="checkbox"
            checked={telemetryEnabled}
            onChange={(event) => {
              const enabled = event.target.checked;
              setTelemetryEnabled(enabled);
              setActivationTelemetryEnabled(enabled);
              if (enabled) recordActivationVisit();
            }}
          />
          Store anonymous milestone events in this browser
        </label>
        {telemetryEnabled && (
          <div className="activation-telemetry-tools">
            <details>
              <summary>View local funnel</summary>
              <ol>
                {FUNNEL_EVENTS.map(([eventName, label]) => {
                  const count = telemetry.filter((event) => event.event === eventName).length;
                  return (
                    <li key={eventName}>
                      <span>{label}</span>
                      <strong>{count}</strong>
                    </li>
                  );
                })}
              </ol>
              {Object.keys(failureCounts).length > 0 && (
                <p>
                  Failure categories: {Object.entries(failureCounts)
                    .map(([reason, count]) => `${reason} ${count}`)
                    .join(" · ")}
                </p>
              )}
            </details>
            <button type="button" onClick={exportTelemetry}>
              <DownloadSimple size={13} aria-hidden="true" />
              Export JSON
            </button>
          </div>
        )}
      </footer>
    </aside>
  );
}
