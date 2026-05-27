import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "./api";
import { HomeHeader } from "./HomeHeader";
import { Logo } from "./Logo";
import { useToast } from "./ToastProvider";
import type { WorkflowGraph, WorkflowSummary } from "./types";

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

function CreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [name, setName] = useState("Untitled workflow");
  const [templateId, setTemplateId] = useState("blank");
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
      notify(template?.id === "blank" ? "Workflow created." : "Template created.", "success");
      onCreated(created.id);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New workflow</h2>
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
                if (name === "Untitled workflow") setName(template.name);
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

function node(
  id: string,
  type: string,
  params: Record<string, unknown>,
  x: number,
  y: number,
) {
  return {
    id,
    type,
    params,
    position: { x, y },
    disabled: false,
    outputs_override: null,
    on_error: "stop",
    retry_on_fail: false,
    retries: 1,
    retry_wait_seconds: 0,
    retry_backoff: false,
    always_output_data: false,
    timeout_seconds: null,
  };
}

const TEMPLATES: {
  id: string;
  name: string;
  description: string;
  graph?: () => WorkflowGraph;
}[] = [
  {
    id: "blank",
    name: "Blank workflow",
    description: "Start with an empty canvas.",
  },
  {
    id: "api-code",
    name: "API fetch + Python",
    description: "Fetch JSON, transform it in Code, inspect output.",
    graph: () => ({
      nodes: [
        node("manual", "manual_trigger", { data: {} }, 0, 40),
        node(
          "fetch",
          "http_request",
          {
            url: "https://jsonplaceholder.typicode.com/users",
            method: "GET",
            headers: {},
            query: {},
            body: {},
          },
          260,
          40,
        ),
        node(
          "code",
          "code",
          {
            code:
              "rows = input or []\n" +
              "output = {\n" +
              "    'row_count': len(rows),\n" +
              "    'emails': [row.get('email') for row in rows],\n" +
              "    'records': rows,\n" +
              "}",
          },
          520,
          40,
        ),
      ],
      edges: [
        { id: "e1", source: "manual", source_output: "main", target: "fetch", target_input: "input" },
        { id: "e2", source: "fetch", source_output: "main", target: "code", target_input: "input" },
      ],
    }),
  },
  {
    id: "webhook-slack",
    name: "Webhook + Slack",
    description: "Capture a webhook and send a Slack notification.",
    graph: () => ({
      nodes: [
        node("hook", "webhook_trigger", { path: "incoming-event", response_mode: "On Received" }, 0, 40),
        node(
          "slack",
          "slack_send_message",
          { bot_token: "", channel: "", text: "New webhook event: {{ $json }}", blocks: null, thread_ts: "" },
          300,
          40,
        ),
      ],
      edges: [
        { id: "e1", source: "hook", source_output: "main", target: "slack", target_input: "input" },
      ],
    }),
  },
  {
    id: "schedule-http",
    name: "Schedule + HTTP transform",
    description: "Run on a schedule, call an API, and normalize fields.",
    graph: () => ({
      nodes: [
        node("schedule", "schedule_trigger", { every: 1, interval: "hours", cron: "", timezone: "UTC" }, 0, 40),
        node("fetch", "http_request", { url: "https://jsonplaceholder.typicode.com/posts", method: "GET", headers: {}, query: {}, body: {} }, 280, 40),
        node("limit", "limit", { max_items: 5, keep: "first" }, 560, 40),
      ],
      edges: [
        { id: "e1", source: "schedule", source_output: "main", target: "fetch", target_input: "input" },
        { id: "e2", source: "fetch", source_output: "main", target: "limit", target_input: "input" },
      ],
    }),
  },
];

export function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sort, setSort] = useState("updated");
  const navigate = useNavigate();
  const { notify } = useToast();

  function load() {
    api
      .listWorkflows()
      .then(setWorkflows)
      .catch((err) => setError(String(err)));
  }

  useEffect(load, []);

  async function remove(id: string, name: string) {
    if (!window.confirm(`Delete “${name}”? This cannot be undone.`)) return;
    try {
      await api.deleteWorkflow(id);
      notify("Workflow deleted.", "success");
      load();
    } catch (err) {
      setError(String(err));
      notify("Could not delete workflow.", "error");
    }
  }

  const visible = useMemo(() => {
    const rows = workflows ?? [];
    const search = query.trim().toLowerCase();
    return rows
      .filter((wf) => {
        if (search && !wf.name.toLowerCase().includes(search)) return false;
        if (statusFilter === "active" && !wf.active) return false;
        if (statusFilter === "inactive" && wf.active) return false;
        if (statusFilter === "draft" && !wf.has_unpublished_changes) return false;
        if (statusFilter === "failed" && wf.last_run_status !== "error") return false;
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

  return (
    <div className="home">
      <HomeHeader />

      <main className="home-main">
        <div className="home-bar">
          <h1>
            Workflows
            {workflows && <span className="home-count">{workflows.length}</span>}
          </h1>
          <button className="btn btn-primary" onClick={() => setModal(true)}>
            New workflow
          </button>
        </div>

        <div className="home-filters">
          <input
            className="field-input"
            placeholder="Search workflows..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
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

        {!workflows && !error && <p className="muted">Loading…</p>}

        {workflows && workflows.length === 0 && (
          <div className="empty-state">
            <Logo size={44} />
            <h2>No workflows yet</h2>
            <p className="muted">
              Create your first automation and start wiring Python nodes together.
            </p>
            <button className="btn btn-primary" onClick={() => setModal(true)}>
              New workflow
            </button>
            <div className="template-strip">
              {TEMPLATES.filter((template) => template.id !== "blank").map((template) => (
                <button
                  key={template.id}
                  type="button"
                  onClick={() => {
                    setModal(true);
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
          <div className="wf-grid">
            {visible.map((wf) => (
              <article
                key={wf.id}
                className="wf-card"
                onClick={() => navigate(`/workflows/${wf.id}`)}
              >
                <div className="wf-card-top">
                  <span className={`wf-status ${wf.active ? "on" : "off"}`}>
                    {wf.active ? "active" : "inactive"}
                  </span>
                  {wf.has_unpublished_changes && (
                    <span className="wf-status draft">draft changes</span>
                  )}
                  {wf.last_run_status && (
                    <span className={`wf-status run-${wf.last_run_status}`}>
                      last {wf.last_run_status}
                    </span>
                  )}
                  <button
                    className="wf-delete"
                    title="Delete workflow"
                    onClick={(e) => {
                      e.stopPropagation();
                      void remove(wf.id, wf.name);
                    }}
                  >
                    ×
                  </button>
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
                </div>
                {wf.last_run_started_at && (
                  <div className="wf-meta wf-run-meta">
                    Last run {relativeTime(wf.last_run_started_at)}
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
        {workflows && workflows.length > 0 && visible.length === 0 && (
          <div className="empty-state">
            <h2>No matches</h2>
            <p className="muted">Adjust search or filters.</p>
          </div>
        )}
      </main>

      {modal && (
        <CreateModal
          onClose={() => setModal(false)}
          onCreated={(id) => navigate(`/workflows/${id}`)}
        />
      )}
    </div>
  );
}
