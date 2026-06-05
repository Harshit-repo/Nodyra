import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { DotsThreeVertical, Rows, SquaresFour } from "@phosphor-icons/react";

import { api } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { HomeHeader } from "./HomeHeader";
import { Logo } from "./Logo";
import { useToast } from "./ToastProvider";
import type {
  Credential,
  Deployment,
  Environment,
  ProviderTriggerStatusCounts,
  ProviderTriggerSubscription,
  WorkflowSummary,
} from "./types";
import { WORKFLOW_TEMPLATES as TEMPLATES } from "./workflowTemplates";

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function providerCounts(wf: WorkflowSummary): ProviderTriggerStatusCounts {
  return (
    wf.provider_trigger_counts ?? {
      total: 0,
      active: 0,
      activating: 0,
      error: 0,
      deleted: 0,
    }
  );
}

function providerStatusBadge(
  counts: ProviderTriggerStatusCounts,
): { className: string; label: string; title: string } | null {
  if (counts.error > 0) {
    return {
      className: "hook-error",
      label: `${counts.error} hook error${counts.error === 1 ? "" : "s"}`,
      title: "Provider trigger error",
    };
  }
  if (counts.activating > 0) {
    return {
      className: "hook-activating",
      label: `${counts.activating} hook activating`,
      title: "Provider trigger activating",
    };
  }
  if (counts.active > 0) {
    return {
      className: "hook-active",
      label: `${counts.active} hook active`,
      title: "Provider trigger active",
    };
  }
  return null;
}

function lastDelivery(
  row: ProviderTriggerSubscription,
): Record<string, unknown> | null {
  const delivery = row.config?.last_delivery;
  if (!delivery || typeof delivery !== "object" || Array.isArray(delivery)) {
    return null;
  }
  return delivery as Record<string, unknown>;
}

