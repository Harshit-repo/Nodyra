import { Article, DownloadSimple, Funnel } from "@phosphor-icons/react";
import { useEffect, useState } from "react";

import { EmptyState } from "../EmptyState";
import { api, userFriendlyError } from "../api";
import { useEntitlements } from "../entitlements";
import type { AuditEventInfo } from "../types";

const ACTIONS = [
  "workflow.create",
  "workflow.delete",
  "workflow.publish",
  "run.start",
  "credential.access",
  "credential.create",
  "login",
  "logout",
  "user.invite",
  "user.role_change",
  "membership.change",
];

const RESOURCE_TYPES = [
  "workflow",
  "run",
  "credential",
  "user",
  "membership",
  "environment",
  "deployment",
  "mcp_connection",
  "custom_role",
  "organization",
];

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------

function FilterBar({
  filters,
  onChange,
}: {
  filters: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  return (
    <div className="audit-filter-bar">
      <Funnel size={16} aria-hidden="true" />
      <select
        className="field-input"
        value={filters.action}
        onChange={(e) => onChange("action", e.target.value)}
      >
        <option value="">All actions</option>
        {ACTIONS.map((a) => (
          <option key={a} value={a}>{a}</option>
        ))}
      </select>
      <select
        className="field-input"
        value={filters.resource_type}
        onChange={(e) => onChange("resource_type", e.target.value)}
      >
        <option value="">All resource types</option>
        {RESOURCE_TYPES.map((r) => (
          <option key={r} value={r}>{r}</option>
        ))}
      </select>
      <input
        className="field-input"
        type="text"
        placeholder="Actor user ID"
        value={filters.user_id}
        onChange={(e) => onChange("user_id", e.target.value)}
      />
      <input
        className="field-input"
        type="date"
        aria-label="From date"
        value={filters.from}
        onChange={(e) => onChange("from", e.target.value)}
      />
      <input
        className="field-input"
        type="date"
        aria-label="To date"
        value={filters.to}
        onChange={(e) => onChange("to", e.target.value)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function AuditLogPage() {
  const entitlements = useEntitlements();

  const [logs, setLogs] = useState<AuditEventInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState<Record<string, string>>({
    user_id: "",
    action: "",
    resource_type: "",
    from: "",
    to: "",
  });
  const PAGE_SIZE = 50;

  const hasFeature = entitlements.has("audit_logs");

  useEffect(() => {
    if (hasFeature) {
      loadLogs();
    } else {
      setLoading(false);
      setLogs([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, hasFeature]);

  function updateFilter(key: string, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(0);
  }

  async function loadLogs() {
    setLoading(true);
    setError("");
    try {
      const data = await api.listAuditLogs({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        user_id: filters.user_id || undefined,
        action: filters.action || undefined,
        resource_type: filters.resource_type || undefined,
        from: filters.from || undefined,
        to: filters.to || undefined,
      });
      setLogs(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(userFriendlyError(err));
      setLogs([]);
    } finally {
      setLoading(false);
    }
  }

  async function handleExport() {
    try {
      const qs = new URLSearchParams();
      if (filters.user_id) qs.set("user_id", filters.user_id);
      if (filters.action) qs.set("action", filters.action);
      if (filters.resource_type) qs.set("resource_type", filters.resource_type);
      if (filters.from) qs.set("from", filters.from);
      if (filters.to) qs.set("to", filters.to);

      const resp = await fetch(
        `/api/admin/audit-logs/export?${qs.toString()}`,
        { headers: { Accept: "text/csv" } },
      );
      if (!resp.ok) {
        const body = await resp.text().catch(() => resp.statusText);
        throw new Error(body);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "audit-log.csv";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(userFriendlyError(err));
    }
  }

  if (!hasFeature) {
    return (
      <div className="home">
        <main className="home-main">
          <EmptyState
            icon={<Article size={48} />}
            title="Audit Logs"
            description="Audit logs are available with the Pro plan or higher."
          />
        </main>
      </div>
    );
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Audit Logs
            {total > 0 && <span className="home-count">{total}</span>}
          </h1>
          <div className="home-bar-actions">
            <button className="btn" onClick={() => void handleExport()}>
              <DownloadSimple size={16} weight="bold" /> Export CSV
            </button>
            <button className="btn btn-primary" onClick={() => { setPage(0); void loadLogs(); }}>
              Refresh
            </button>
          </div>
        </div>

        <FilterBar filters={filters} onChange={updateFilter} />

        {error && <p className="error-text">{error}</p>}

        {loading && (
          <div className="audit-log-skeleton" aria-label="Loading audit logs">
            {Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="skeleton-line" />
            ))}
          </div>
        )}

        {!loading && logs.length === 0 && (
          <EmptyState
            icon={<Article size={48} />}
            title="No audit log entries"
            description={
              Object.values(filters).some(Boolean)
                ? "Try adjusting your filters."
                : "Audit events will appear here as actions are performed."
            }
          />
        )}

        {!loading && logs.length > 0 && (
          <>
            <table className="audit-log-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Action</th>
                  <th>Resource</th>
                  <th>Target ID</th>
                  <th>Actor</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((entry) => (
                  <tr key={entry.id}>
                    <td className="audit-log-cell-time">
                      {new Date(entry.created_at).toLocaleString()}
                    </td>
                    <td><code className="audit-log-action">{entry.action}</code></td>
                    <td>{entry.target_type}</td>
                    <td className="audit-log-cell-id">
                      <code>{entry.target_id || "—"}</code>
                    </td>
                    <td>{entry.actor_email || entry.actor_id || "—"}</td>
                    <td className="audit-log-cell-detail">
                      {entry.detail || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {totalPages > 1 && (
              <div className="pagination">
                <button
                  className="btn btn-sm"
                  disabled={page <= 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous
                </button>
                <span className="pagination-info">
                  Page {page + 1} of {totalPages}
                </span>
                <button
                  className="btn btn-sm"
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
