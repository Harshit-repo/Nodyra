import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, errorMessage } from "./api";
import { useConfirm } from "./ConfirmProvider";
import { useEntitlements } from "./entitlements";
import {
  useCreateDeploymentMutation,
  useDeleteDeploymentMutation,
  useDeploymentRuns,
  useDeployments,
  useRunDeploymentMutation,
  useUpdateDeploymentMutation,
  useWorkflows,
  useWorkflowVersions,
} from "./queries";
import { useToast } from "./ToastProvider";
import { useModalA11y } from "./useModalA11y";
import type { Deployment, WorkflowSummary } from "./types";

interface UnsafeFinding {
  node_id: string;
  node_type: string;
  kind: string;
  detail?: string;
}
interface UnsafePromptState {
  deployment: Deployment;
  policy: string;
  findings: UnsafeFinding[];
  message: string;
}

function when(iso: string | null): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleString();
}

function describeSchedule(d: Deployment): string {
  if (d.schedule_cron) {
    return `cron: ${d.schedule_cron}${d.schedule_tz ? ` (${d.schedule_tz})` : ""}`;
  }
  const every = d.schedule_every || 1;
  return `every ${every} ${d.schedule_interval || "hours"}`;
}

function runStatusClass(status: string): string {
  if (status === "success") return "status-run-success";
  if (status === "error") return "status-run-error";
  if (status === "running" || status === "queued" || status === "waiting")
    return "status-run-skipped";
  return "status-run-skipped";
}

function DeploymentHistory({
  deploymentId,
  onOpenRun,
}: {
  deploymentId: string;
  onOpenRun: (runId: string) => void;
}) {
  const runsQuery = useDeploymentRuns(deploymentId, { refetchInterval: 5000 });
  const runs = (runsQuery.data ?? []).slice(0, 8);
  const recent = runsQuery.data ?? [];
  const finished = recent.filter(
    (r) => r.status === "success" || r.status === "error",
  );
  const successRate = finished.length
    ? Math.round(
        (finished.filter((r) => r.status === "success").length /
          finished.length) *
          100,
      )
    : null;

  return (
    <div className="deploy-history">
      <div className="deploy-history-head">
        <span className="muted">Recent runs</span>
        {successRate != null && (
          <span className="muted">
            {successRate}% success over last {finished.length}
          </span>
        )}
      </div>
      {runsQuery.isLoading && <p className="muted">Loading…</p>}
      {!runsQuery.isLoading && runs.length === 0 && (
        <p className="muted">No runs yet.</p>
      )}
      {runs.map((r) => (
        <button
          type="button"
          key={r.id}
          className="deploy-run-row"
          onClick={() => onOpenRun(r.id)}
          title="Open in Executions"
        >
          <span className={`run-pill ${runStatusClass(r.status)}`}>
            {r.status}
          </span>
          <span className="muted">{r.trigger_type}</span>
          <span className="muted">
            {r.started_at ? when(r.started_at) : "—"}
          </span>
        </button>
      ))}
    </div>
  );
}

