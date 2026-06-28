import { useEffect, useMemo, useRef, useState } from "react";
import { useEditor } from "./store";

export function ExecutionTimeline() {
  const running = useEditor((s) => s.running);
  const runStatus = useEditor((s) => s.runStatus);
  const nodes = useEditor((s) => s.nodes);
  const runError = useEditor((s) => s.runError);
  const [hidden, setHidden] = useState(true);
  const hideTimer = useRef<number | null>(null);

  useEffect(() => {
    if (running) {
      if (hideTimer.current) {
        clearTimeout(hideTimer.current);
        hideTimer.current = null;
      }
      setHidden(false);
      return;
    }
    const hasStatus = Object.keys(runStatus).length > 0;
    if (hasStatus) {
      hideTimer.current = window.setTimeout(() => setHidden(true), 5000);
    }
    return () => {
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, [running, runStatus]);

  const segments = useMemo(() => {
    const counts = { completed: 0, running: 0, pending: 0, error: 0 };
    const relevant = nodes.filter((n) => n.data?.manifest?.id);
    if (relevant.length === 0) return null;
    let currentName: string | null = null;
    for (const n of relevant) {
      const s = runStatus[n.id] ?? "pending";
      if (s === "success" || s === "skipped") counts.completed++;
      else if (s === "running") {
        counts.running++;
        currentName = n.data?.manifest?.name ?? n.id;
      } else if (s === "error") counts.error++;
      else counts.pending++;
    }
    return { ...counts, total: relevant.length, currentName };
  }, [nodes, runStatus]);

  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef<number | null>(null);
  useEffect(() => {
    if (running && !startRef.current) startRef.current = Date.now();
    if (!running) startRef.current = null;
  }, [running]);
  useEffect(() => {
    if (!running) return;
    const iv = setInterval(() => {
      if (startRef.current) setElapsed(Date.now() - startRef.current!);
    }, 200);
    return () => clearInterval(iv);
  }, [running]);

  if (hidden || !segments) return null;

  const pct = (n: number) =>
    segments.total > 0 ? (n / segments.total) * 100 : 0;
  const fmt = (ms: number) => {
    const s = Math.floor(ms / 1000);
    return s >= 60
      ? `${Math.floor(s / 60)}m ${s % 60}s`
      : `${s}s`;
  };

  return (
    <div
      className="exec-timeline"
      role="status"
      aria-label={`${segments.completed}/${segments.total} nodes`}
    >
      <div className="exec-timeline-bar">
        {segments.completed > 0 && (
          <div
            className="exec-timeline-seg exec-timeline-ok"
            style={{ width: `${pct(segments.completed)}%` }}
          />
        )}
        {segments.running > 0 && (
          <div
            className="exec-timeline-seg exec-timeline-run"
            style={{ width: `${pct(segments.running)}%` }}
          />
        )}
        {segments.error > 0 && (
          <div
            className="exec-timeline-seg exec-timeline-err"
            style={{ width: `${pct(segments.error)}%` }}
          />
        )}
        {segments.pending > 0 && (
          <div
            className="exec-timeline-seg exec-timeline-pend"
            style={{ width: `${pct(segments.pending)}%` }}
          />
        )}
      </div>
      <span className="exec-timeline-info">
        {segments.completed}/{segments.total} nodes
        {segments.currentName && (
          <span className="exec-timeline-cur"> &middot; {segments.currentName}</span>
        )}
        {runError && <span className="exec-timeline-err-text"> &mdash; Error</span>}
        <span className="exec-timeline-time">{fmt(elapsed)}</span>
      </span>
    </div>
  );
}
