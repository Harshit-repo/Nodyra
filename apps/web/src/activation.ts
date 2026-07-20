import { safeGetItem, safeSetItem } from "./safeStorage";

export const ACTIVATION_EVENT_NAME = "nodyra:activation-progress";

export type ActivationEventName =
  | "install_completed"
  | "first_page"
  | "first_return"
  | "template_selected"
  | "run_succeeded"
  | "run_failed"
  | "output_inspected"
  | "workflow_edited"
  | "workflow_published"
  | "credential_connected";

export type ActivationFailureReason =
  | "capacity"
  | "configuration"
  | "credential"
  | "network"
  | "permission"
  | "runtime"
  | "unknown";

export type ActivationStepId =
  | "template"
  | "run"
  | "inspect"
  | "edit_rerun"
  | "publish"
  | "credential";

export interface ActivationProgress {
  version: 2;
  completed: Partial<Record<ActivationStepId, string>>;
  successfulRuns: number;
  editedAfterRun: boolean;
  collapsed: boolean;
}

export interface ActivationTelemetryEvent {
  event: ActivationEventName;
  occurred_at: string;
  failure_reason?: ActivationFailureReason;
}

const STORAGE_PREFIX = "nodyra-activation-v2";
const TELEMETRY_ENABLED_KEY = "nodyra-activation-telemetry-enabled";
const TELEMETRY_LOG_KEY = "nodyra-activation-telemetry-log-v1";
const TELEMETRY_VISIT_KEY = "nodyra-activation-telemetry-visit-v1";
const TELEMETRY_SESSION_KEY = "nodyra-activation-session-v1";
const MAX_LOCAL_EVENTS = 100;

function emptyProgress(): ActivationProgress {
  return {
    version: 2,
    completed: {},
    successfulRuns: 0,
    editedAfterRun: false,
    collapsed: false,
  };
}

export function activationStorageKey(scope: string): string {
  return `${STORAGE_PREFIX}:${scope}`;
}

export function readActivationProgress(scope: string): ActivationProgress {
  try {
    const raw = safeGetItem(activationStorageKey(scope));
    if (!raw) return emptyProgress();
    const parsed = JSON.parse(raw) as Partial<ActivationProgress>;
    if (parsed.version !== 2) return emptyProgress();
    return {
      version: 2,
      completed: parsed.completed ?? {},
      successfulRuns: Math.max(0, parsed.successfulRuns ?? 0),
      editedAfterRun: Boolean(parsed.editedAfterRun),
      collapsed: Boolean(parsed.collapsed),
    };
  } catch {
    return emptyProgress();
  }
}

export function writeActivationProgress(
  scope: string,
  progress: ActivationProgress,
): void {
  safeSetItem(activationStorageKey(scope), JSON.stringify(progress));
}

function completedAt(
  completed: ActivationProgress["completed"],
  step: ActivationStepId,
  occurredAt: string,
): ActivationProgress["completed"] {
  return completed[step]
    ? completed
    : { ...completed, [step]: occurredAt };
}

export function applyActivationEvent(
  progress: ActivationProgress,
  event: ActivationEventName,
  occurredAt = new Date().toISOString(),
): ActivationProgress {
  let completed = progress.completed;
  let successfulRuns = progress.successfulRuns;
  let editedAfterRun = progress.editedAfterRun;

  if (event === "template_selected") {
    completed = completedAt(completed, "template", occurredAt);
  } else if (event === "run_succeeded") {
    successfulRuns += 1;
    completed = completedAt(completed, "run", occurredAt);
    if (successfulRuns >= 2 && editedAfterRun) {
      completed = completedAt(completed, "edit_rerun", occurredAt);
    }
  } else if (event === "output_inspected") {
    completed = completedAt(completed, "inspect", occurredAt);
  } else if (event === "workflow_edited") {
    if (successfulRuns > 0) editedAfterRun = true;
  } else if (event === "workflow_published") {
    completed = completedAt(completed, "publish", occurredAt);
  } else if (event === "credential_connected") {
    completed = completedAt(completed, "credential", occurredAt);
  }

  return {
    ...progress,
    completed,
    successfulRuns,
    editedAfterRun,
  };
}

export function activationTelemetryEnabled(): boolean {
  return safeGetItem(TELEMETRY_ENABLED_KEY) === "1";
}

export function setActivationTelemetryEnabled(enabled: boolean): void {
  safeSetItem(TELEMETRY_ENABLED_KEY, enabled ? "1" : "0");
}

export function readActivationTelemetry(): ActivationTelemetryEvent[] {
  try {
    const parsed = JSON.parse(safeGetItem(TELEMETRY_LOG_KEY) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is ActivationTelemetryEvent =>
        Boolean(item) &&
        typeof item === "object" &&
        typeof (item as ActivationTelemetryEvent).event === "string" &&
        typeof (item as ActivationTelemetryEvent).occurred_at === "string",
    );
  } catch {
    return [];
  }
}

function appendTelemetry(event: ActivationTelemetryEvent): void {
  if (!activationTelemetryEnabled()) return;
  const next = [...readActivationTelemetry(), event].slice(-MAX_LOCAL_EVENTS);
  safeSetItem(TELEMETRY_LOG_KEY, JSON.stringify(next));
}

export function recordActivationEvent(
  event: ActivationEventName,
  failureReason?: ActivationFailureReason,
): void {
  const detail: ActivationTelemetryEvent = {
    event,
    occurred_at: new Date().toISOString(),
    ...(event === "run_failed" ? { failure_reason: failureReason ?? "unknown" } : {}),
  };
  appendTelemetry(detail);
  window.dispatchEvent(
    new CustomEvent<ActivationTelemetryEvent>(ACTIVATION_EVENT_NAME, { detail }),
  );
}

export function classifyActivationFailure(value: unknown): ActivationFailureReason {
  const message = typeof value === "string" ? value.toLowerCase() : "";
  if (/credential|secret|api[ _-]?key|oauth|token/.test(message)) return "credential";
  if (/permission|forbidden|unauthorized|denied|\b401\b|\b403\b/.test(message)) return "permission";
  if (/network|dns|socket|connect|timeout|timed out|\b5\d\d\b/.test(message)) return "network";
  if (/capacity|quota|no eligible worker|queue|overload/.test(message)) return "capacity";
  if (/config|parameter|validation|invalid|required|missing/.test(message)) return "configuration";
  if (message) return "runtime";
  return "unknown";
}

export function recordActivationVisit(): void {
  if (!activationTelemetryEnabled()) return;

  const seen = safeGetItem(TELEMETRY_VISIT_KEY);
  let sessionSeen = false;
  try {
    sessionSeen = window.sessionStorage.getItem(TELEMETRY_SESSION_KEY) === "1";
    window.sessionStorage.setItem(TELEMETRY_SESSION_KEY, "1");
  } catch {
    // Session storage is optional; local milestones still remain available.
  }

  if (!seen) {
    safeSetItem(TELEMETRY_VISIT_KEY, new Date().toISOString());
    recordActivationEvent("install_completed");
    recordActivationEvent("first_page");
  } else if (!sessionSeen) {
    recordActivationEvent("first_return");
  }
}