export function DeploymentsPage() {
  const navigate = useNavigate();
  const [editing, setEditing] = useState<Deployment | null>(null);
  const [creating, setCreating] = useState(false);
  const [unsafePrompt, setUnsafePrompt] = useState<UnsafePromptState | null>(null);
  const [historyId, setHistoryId] = useState<string | null>(null);
  // Deployment ids with an action (run / toggle) in flight, so their row
  // controls disable and can't be double-fired while the request is pending.
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const { notify } = useToast();
  const confirm = useConfirm();
  const deploymentsQuery = useDeployments();
  const workflowsQuery = useWorkflows();
  const runDeploymentMutation = useRunDeploymentMutation();
  const updateDeploymentMutation = useUpdateDeploymentMutation();
  const deleteDeploymentMutation = useDeleteDeploymentMutation();
  const deployments = deploymentsQuery.data ?? null;
  const workflows = workflowsQuery.data ?? [];
  const ent = useEntitlements();
  const atActiveCap = ent.atLimit(
    "deployments",
    deployments?.filter((d) => d.active).length ?? 0,
  );
  const error =
    deploymentsQuery.isError && !deploymentsQuery.data
      ? errorMessage(deploymentsQuery.error)
      : "";

  function setBusy(id: string, busy: boolean): void {
    setBusyIds((current) => {
      const next = new Set(current);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function runNow(d: Deployment): Promise<void> {
    if (busyIds.has(d.id)) return;
    setBusy(d.id, true);
    try {
      const { run_id } = await runDeploymentMutation.mutateAsync(d.id);
      notify("Deployment run started.", "success");
      navigate(`/executions?run=${run_id}`);
    } catch (err) {
      notify(`Could not start deployment. ${errorMessage(err)}`, "error");
    } finally {
      setBusy(d.id, false);
    }
  }

  async function toggleActive(
    d: Deployment,
    active: boolean,
    approveUnsafe = false,
  ): Promise<void> {
    setBusy(d.id, true);
    try {
      await updateDeploymentMutation.mutateAsync({
        id: d.id,
        body: {
          active,
          ...(approveUnsafe ? { approve_unsafe_nodes: true } : {}),
        },
      });
      notify(active ? "Deployment activated." : "Deployment paused.", "success");
      setUnsafePrompt(null);
    } catch (err) {
      if (
        active &&
        err instanceof ApiError &&
        err.status === 409 &&
        err.detail &&
        typeof err.detail === "object" &&
        Array.isArray((err.detail as { findings?: unknown }).findings)
      ) {
        const detail = err.detail as {
          message?: string;
          policy?: string;
          findings: UnsafeFinding[];
        };
        setUnsafePrompt({
          deployment: d,
          policy: detail.policy ?? "require_approval",
          findings: detail.findings,
          message:
            detail.message ??
            "Workflow contains risky nodes; explicit approval is required.",
        });
        return;
      }
      notify(`Could not update deployment. ${errorMessage(err)}`, "error");
    } finally {
      setBusy(d.id, false);
    }
  }

  async function remove(d: Deployment): Promise<void> {
    const ok = await confirm({
      title: "Delete deployment?",
      body: `“${d.name}” will be removed and will stop firing on its schedule.`,
    });
    if (!ok) return;
    try {
      await deleteDeploymentMutation.mutateAsync(d.id);
      notify("Deployment deleted.", "success");
    } catch (err) {
      notify(`Could not delete deployment. ${errorMessage(err)}`, "error");
    }
  }

  const workflowName = (id: string): string =>
    workflows.find((w) => w.id === id)?.name ?? "(deleted workflow)";

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Deployments
            {deployments && <span className="home-count">{deployments.length}</span>}
          </h1>
          <button
            type="button"
            className="btn"
            onClick={() => setCreating(true)}
            disabled={workflows.length === 0}
          >
            + New deployment
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}
        {!deployments && !error && (
          <div className="deploy-list" aria-label="Loading deployments">
            {Array.from({ length: 4 }).map((_, index) => (
              <div className="deploy-row skeleton-row" key={index}>
                <div className="deploy-main">
                  <span className="skeleton-line short" />
                  <span className="skeleton-line" />
                </div>
                <span className="skeleton-line tiny" />
              </div>
            ))}
          </div>
        )}

        {deployments && deployments.length === 0 && (
          <div className="empty-state">
            <h2>No deployments yet</h2>
            <p className="muted">Create one from a published workflow.</p>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setCreating(true)}
              disabled={workflows.length === 0}
            >
              New deployment
            </button>
          </div>
        )}

        {deployments && deployments.length > 0 && (
          <div className="deploy-list">
            {deployments.map((d) => (
              <div className="deploy-item" key={d.id}>
                <div className="deploy-row">
                  <div className="deploy-main">
                    <div className="deploy-name" title={d.name}>
                      {d.name}
                      <span
                        className={`run-pill ${
                          d.active ? "status-run-success" : "status-run-skipped"
                        }`}
                      >
                        {d.active ? "active" : "paused"}
                      </span>
                    </div>
                    <div className="deploy-meta">
                      <Link to={`/workflows/${d.workflow_id}`}>
                        {workflowName(d.workflow_id)}
                      </Link>
                      {" · "}
                      {describeSchedule(d)}
                      {d.workflow_version ? ` · pinned v${d.workflow_version}` : ""}
                      {d.error_workflow_id
                        ? ` · error → ${workflowName(d.error_workflow_id)}`
                        : ""}
                      {" · last fired "}
                      {when(d.last_fired)}
                    </div>
                  </div>
                  <div className="deploy-actions">
                    <label
                      className="active-toggle"
                      title={
                        !d.active && atActiveCap
                          ? `Active-deployment limit reached on the ${ent.edition} edition — upgrade to activate more.`
                          : undefined
                      }
                    >
                      <input
                        type="checkbox"
                        checked={d.active}
                        disabled={busyIds.has(d.id) || (!d.active && atActiveCap)}
                        onChange={(e) => void toggleActive(d, e.target.checked)}
                      />
                      <span className="active-track" />
                    </label>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => void runNow(d)}
                      disabled={busyIds.has(d.id)}
                    >
                      ▶ Run now
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={() =>
                        setHistoryId((cur) => (cur === d.id ? null : d.id))
                      }
                    >
                      {historyId === d.id ? "Hide" : "History"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={() => setEditing(d)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={() => void remove(d)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
                {historyId === d.id && (
                  <DeploymentHistory
                    deploymentId={d.id}
                    onOpenRun={(runId) => navigate(`/executions?run=${runId}`)}
                  />
                )}
              </div>
            ))}
          </div>
        )}

        {(creating || editing) && (
          <DeploymentDialog
            initial={editing}
            workflows={workflows}
            onClose={() => {
              setCreating(false);
              setEditing(null);
            }}
            onSaved={() => {
              setCreating(false);
              setEditing(null);
              void deploymentsQuery.refetch();
            }}
          />
        )}
        {unsafePrompt && (
          <UnsafeNodesDialog
            state={unsafePrompt}
            onCancel={() => setUnsafePrompt(null)}
            onApprove={() =>
              void toggleActive(unsafePrompt.deployment, true, true)
            }
          />
        )}
      </main>
    </div>
  );
}

function UnsafeNodesDialog({
  state,
  onCancel,
  onApprove,
}: {
  state: UnsafePromptState;
  onCancel: () => void;
  onApprove: () => void;
}) {
  const blocked = state.policy === "block";
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onCancel);
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="unsafe-nodes-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="unsafe-nodes-title">Risky nodes detected</h2>
        <p className="muted">{state.message}</p>
        <ul className="ops-warnings">
          {state.findings.map((f) => (
            <li key={f.node_id}>
              <strong>{f.node_type}</strong>
              {" · "}
              <span className="muted">{f.kind}</span>
              {f.detail ? <> — {f.detail}</> : null}
            </li>
          ))}
        </ul>
        <p className="muted" style={{ marginTop: 12 }}>
          Policy: <code>{state.policy}</code>
        </p>
        <div className="modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          {!blocked && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={onApprove}
            >
              Approve and activate
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function DeploymentDialog({
  initial,
  workflows,
  onClose,
  onSaved,
}: {
  initial: Deployment | null;
  workflows: WorkflowSummary[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [workflowId, setWorkflowId] = useState(
    initial?.workflow_id ?? workflows[0]?.id ?? "",
  );
  const [name, setName] = useState(initial?.name ?? "");
  const [cron, setCron] = useState(initial?.schedule_cron ?? "");
  const [interval, setInterval] = useState(
    initial?.schedule_interval ?? "hours",
  );
  const [every, setEvery] = useState(initial?.schedule_every ?? 1);
  const [tz, setTz] = useState(initial?.schedule_tz ?? "");
  const [workflowVersionId, setWorkflowVersionId] = useState(
    initial?.workflow_version_id ?? "",
  );
  const [errorWorkflowId, setErrorWorkflowId] = useState(
    initial?.error_workflow_id ?? "",
  );
  const [paramsText, setParamsText] = useState(
    JSON.stringify(initial?.default_parameters ?? {}, null, 2),
  );
  const [active, setActive] = useState(initial?.active ?? false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const { notify } = useToast();
  const createDeploymentMutation = useCreateDeploymentMutation();
  const updateDeploymentMutation = useUpdateDeploymentMutation();
  const versionsQuery = useWorkflowVersions(workflowId || null);
  const versions = [...(versionsQuery.data ?? [])].sort(
    (a, b) => b.version - a.version,
  );
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  async function save(): Promise<void> {
    setErr("");
    let parsed: Record<string, unknown>;
    try {
      parsed = paramsText.trim() ? JSON.parse(paramsText) : {};
    } catch {
      setErr("Default parameters must be valid JSON.");
      return;
    }
    if (!name.trim()) {
      setErr("Name is required.");
      return;
    }
    setSaving(true);
    try {
      if (initial) {
        await updateDeploymentMutation.mutateAsync({
          id: initial.id,
          body: {
            name: name.trim(),
            schedule_cron: cron,
            schedule_interval: interval,
            schedule_every: Math.max(1, every),
            schedule_tz: tz,
            default_parameters: parsed,
            active,
            workflow_version_id: workflowVersionId.trim() || undefined,
            error_workflow_id: errorWorkflowId.trim() || undefined,
          },
        });
        notify("Deployment updated.", "success");
      } else {
        await createDeploymentMutation.mutateAsync({
          workflow_id: workflowId,
          name: name.trim(),
          schedule_cron: cron,
          schedule_interval: interval,
          schedule_every: Math.max(1, every),
          schedule_tz: tz,
          default_parameters: parsed,
          active,
          workflow_version_id: workflowVersionId.trim() || undefined,
          error_workflow_id: errorWorkflowId.trim() || undefined,
        });
        notify("Deployment created.", "success");
      }
      onSaved();
    } catch (e) {
      setErr(String(e));
      notify("Could not save deployment.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="deployment-dialog-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="deployment-dialog-title">{initial ? "Edit deployment" : "New deployment"}</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="modal-body">
          {err && <p className="error-text">{err}</p>}

          <div className="field">
            <div className="field-label">
              <span className="field-name">Workflow</span>
            </div>
            <select
              className="field-input"
              value={workflowId}
              onChange={(e) => setWorkflowId(e.target.value)}
              disabled={Boolean(initial)}
            >
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Name</span>
            </div>
            <input
              className="field-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Daily 9am Sydney"
            />
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Cron expression</span>
            </div>
            <p className="field-desc">
              Leave blank to use the simple interval below. Examples:
              <code> 0 9 * * *</code> (9am daily), <code>*/15 * * * *</code> (every 15 min).
            </p>
            <input
              className="field-input"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              placeholder="0 9 * * 1-5"
            />
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Timezone</span>
            </div>
            <p className="field-desc">
              IANA name like <code>Australia/Sydney</code>. Blank = app default.
            </p>
            <input
              className="field-input"
              value={tz}
              onChange={(e) => setTz(e.target.value)}
              placeholder="Australia/Sydney"
            />
          </div>

          <div className="field field-row">
            <div>
              <div className="field-label">
                <span className="field-name">Interval (when cron is blank)</span>
              </div>
              <select
                className="field-input"
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
              >
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </select>
            </div>
            <div>
              <div className="field-label">
                <span className="field-name">Every</span>
              </div>
              <input
                className="field-input"
                type="number"
                min={1}
                value={every}
                onChange={(e) => setEvery(parseInt(e.target.value || "1", 10) || 1)}
              />
            </div>
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Default parameters (JSON)</span>
            </div>
            <p className="field-desc">
              Seeded into the first trigger node when the deployment fires.
              Accessible via <code>{"{{ $json.field }}"}</code>.
            </p>
            <textarea
              className="field-input field-code"
              rows={6}
              value={paramsText}
              onChange={(e) => setParamsText(e.target.value)}
              spellCheck={false}
            />
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Pinned version</span>
            </div>
            <p className="field-desc">
              “Latest published” re-pins on each publish. Pick a specific version
              for a controlled rollout.
            </p>
            <select
              className="field-input"
              value={workflowVersionId}
              onChange={(e) => setWorkflowVersionId(e.target.value)}
            >
              <option value="">Latest published</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version}
                  {v.published ? "" : " (draft)"}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Error workflow</span>
            </div>
            <p className="field-desc">
              Optional workflow to run when this deployment fails.
            </p>
            <select
              className="field-input"
              value={errorWorkflowId}
              onChange={(e) => setErrorWorkflowId(e.target.value)}
            >
              <option value="">None</option>
              {workflows
                .filter((w) => w.id !== workflowId)
                .map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
            </select>
          </div>

          <label className="field-toggle">
            <input
              type="checkbox"
              checked={active}
              onChange={(e) => setActive(e.target.checked)}
            />
            <span className="field-toggle-track" />
            <span className="field-toggle-text">
              {active ? "Active — scheduler will fire this" : "Paused"}
            </span>
          </label>
        </div>
        <footer className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn" onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : initial ? "Save changes" : "Create deployment"}
          </button>
        </footer>
      </div>
    </div>
  );
}
