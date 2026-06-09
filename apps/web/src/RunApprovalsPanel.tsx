import { useEffect, useState } from "react";

import { api, type RunApprovalInfo } from "./api";

/**
 * Tool-approval list for a run: shows pending/decided agent-tool approvals and,
 * while any are pending, lets the user approve/reject inline. Polls while the
 * run is still running/waiting. Reused by ExecutionsPage and the EditorPage
 * "run paused for approval" banner (UX-6).
 */
export function RunApprovalsPanel({
  runId,
  runStatus,
  onChanged,
}: {
  runId: string;
  runStatus: string;
  onChanged?: () => void;
}) {
  const [approvals, setApprovals] = useState<RunApprovalInfo[]>([]);
  const [error, setError] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    try {
      const rows = await api.runApprovals(runId);
      setApprovals(rows);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void refresh();
    if (runStatus !== "running" && runStatus !== "waiting") return;
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, runStatus]);

  async function decide(
    approvalId: string,
    decision: "approve" | "reject",
  ): Promise<void> {
    setPendingId(approvalId);
    try {
      const updated = await api.decideRunApproval(runId, approvalId, decision);
      setApprovals((rows) =>
        rows.map((row) => (row.id === updated.id ? updated : row)),
      );
      onChanged?.();
      setError("");
    } catch (e) {
      setError(String(e));
    } finally {
      setPendingId(null);
    }
  }

  if (approvals.length === 0 && !error) return null;

  return (
    <section className="exec-approvals">
      <div className="exec-approvals-head">
        <h3>Tool approvals</h3>
        {approvals.some((row) => row.status === "pending") && (
          <span className="run-pill status-run-running">pending</span>
        )}
      </div>
      {error && <p className="error-text">{error}</p>}
      {approvals.map((approval) => (
        <article
          key={approval.id}
          className={`exec-approval status-approval-${approval.status}`}
        >
          <div className="exec-approval-main">
            <div>
              <div className="exec-approval-tool">{approval.tool_name}</div>
              <div className="exec-approval-meta">
                {approval.agent_node_id || approval.node_id || "agent"} · step{" "}
                {approval.step}
              </div>
            </div>
            <span className={`run-pill status-run-${approval.status}`}>
              {approval.status}
            </span>
          </div>
          {approval.message && (
            <div className="exec-approval-message">{approval.message}</div>
          )}
          {Object.keys(approval.arguments || {}).length > 0 && (
            <details className="exec-approval-args">
              <summary>Arguments</summary>
              <pre className="data-json">
                {JSON.stringify(approval.arguments, null, 2)}
              </pre>
            </details>
          )}
          {approval.status === "pending" && (
            <div className="exec-approval-actions">
              <button
                type="button"
                className="btn btn-sm"
                disabled={pendingId === approval.id}
                onClick={() => void decide(approval.id, "approve")}
              >
                Approve
              </button>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                disabled={pendingId === approval.id}
                onClick={() => void decide(approval.id, "reject")}
              >
                Reject
              </button>
            </div>
          )}
        </article>
      ))}
    </section>
  );
}
