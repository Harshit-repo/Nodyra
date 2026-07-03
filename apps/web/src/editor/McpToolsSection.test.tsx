import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { McpToolsSection } from "./McpToolsSection";
import type { MCPConnection } from "../types";

describe("McpToolsSection", () => {
  it("shows allowed cached tools and inserts the selected tool node", () => {
    const onUseTool = vi.fn();
    const connection: MCPConnection = {
      id: "conn-1",
      name: "Local MCP",
      url: "http://localhost/mcp",
      transport: "streamable-http",
      auth_type: "none",
      auth_secret: "",
      enabled: true,
      allowed_tools: ["search_docs"],
      tool_cache: {
        tools: [
          { name: "search_docs", description: "Search documentation" },
          { name: "delete_all", description: "Blocked" },
        ],
      },
      last_synced_at: null,
      created_at: "2026-07-03T00:00:00Z",
      updated_at: "2026-07-03T00:00:00Z",
    };

    render(
      <McpToolsSection
        connections={[connection]}
        loading={false}
        error=""
        syncingId={null}
        onRefresh={vi.fn()}
        onSync={vi.fn()}
        onUseTool={onUseTool}
      />,
    );

    expect(screen.getByText("search_docs")).toBeTruthy();
    expect(screen.queryByText("delete_all")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /search_docs/i }));
    expect(onUseTool).toHaveBeenCalledWith("mcp_tool__conn-1__search_docs");
  });
});
