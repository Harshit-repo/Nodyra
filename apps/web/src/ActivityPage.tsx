import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { HomeHeader } from "./HomeHeader";
import type { AuditEvent } from "./types";

function when(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleString();
}

export function ActivityPage() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [actionFilter, setActionFilter] = useState("all");
  const [targetFilter, setTargetFilter] = useState("all");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  useEffect(() => {
    api
      .listAudit()
      .then(setEvents)
      .catch((err) => setError(String(err)));
  }, []);

  const actions = useMemo(
    () => Array.from(new Set(events?.map((event) => event.action) ?? [])).sort(),
    [events],
  );
  const targets = useMemo(
    () =>
      Array.from(new Set(events?.map((event) => event.target_type) ?? [])).sort(),
    [events],
  );

  const visible = useMemo(() => {
    const rows = events ?? [];
    const needle = query.trim().toLowerCase();
    const from = fromDate ? new Date(`${fromDate}T00:00:00`).getTime() : null;
    const to = toDate ? new Date(`${toDate}T23:59:59`).getTime() : null;
    return rows.filter((event) => {
      const created = new Date(event.created_at).getTime();
      const searchable =
        `${event.action} ${event.target_type} ${event.target_id} ${event.detail}`.toLowerCase();
      if (needle && !searchable.includes(needle)) return false;
      if (actionFilter !== "all" && event.action !== actionFilter) return false;
      if (targetFilter !== "all" && event.target_type !== targetFilter) return false;
      if (from !== null && created < from) return false;
      if (to !== null && created > to) return false;
      return true;
    });
  }, [actionFilter, events, fromDate, query, targetFilter, toDate]);

  function exportCsv(): void {
    const escape = (value: string) => `"${value.replaceAll('"', '""')}"`;
    const rows = [
      ["created_at", "action", "target_type", "target_id", "detail"],
      ...visible.map((event) => [
        event.created_at,
        event.action,
        event.target_type,
        event.target_id,
        event.detail,
      ]),
    ];
    const csv = rows.map((row) => row.map(escape).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "noodle-activity.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Activity
            {events && <span className="home-count">{visible.length}</span>}
          </h1>
          <button
            type="button"
            className="btn"
            onClick={exportCsv}
            disabled={visible.length === 0}
          >
            Export CSV
          </button>
        </div>

        <div className="activity-filters">
          <input
            className="field-input"
            placeholder="Filter actor, target, action, detail..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="field-input"
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
          >
            <option value="all">All actions</option>
            {actions.map((action) => (
              <option key={action} value={action}>
                {action}
              </option>
            ))}
          </select>
          <select
            className="field-input"
            value={targetFilter}
            onChange={(e) => setTargetFilter(e.target.value)}
          >
            <option value="all">All targets</option>
            {targets.map((target) => (
              <option key={target} value={target}>
                {target}
              </option>
            ))}
          </select>
          <input
            className="field-input"
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            aria-label="From date"
          />
          <input
            className="field-input"
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            aria-label="To date"
          />
        </div>

        {error && <p className="error-text">{error}</p>}
        {!events && !error && (
          <div className="activity-list" aria-label="Loading activity">
            {Array.from({ length: 7 }).map((_, index) => (
              <div className="activity-row skeleton-row" key={index}>
                <span className="skeleton-line short" />
                <span className="skeleton-line short" />
                <span className="skeleton-line" />
                <span className="skeleton-line tiny" />
              </div>
            ))}
          </div>
        )}

        {events && events.length === 0 && (
          <p className="muted">No activity recorded yet.</p>
        )}

        {events && visible.length > 0 && (
          <div className="activity-list">
            {visible.map((event) => (
              <div className="activity-row" key={event.id}>
                <span className={`activity-action action-${event.action}`}>
                  {event.action}
                </span>
                <span className="activity-target">{event.target_type}</span>
                <span className="activity-detail">{event.detail || "—"}</span>
                <span className="activity-time">{when(event.created_at)}</span>
              </div>
            ))}
          </div>
        )}
        {events && events.length > 0 && visible.length === 0 && (
          <div className="empty-state">
            <h2>No matching activity</h2>
            <p className="muted">Clear one or more filters to broaden the view.</p>
          </div>
        )}
      </main>
    </div>
  );
}
