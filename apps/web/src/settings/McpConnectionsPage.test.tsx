import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { ConfirmProvider } from "../ConfirmProvider";
import { ToastProvider } from "../ToastProvider";
import type { MCPConnection, MCPToolCallAuditInfo } from "../types";
import { McpConnectionsPage } from "./McpConnectionsPage";

const connection: MCPConnection = {
  id: "conn_1",
  name: "Docs MCP",
  url: "https://mcp.example.com/mcp",
  transport: "streamable-http",
  auth_type: "bearer",
  auth_secret: "***redacted***",
  enabled: true,
  allowed_tools: ["search_docs"],
  tool_cache: [{ name: "search_docs" }] as unknown as Record<string, unknown>,
  last_synced_at: null,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const call: MCPToolCallAuditInfo = {
  id: "audit_1",
  connection_id: "conn_1",
  tool: "search_docs",
  ok: true,
  org_id: "default",
  actor_id: null,
  actor_email: null,
  run_id: "run_1",
  duration_ms: 42,
  error: null,
  created_at: "2026-07-13T01:00:00Z",
};

function renderPage() {
  return render(
    <ToastProvider>
      <ConfirmProvider>
        <McpConnectionsPage />
      </ConfirmProvider>
    </ToastProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("McpConnectionsPage", () => {
  it("shows recent tool-call audit history for a connection", async () => {
    vi.spyOn(api, "listMcpConnections").mockResolvedValue([connection]);
    vi.spyOn(api, "listMcpConnectionCalls").mockResolvedValue([call]);

    renderPage();

    expect(await screen.findByText("Docs MCP")).toBeTruthy();
    expect(await screen.findByText("Recent calls")).toBeTruthy();
    expect(screen.getByText("search_docs")).toBeTruthy();
    expect(screen.getByText("ok")).toBeTruthy();
    expect(screen.getByText(/42 ms/)).toBeTruthy();
    expect(screen.getByText(/run run_1/)).toBeTruthy();
  });
});
