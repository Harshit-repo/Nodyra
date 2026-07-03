import { ArrowsClockwise, Plus, Plug } from "@phosphor-icons/react";

import type { MCPConnection } from "../types";

export interface CachedMcpTool {
  name: string;
  description: string;
  manifestId: string;
}

export function mcpToolManifestId(connectionId: string, toolName: string): string {
  const safeId = toolName.replace(/[^a-zA-Z0-9_-]/g, "_").toLowerCase();
  return `mcp_tool__${connectionId}__${safeId}`;
}

export function cachedMcpTools(conn: MCPConnection): CachedMcpTool[] {
  const rawCache = conn.tool_cache as unknown;
  const rawTools = Array.isArray(rawCache)
    ? rawCache
    : rawCache && typeof rawCache === "object"
      ? ((rawCache as Record<string, unknown>).tools as unknown)
      : [];
  const allowed = conn.allowed_tools?.length ? new Set(conn.allowed_tools) : null;
  if (!Array.isArray(rawTools)) return [];
  return rawTools
    .map((tool) => {
      if (!tool || typeof tool !== "object") return null;
      const record = tool as Record<string, unknown>;
      const name = String(record.name ?? "");
      if (!name || (allowed && !allowed.has(name))) return null;
      return {
        name,
        description: String(record.description ?? ""),
        manifestId: mcpToolManifestId(conn.id, name),
      };
    })
    .filter((tool): tool is CachedMcpTool => Boolean(tool));
}

export function McpToolsSection({
  connections,
  loading,
  error,
  syncingId,
  onRefresh,
  onSync,
  onUseTool,
}: {
  connections: MCPConnection[];
  loading: boolean;
  error: string;
  syncingId: string | null;
  onRefresh: () => void;
  onSync: (connection: MCPConnection) => void;
  onUseTool: (manifestId: string) => void;
}) {
  return (
    <div className="palette-group palette-mcp-tools">
      <div className="palette-group-head">
        <span>
          <Plug size={12} weight="fill" aria-hidden="true" />
          MCP tools
        </span>
        <span className="palette-group-head-right">
          <button
            type="button"
            className="palette-mini-icon"
            aria-label="Refresh MCP tools"
            title="Refresh MCP tools"
            onClick={onRefresh}
            disabled={loading}
          >
            <ArrowsClockwise size={12} weight="bold" aria-hidden="true" />
          </button>
        </span>
      </div>

      {loading ? (
        <p className="palette-mcp-note">Loading MCP connections...</p>
      ) : error ? (
        <p className="palette-mcp-note palette-mcp-note-error">{error}</p>
      ) : connections.length === 0 ? (
        <p className="palette-mcp-note">No MCP connections configured.</p>
      ) : (
        <div className="palette-mcp-connection-list">
          {connections.map((conn) => {
            const tools = cachedMcpTools(conn);
            const syncing = syncingId === conn.id;
            return (
              <section className="palette-mcp-connection" key={conn.id}>
                <div className="palette-mcp-connection-head">
                  <span>
                    <strong>{conn.name}</strong>
                    <small>{conn.enabled === false ? "disabled" : `${tools.length} tools`}</small>
                  </span>
                  <button
                    type="button"
                    className="palette-mini-icon"
                    aria-label={`Sync ${conn.name} tools`}
                    title={`Sync ${conn.name} tools`}
                    onClick={() => onSync(conn)}
                    disabled={syncing}
                  >
                    <ArrowsClockwise
                      size={12}
                      weight="bold"
                      className={syncing ? "spin" : ""}
                      aria-hidden="true"
                    />
                  </button>
                </div>
                {tools.length ? (
                  <div className="palette-mcp-tool-list">
                    {tools.slice(0, 6).map((tool) => (
                      <button
                        type="button"
                        className="palette-mcp-tool"
                        key={tool.manifestId}
                        onClick={() => onUseTool(tool.manifestId)}
                        disabled={conn.enabled === false}
                        title={tool.description || tool.name}
                      >
                        <span>{tool.name}</span>
                        <Plus size={12} weight="bold" aria-hidden="true" />
                      </button>
                    ))}
                    {tools.length > 6 && (
                      <small className="palette-mcp-more">+{tools.length - 6} more in MCP category</small>
                    )}
                  </div>
                ) : (
                  <p className="palette-mcp-note">No cached tools. Sync to discover.</p>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