function CreateModal({
  onClose,
  onCreated,
  initialTemplateId,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
  initialTemplateId: string;
}) {
  const initialTemplate =
    TEMPLATES.find((item) => item.id === initialTemplateId) ?? TEMPLATES[0];
  const [name, setName] = useState(
    initialTemplate.id === "blank" ? "Untitled workflow" : initialTemplate.name,
  );
  const [templateId, setTemplateId] = useState(initialTemplate.id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { notify } = useToast();

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createWorkflow(name.trim() || "Untitled workflow");
      const template = TEMPLATES.find((item) => item.id === templateId);
      if (template?.graph) {
        await api.updateWorkflow(created.id, { graph: template.graph() });
      }
      notify(
        template?.id === "blank" ? "Workflow created." : "Template created.",
        "success",
      );
      onCreated(created.id);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-workflow-title"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <h2 id="new-workflow-title">New workflow</h2>
        <p className="muted">Give your automation a name. You can rename it later.</p>
        <input
          className="field-input"
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
        />
        <div className="template-picker">
          {TEMPLATES.map((template) => (
            <button
              type="button"
              key={template.id}
              className={templateId === template.id ? "is-selected" : ""}
              onClick={() => {
                setTemplateId(template.id);
                const previous =
                  TEMPLATES.find((item) => item.id === templateId)?.name ??
                  "Untitled workflow";
                if (name === "Untitled workflow" || name === previous) {
                  setName(
                    template.id === "blank"
                      ? "Untitled workflow"
                      : template.name,
                  );
                }
              }}
            >
              <strong>{template.name}</strong>
              <span>{template.description}</span>
            </button>
          ))}
        </div>
        {error && <p className="error-text">{error}</p>}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [deployments, setDeployments] = useState<Deployment[] | null>(null);
  const [credentials, setCredentials] = useState<Credential[] | null>(null);
  const [environments, setEnvironments] = useState<Environment[] | null>(null);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);
  const [modalTemplateId, setModalTemplateId] = useState("blank");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sort, setSort] = useState("updated");
  const [pendingDelete, setPendingDelete] = useState<WorkflowSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [pendingRename, setPendingRename] = useState<WorkflowSummary | null>(null);
  const [renameName, setRenameName] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"grid" | "list">(() => {
    return (localStorage.getItem("noodle-wf-view") as "grid" | "list") ?? "grid";
  });
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [providerModalWorkflow, setProviderModalWorkflow] =
    useState<WorkflowSummary | null>(null);
  const [providerRows, setProviderRows] =
    useState<ProviderTriggerSubscription[] | null>(null);
  const [providerError, setProviderError] = useState("");
  const navigate = useNavigate();
  const { notify } = useToast();

  function load() {
    setError("");
    api
      .listWorkflows()
      .then(setWorkflows)
      .catch((err) => setError(String(err)));
    void Promise.allSettled([
      api.listDeployments(),
      api.listCredentials(),
      api.listEnvironments(),
    ]).then(([deploymentResult, credentialResult, environmentResult]) => {
      if (deploymentResult.status === "fulfilled") {
        setDeployments(deploymentResult.value);
      }
      if (credentialResult.status === "fulfilled") {
        setCredentials(credentialResult.value);
      }
      if (environmentResult.status === "fulfilled") {
        setEnvironments(environmentResult.value);
      }
    });
  }

  function openCreate(templateId = "blank"): void {
    setModalTemplateId(templateId);
    setModal(true);
  }

  function resetFilters(): void {
    setQuery("");
    setStatusFilter("all");
    setSort("updated");
  }

  useEffect(load, []);

  async function remove(id: string) {
    setDeleteBusy(true);
    try {
      await api.deleteWorkflow(id);
      notify("Workflow deleted.", "success");
      setPendingDelete(null);
      load();
    } catch (err) {
      setError(String(err));
      notify("Could not delete workflow.", "error");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function duplicate(wf: WorkflowSummary): Promise<void> {
    setOpenMenuId(null);
    try {
      const detail = await api.getWorkflow(wf.id);
      const created = await api.createWorkflow(`Copy of ${wf.name}`);
      await api.updateWorkflow(created.id, { graph: detail.graph });
      notify(`"${wf.name}" duplicated.`, "success");
      load();
    } catch {
      notify("Could not duplicate workflow.", "error");
    }
  }

  async function commitRename(): Promise<void> {
    if (!pendingRename || renameBusy) return;
    setRenameBusy(true);
    try {
      await api.updateWorkflow(pendingRename.id, {
        name: renameName.trim() || pendingRename.name,
      });
      notify("Workflow renamed.", "success");
      setPendingRename(null);
      load();
    } catch {
      notify("Could not rename workflow.", "error");
    } finally {
      setRenameBusy(false);
    }
  }

  useEffect(() => {
    if (!openMenuId) return;
    function handleOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuId(null);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [openMenuId]);

  function setView(mode: "grid" | "list"): void {
    setViewMode(mode);
    localStorage.setItem("noodle-wf-view", mode);
  }

  async function openProviderStatus(
    wf: WorkflowSummary,
    event: { stopPropagation: () => void },
  ): Promise<void> {
    event.stopPropagation();
    setProviderModalWorkflow(wf);
    setProviderRows(null);
    setProviderError("");
    try {
      setProviderRows(await api.listWorkflowProviderTriggers(wf.id));
    } catch (err) {
      setProviderError(String(err));
    }
  }

  function closeProviderStatus(): void {
    setProviderModalWorkflow(null);
    setProviderRows(null);
    setProviderError("");
  }

  const visible = useMemo(() => {
    const rows = workflows ?? [];
    const search = query.trim().toLowerCase();
    return rows
      .filter((wf) => {
        const counts = providerCounts(wf);
        if (search && !wf.name.toLowerCase().includes(search)) return false;
        if (statusFilter === "active" && !wf.active) return false;
        if (statusFilter === "inactive" && wf.active) return false;
        if (statusFilter === "draft" && !wf.has_unpublished_changes) return false;
        if (statusFilter === "failed" && wf.last_run_status !== "error") return false;
        if (statusFilter === "provider_error" && counts.error === 0) return false;
        return true;
      })
      .sort((a, b) => {
        if (sort === "name") return a.name.localeCompare(b.name);
        if (sort === "last_run") {
          return (
            new Date(b.last_run_started_at ?? 0).getTime() -
            new Date(a.last_run_started_at ?? 0).getTime()
          );
        }
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      });
  }, [query, sort, statusFilter, workflows]);

  const stats = useMemo(() => {
    const rows = workflows ?? [];
    return {
      active: rows.filter((wf) => wf.active).length,
      drafts: rows.filter((wf) => wf.has_unpublished_changes).length,
      failed: rows.filter((wf) => wf.last_run_status === "error").length,
      running: rows.filter((wf) => wf.last_run_status === "running").length,
      providerErrors: rows.filter((wf) => providerCounts(wf).error > 0).length,
    };
  }, [workflows]);

  const ops = useMemo(() => {
    const activeDeployments = deployments?.filter((item) => item.active).length ?? 0;
    const envErrors =
      environments?.filter((env) => env.status === "error").length ?? 0;
    const envBuilding =
      environments?.filter(
        (env) => env.status === "pending" || env.status === "building",
      ).length ?? 0;
    const credentialsNeedingAttention =
      credentials?.filter((cred) => cred.keys.length === 0).length ?? 0;
    return {
      activeDeployments,
      credentialsNeedingAttention,
      envErrors,
      envBuilding,
    };
  }, [credentials, deployments, environments]);

  return (
    <div className="home">
      <HomeHeader />

      <main className="home-main">
        <div className="home-bar">
          <h1>
            Workflows
            {workflows && <span className="home-count">{workflows.length}</span>}
          </h1>
          <button className="btn btn-primary" onClick={() => openCreate()}>
            New workflow
          </button>
        </div>

        {workflows && workflows.length > 0 && (
          <div className="dashboard-grid" aria-label="Operational summary">
            <button type="button" onClick={() => setStatusFilter("active")}>
              <strong>{stats.active}</strong>
              <span>Active</span>
            </button>
            <button type="button" onClick={() => setStatusFilter("draft")}>
              <strong>{stats.drafts}</strong>
              <span>Drafts</span>
            </button>
            <button type="button" onClick={() => setStatusFilter("failed")}>
              <strong>{stats.failed}</strong>
              <span>Failed runs</span>
            </button>
            <button type="button" onClick={() => setStatusFilter("provider_error")}>
              <strong>{stats.providerErrors}</strong>
              <span>Trigger errors</span>
            </button>
            <button type="button" onClick={() => setStatusFilter("all")}>
              <strong>{stats.running}</strong>
              <span>Running runs</span>
            </button>
            <button type="button" onClick={() => navigate("/deployments")}>
              <strong>{ops.activeDeployments}</strong>
              <span>Active deployments</span>
            </button>
            <button type="button" onClick={() => navigate("/credentials")}>
              <strong>{ops.credentialsNeedingAttention}</strong>
              <span>Credential attention</span>
            </button>
            <button type="button" onClick={() => navigate("/environments")}>
              <strong>{ops.envErrors}</strong>
              <span>Env errors</span>
            </button>
            <button type="button" onClick={() => navigate("/environments")}>
              <strong>{ops.envBuilding}</strong>
              <span>Env building</span>
            </button>
          </div>
        )}

        <div className="home-filters">
          <div className="home-search-row">
            <input
              className="field-input"
              placeholder="Search workflows..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <div className="wf-view-toggle" role="group" aria-label="View mode">
              <button
                type="button"
                title="Card view"
                className={viewMode === "grid" ? "active" : ""}
                onClick={() => setView("grid")}
              >
                <SquaresFour size={13} weight={viewMode === "grid" ? "fill" : "regular"} />
              </button>
              <button
                type="button"
                title="List view"
                className={viewMode === "list" ? "active" : ""}
                onClick={() => setView("list")}
              >
                <Rows size={13} weight={viewMode === "list" ? "fill" : "regular"} />
              </button>
            </div>
          </div>
          <select
            className="field-input"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
            <option value="draft">Draft changes</option>
            <option value="failed">Failed recently</option>
            <option value="provider_error">Trigger errors</option>
          </select>
          <select
            className="field-input"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="updated">Updated</option>
            <option value="last_run">Last run</option>
            <option value="name">Name</option>
          </select>
        </div>

        {error && <p className="error-text">{error}</p>}

        {!workflows && !error && (
          <div className="wf-grid" aria-label="Loading workflows">
            {Array.from({ length: 6 }).map((_, index) => (
              <div className="wf-card skeleton-card" key={index}>
                <span className="skeleton-line short" />
                <span className="skeleton-line title" />
                <span className="skeleton-line" />
                <span className="skeleton-line tiny" />
              </div>
            ))}
          </div>
        )}

        {workflows && workflows.length === 0 && (
          <div className="empty-state">
            <Logo size={44} />
            <h2>No workflows yet</h2>
            <p className="muted">
              Create your first automation and start wiring Python nodes together.
            </p>
            <button className="btn btn-primary" onClick={() => openCreate()}>
              New workflow
            </button>
            <div className="template-strip">
              {TEMPLATES.filter((template) => template.id !== "blank").map((template) => (
                <button
                  key={template.id}
                  type="button"
                  onClick={() => {
                    openCreate(template.id);
                    setQuery("");
                  }}
                >
                  {template.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {workflows && workflows.length > 0 && (
          <div className={viewMode === "list" ? "wf-list" : "wf-grid"}>
            {visible.map((wf) => {
              const hookBadge = providerStatusBadge(providerCounts(wf));
              const menuOpen = openMenuId === wf.id;
              return (
                <article
                  key={wf.id}
                  className={`wf-card${viewMode === "list" ? " wf-card--row" : ""}`}
                  onClick={() => navigate(`/workflows/${wf.id}`)}
                >
                  <div className="wf-card-top">
                    <span className={`wf-status ${wf.active ? "on" : "off"}`}>
                      {wf.active ? "active" : "inactive"}
                    </span>
                    {hookBadge && (
                      <button
                        type="button"
                        className={`wf-status wf-trigger-status ${hookBadge.className}`}
                        title={hookBadge.title}
                        onClick={(event) => void openProviderStatus(wf, event)}
                      >
                        {hookBadge.label}
                      </button>
                    )}
                  </div>
                  <h3 className="wf-name">{wf.name}</h3>
                  <div className="wf-meta">
                    <span>
                      {wf.node_count} node{wf.node_count === 1 ? "" : "s"}
                    </span>
                    <span className="dot-sep" />
                    <span>v{wf.version}</span>
                    <span className="dot-sep" />
                    <span>published v{wf.published_version}</span>
                    <span className="dot-sep" />
                    <span>{relativeTime(wf.updated_at)}</span>
                    {wf.has_unpublished_changes && (
                      <>
                        <span className="dot-sep" />
                        <span className="wf-meta-draft">draft</span>
                      </>
                    )}
                    {wf.last_run_status && (
                      <>
                        <span className="dot-sep" />
                        <span className={`wf-meta-run wf-meta-run-${wf.last_run_status}`}>
                          {wf.last_run_status}
                        </span>
                      </>
                    )}
                  </div>
                  {wf.last_run_started_at && (
                    <div className="wf-meta wf-run-meta">
                      Last run {relativeTime(wf.last_run_started_at)}
                    </div>
                  )}
                  <div
                    className="wf-menu-wrap"
                    ref={menuOpen ? menuRef : null}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      type="button"
                      className="wf-menu-btn"
                      title="More options"
                      aria-label="More options"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpenMenuId(menuOpen ? null : wf.id);
                      }}
                    >
                      <DotsThreeVertical size={16} weight="bold" />
                    </button>
                    {menuOpen && (
                      <div className="wf-menu" role="menu">
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => navigate(`/workflows/${wf.id}`)}
                        >
                          Open
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setOpenMenuId(null);
                            setPendingRename(wf);
                            setRenameName(wf.name);
                          }}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => void duplicate(wf)}
                        >
                          Duplicate
                        </button>
                        <div className="wf-menu-sep" />
                        <button
                          type="button"
                          role="menuitem"
                          className="danger"
                          onClick={() => {
                            setOpenMenuId(null);
                            setPendingDelete(wf);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
        {workflows && workflows.length > 0 && visible.length === 0 && (
          <div className="empty-state">
            <h2>No matches</h2>
            <p className="muted">Adjust search or filters.</p>
            <button className="btn" type="button" onClick={resetFilters}>
              Reset filters
            </button>
          </div>
        )}
      </main>

      {modal && (
        <CreateModal
          onClose={() => setModal(false)}
          onCreated={(id) => navigate(`/workflows/${id}`)}
          initialTemplateId={modalTemplateId}
        />
      )}
      {pendingDelete && (
        <ConfirmDialog
          title="Delete workflow"
          body={`Delete "${pendingDelete.name}"? This removes the workflow and cannot be undone.`}
          busy={deleteBusy}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void remove(pendingDelete.id)}
        />
      )}
      {pendingRename && (
        <div className="modal-overlay" onClick={() => setPendingRename(null)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="rename-workflow-title"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => { if (e.key === "Escape") setPendingRename(null); }}
          >
            <h2 id="rename-workflow-title">Rename workflow</h2>
            <input
              className="field-input"
              autoFocus
              value={renameName}
              onChange={(e) => setRenameName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void commitRename()}
            />
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setPendingRename(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={() => void commitRename()}
                disabled={renameBusy}
              >
                {renameBusy ? "Saving…" : "Rename"}
              </button>
            </div>
          </div>
        </div>
      )}
      {providerModalWorkflow && (
        <div className="modal-overlay" onClick={closeProviderStatus}>
          <div
            className="modal provider-trigger-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="provider-trigger-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="provider-trigger-title">Provider triggers</h2>
            <p className="muted">{providerModalWorkflow.name}</p>
            {providerError && <p className="error-text">{providerError}</p>}
            {!providerRows && !providerError && <p className="muted">Loading…</p>}
            {providerRows && providerRows.length === 0 && (
              <p className="muted">No provider trigger subscriptions.</p>
            )}
            {providerRows && providerRows.length > 0 && (
              <div className="provider-trigger-list">
                {providerRows.map((row) => {
                  const delivery = lastDelivery(row);
                  return (
                    <div className="provider-trigger-row" key={row.id}>
                      <div className="provider-trigger-head">
                        <strong>
                          {row.provider} · {row.trigger_key}
                        </strong>
                        <span className={`run-pill status-run-${row.status}`}>
                          {row.status}
                        </span>
                      </div>
                      <div className="provider-trigger-meta">
                        <span>{row.node_id}</span>
                        <span>
                          version {row.workflow_version_id?.slice(0, 8) ?? "draft"}
                        </span>
                        <span>
                          last event{" "}
                          {row.last_event_at
                            ? relativeTime(row.last_event_at)
                            : "never"}
                        </span>
                      </div>
                      {delivery && (
                        <div className="provider-trigger-meta">
                          {typeof delivery.response_status === "number" && (
                            <span>HTTP {delivery.response_status}</span>
                          )}
                          {typeof delivery.latency_ms === "number" && (
                            <span>{delivery.latency_ms}ms</span>
                          )}
                          {typeof delivery.event === "string" && (
                            <span>{delivery.event}</span>
                          )}
                          {typeof delivery.repository === "string" && (
                            <span>{delivery.repository}</span>
                          )}
                          {delivery.duplicate === true && <span>duplicate</span>}
                        </div>
                      )}
                      {row.callback_url && <code>{row.callback_url}</code>}
                      {row.error && <p className="error-text">{row.error}</p>}
                    </div>
                  );
                })}
              </div>
            )}
            <div className="modal-actions">
              <button className="btn" type="button" onClick={closeProviderStatus}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
