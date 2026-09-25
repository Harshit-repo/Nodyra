import { ApiError, formatErrorDetail, getOrgId } from "./api";

export interface MCPCommandApproval {
  id: string;
  tool_name: string;
  status: "pending" | "approved" | "denied" | "consumed" | "expired";
  arguments: Record<string, unknown>;
  arguments_digest: string;
  target: { workflow_id?: string; workflow_name?: string; graph_revision?: number; published_version?: number | null };
  actor_id: string;
  org_id: string;
  correlation_id: string;
  expires_at: string;
  created_at: string;
}

async function browserRequest(path: string, decision?: "approve" | "deny"): Promise<MCPCommandApproval> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const orgId = getOrgId();
  if (orgId) headers["X-Org-Id"] = orgId;
  const csrf = document.cookie.split("; ").find((part) => part.startsWith("nodyra_csrf="));
  if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf.slice("nodyra_csrf=".length));
  // Intentionally never attach a stored bearer token: approval decisions are
  // restricted to authenticated browser sessions on the server.
  let response: Response;
  try {
    response = await fetch(`/api/mcp-approvals/${path}`, {
      method: decision ? "POST" : "GET", credentials: "same-origin", headers,
      ...(decision ? { body: JSON.stringify({ decision }) } : {}),
    });
  } catch {
    throw new Error("Could not reach Nodyra. Check your connection and try again.");
  }
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error("Nodyra could not load this approval request. Reload the page or try again shortly.");
  }
  if (!response.ok) throw new ApiError(response.status, formatErrorDetail(body.detail), body.detail);
  if (!body || typeof body !== "object" || Array.isArray(body) || typeof body.id !== "string"
    || typeof body.tool_name !== "string" || !body.target || !body.arguments
    || !["pending", "approved", "denied", "consumed", "expired"].includes(body.status)
    || !Number.isFinite(Date.parse(body.expires_at))) {
    throw new Error("The approval request could not be read. Reload the page or ask your client for a new review link.");
  }
  return body as MCPCommandApproval;
}

export const mcpApprovalApi = {
  get: (id: string) => browserRequest(encodeURIComponent(id)),
  decide: (id: string, decision: "approve" | "deny") => browserRequest(`${encodeURIComponent(id)}/decision`, decision),
};
