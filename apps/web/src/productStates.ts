export type ProductStateDomain =
  | "run"
  | "workflow"
  | "deployment"
  | "environment"
  | "queue"
  | "license";

export type ProductStateTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger";

export interface ProductStateDefinition {
  label: string;
  explanation: string;
  tone: ProductStateTone;
  icon: "circle" | "clock" | "play" | "check" | "pause" | "warning" | "x";
  next_actions: string[];
  terminal: boolean;
  retryable: boolean;
  accessibility_text: string;
}

type StateContract = Record<ProductStateDomain, Record<string, ProductStateDefinition>>;

function state(
  label: string,
  explanation: string,
  tone: ProductStateTone,
  icon: ProductStateDefinition["icon"],
  nextActions: string[],
  terminal: boolean,
  retryable: boolean,
): ProductStateDefinition {
  return {
    label,
    explanation,
    tone,
    icon,
    next_actions: nextActions,
    terminal,
    retryable,
    accessibility_text: `${label}. ${explanation}`,
  };
}

export const PRODUCT_STATE_CONTRACT: StateContract = {
  run: {
    pending: state("Pending", "Execution has been created but is not yet queued.", "neutral", "clock", ["Wait for dispatch", "Cancel run"], false, false),
    queued: state("Queued", "Waiting for an eligible worker.", "info", "clock", ["Inspect queue position", "Cancel run"], false, false),
    running: state("Running", "A worker is executing the workflow.", "info", "play", ["Follow live events", "Cancel run"], false, false),
    waiting: state("Needs approval", "Execution is paused until an approval is resolved.", "warning", "pause", ["Review request", "Approve or reject"], false, false),
    cancelling: state("Cancelling", "The cancellation request is being applied.", "warning", "clock", ["Wait for terminal status"], false, false),
    success: state("Succeeded", "Every required node completed successfully.", "success", "check", ["Inspect output", "View artifacts"], true, false),
    error: state("Failed", "Execution stopped because a node or runtime operation failed.", "danger", "x", ["Inspect failed node", "Retry run"], true, true),
    failed: state("Failed", "Execution stopped because a node or runtime operation failed.", "danger", "x", ["Inspect failed node", "Retry run"], true, true),
    timed_out: state("Timed out", "The configured execution deadline was exceeded.", "danger", "clock", ["Inspect the slow node", "Adjust timeout and retry"], true, true),
    cancelled: state("Cancelled", "Execution was stopped before completion.", "neutral", "x", ["Run again when ready"], true, true),
    skipped: state("Skipped", "Execution was intentionally not run.", "neutral", "circle", ["Inspect trigger or branch conditions"], true, false),
    cached: state("Cached", "A valid result was reused without executing this node.", "success", "check", ["Inspect cached output"], true, false),
  },
  workflow: {
    active: state("Active", "The published version can receive production triggers.", "success", "play", ["Monitor executions", "Pause workflow"], false, false),
    published: state("Published", "An immutable workflow version is available for deployment.", "success", "check", ["Create deployment", "Monitor executions"], true, false),
    draft: state("Draft changes", "Unpublished edits differ from the latest published version.", "warning", "circle", ["Review changes", "Publish changes"], false, false),
    inactive: state("Inactive", "Production triggers are paused.", "neutral", "pause", ["Review published version", "Activate workflow"], false, false),
    error: state("Attention required", "The latest workflow run or trigger has failed.", "danger", "warning", ["Inspect executions", "Resolve trigger errors"], false, true),
  },
  deployment: {
    active: state("Active", "The deployment accepts configured triggers.", "success", "play", ["Monitor runs", "Review pinned version"], false, false),
    paused: state("Paused", "The deployment is retained but does not accept new triggers.", "neutral", "pause", ["Resume deployment"], false, false),
    pending: state("Preparing", "Deployment configuration is being reconciled.", "info", "clock", ["Wait for readiness", "Inspect events"], false, false),
    error: state("Degraded", "The deployment could not reach its desired state.", "danger", "warning", ["Inspect deployment events", "Retry reconciliation"], false, true),
    not_deployed: state("Not deployed", "No production deployment exists for this workflow.", "neutral", "circle", ["Publish workflow", "Create deployment"], true, false),
  },
  environment: {
    ready: state("Ready", "The environment can accept workflow runs.", "success", "check", ["Run workflow"], false, false),
    provisioning: state("Provisioning", "Environment resources are being prepared.", "info", "clock", ["Wait for readiness", "Inspect events"], false, false),
    degraded: state("Degraded", "Some environment checks are failing.", "warning", "warning", ["Review readiness checks", "Apply remediation"], false, true),
    error: state("Unavailable", "The environment cannot accept runs.", "danger", "x", ["Review health checks", "Retry provisioning"], false, true),
    archived: state("Archived", "The environment is retained for history only.", "neutral", "pause", ["Create a replacement environment"], true, false),
  },
  queue: {
    healthy: state("Healthy", "Queue latency and worker capacity are within target.", "success", "check", ["Continue monitoring"], false, false),
    backlogged: state("Backlogged", "Runs are waiting longer than the queue target.", "warning", "clock", ["Inspect worker capacity", "Review fairness limits"], false, true),
    degraded: state("Degraded", "Queue processing is slower or less reliable than expected.", "warning", "warning", ["Inspect workers", "Review queue errors"], false, true),
    stalled: state("Stalled", "No eligible worker is making queue progress.", "danger", "x", ["Restore worker capacity", "Review leases"], false, true),
    draining: state("Draining", "New work is paused while queued runs finish or migrate.", "info", "pause", ["Monitor remaining work", "End drain when safe"], false, false),
  },
  license: {
    community: state("Community", "Community capabilities and limits are active.", "neutral", "circle", ["Review included capabilities"], false, false),
    trial: state("Trial", "Time-limited evaluation capabilities are active.", "info", "clock", ["Review trial end date", "Choose a plan"], false, false),
    active: state("Licensed", "The installed license is valid.", "success", "check", ["Review renewal date"], false, false),
    grace: state("Grace period", "The license needs attention before restricted mode begins.", "warning", "warning", ["Update license", "Contact support"], false, true),
    expired: state("Expired", "Licensed capabilities are restricted until renewal.", "danger", "x", ["Renew or replace license"], true, true),
    invalid: state("Invalid", "The license could not be verified.", "danger", "x", ["Check license source", "Replace license"], true, true),
  },
};

const UNKNOWN_STATE = state(
  "Unknown",
  "The service returned a state this web version does not recognize.",
  "warning",
  "warning",
  ["Refresh", "Check version compatibility"],
  false,
  true,
);

export function productState(
  domain: ProductStateDomain,
  value: string | null | undefined,
): ProductStateDefinition {
  if (!value) return UNKNOWN_STATE;
  return PRODUCT_STATE_CONTRACT[domain][value.toLowerCase()] ?? UNKNOWN_STATE;
}
