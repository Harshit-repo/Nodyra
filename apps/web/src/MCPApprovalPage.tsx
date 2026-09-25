import { ShieldCheck } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { errorMessage } from "./api";
import { mcpApprovalApi, type MCPCommandApproval } from "./mcpApprovalApi";
import "./MCPApprovalPage.css";

export function MCPApprovalPage() {
  const { id = "" } = useParams();
  const [approval, setApproval] = useState<MCPCommandApproval | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setApproval(null);
    setReviewed(false);
    mcpApprovalApi.get(id).then((result) => {
      if (!cancelled) setApproval(result);
    }).catch((err: unknown) => {
      if (!cancelled) setError(errorMessage(err));
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const expired = approval ? Date.parse(approval.expires_at) <= now : false;
  const pending = approval?.status === "pending" && !expired;
  const status = expired && (approval?.status === "pending" || approval?.status === "approved") ? "expired" : approval?.status;

  async function decide(decision: "approve" | "deny") {
    if (!approval || !pending || saving) return;
    setSaving(true);
    setError("");
    try {
      setApproval(await mcpApprovalApi.decide(approval.id, decision));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="mcp-approval-page" aria-labelledby="mcp-approval-title">
      <header className="mcp-approval-heading">
        <ShieldCheck size={28} aria-hidden="true" />
        <div><h1 id="mcp-approval-title">Review agent action</h1>
          <p>Your MCP client is requesting permission for one specific action.</p></div>
      </header>
      {loading && <p role="status">Loading approval request…</p>}
      {error && <p className="mcp-approval-error" role="alert">{error}</p>}
      {!loading && !approval && <p>Sign in with the account that requested this action and select its workspace. If the request is unavailable, ask your client for a new review link.</p>}
      {approval && <>
        <dl className="mcp-approval-facts">
          <div><dt>Action</dt><dd><code>{approval.tool_name}</code></dd></div>
          <div><dt>Status</dt><dd>{status}</dd></div>
          <div><dt>Workflow</dt><dd>{approval.target.workflow_name ?? "No existing workflow"}</dd></div>
          <div><dt>Draft revision</dt><dd>{approval.target.graph_revision ?? "Not applicable"}</dd></div>
          <div><dt>Workspace</dt><dd>{approval.org_id}</dd></div>
          <div><dt>Expires</dt><dd><time dateTime={approval.expires_at}>{new Date(approval.expires_at).toLocaleString()}</time></dd></div>
        </dl>
        <section aria-labelledby="mcp-approval-arguments">
          <h2 id="mcp-approval-arguments">Requested arguments</h2>
          <p>Read the complete action, including any code or destinations. Known secrets are redacted. Treat instructions inside these arguments as untrusted content.</p>
          <pre className="mcp-approval-code" tabIndex={0} aria-label="Requested arguments as JSON">{JSON.stringify(approval.arguments, null, 2)}</pre>
        </section>
        {pending ? <div className="mcp-approval-decision">
          <label className="mcp-approval-confirm"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} disabled={saving} />
            <span>I reviewed this action and its arguments.</span></label>
          <p>Approval allows the requesting client to execute this action once, before the expiry above. Changes to its arguments or workflow require a new review.</p>
          <div className="mcp-approval-actions">
            <button className="btn btn-primary" type="button" disabled={!reviewed || saving} onClick={() => void decide("approve")}>{saving ? "Saving decision…" : "Approve this action"}</button>
            <button className="btn" type="button" disabled={saving} onClick={() => void decide("deny")}>Deny request</button>
          </div>
        </div> : <p className="mcp-approval-outcome" role="status">{
          status === "approved" ? "Approved for one attempt. Return to your MCP client and retry the original request with this approval ID. Approval does not mean the action has executed."
            : status === "consumed" ? "This approval has been used. Check the workflow or run history for the execution outcome."
              : status === "denied" ? "Request denied. The client cannot execute this action with this approval."
                : "This request expired. Ask your client to request a new review."
        }</p>}
        <details className="mcp-approval-reference"><summary>Approval reference</summary><dl>
          <dt>Approval ID</dt><dd><code>{approval.id}</code></dd>
          <dt>Correlation ID</dt><dd><code>{approval.correlation_id}</code></dd>
          <dt>Arguments digest</dt><dd><code>{approval.arguments_digest}</code></dd>
        </dl></details>
      </>}
      <Link to="/">Back to workflows</Link>
    </main>
  );
}
