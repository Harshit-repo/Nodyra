import { useEffect, useState } from "react";

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

  useEffect(() => {
    api
      .listAudit()
      .then(setEvents)
      .catch((err) => setError(String(err)));
  }, []);

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Activity
            {events && <span className="home-count">{events.length}</span>}
          </h1>
        </div>

        {error && <p className="error-text">{error}</p>}
        {!events && !error && <p className="muted">Loading…</p>}

        {events && events.length === 0 && (
          <p className="muted">No activity recorded yet.</p>
        )}

        {events && events.length > 0 && (
          <div className="activity-list">
            {events.map((event) => (
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
      </main>
    </div>
  );
}
