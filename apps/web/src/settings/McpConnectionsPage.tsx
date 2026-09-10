import { ArrowsClockwise, Plug, Trash } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import { EmptyState } from "../EmptyState";

import { api, userFriendlyError } from "../api";
import { useConfirm } from "../ConfirmProvider";
import { useToast } from "../ToastProvider";
import { useModalA11y } from "../useModalA11y";
import type { MCPConnection, MCPToolCallAuditInfo } from "../types";

// ---------------------------------------------------------------------------
// Create / Edit modal
// ---------------------------------------------------------------------------

type AuthType = "none" | "bearer" | "header";

function parseAllowedTools(value: string): string[] | null {
  const tools = value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return tools.length ? Array.from(new Set(tools)) : null;
}

function CreateConnectionModal({
  edit,
  onClose,
  onSaved,
}: {
  edit: MCPConnection | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState(edit?.name ?? "");
  const [url, setUrl] = useState(edit?.url ?? "");
  const [transport, setTransport] = useState(edit?.transport ?? "streamable-http");
  const [authType, setAuthType] = useState<AuthType>(
    (edit?.auth_type as AuthType) ?? "none",
  );
  const [authSecret, setAuthSecret] = useState("");
  const [authHeaderName, setAuthHeaderName] = useState("");
  const [enabled, setEnabled] = useState(edit?.enabled ?? true);
  const [allowedTools, setAllowedTools] = useState((edit?.allowed_tools ?? []).join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useModalA11y(dialogRef, onClose);

  // When editing an existing connection, we don't show the stored secret value
  // (the API never returns it). The user only needs to enter a value if they
  // want to change it.
  useEffect(() => {
    if (edit) {
      // Pre-fill header name if auth_type is "header"
      if (edit.auth_type === "header") {
        setAuthHeaderName("Authorization");
      }
    }
  }, [edit]);

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      if (edit) {
        const body: Record<string, unknown> = {
          name: name.trim(),
          url: url.trim(),
          transport,
          auth_type: authType,
          enabled,
          allowed_tools: parseAllowedTools(allowedTools),
        };
        if (authSecret.trim()) {
          body.auth_secret = authSecret.trim();
        }
        if (authType === "header" && authHeaderName.trim()) {
          body.auth_header_name = authHeaderName.trim();
        }
        await api.updateMcpConnection(edit.id, body);
        notify("MCP connection updated.", "success");
      } else {
        await api.createMcpConnection({
          name: name.trim(),
          url: url.trim(),
          transport,
          auth_type: authType,
          auth_secret: authSecret.trim() || undefined,
          auth_header_name:
            authType === "header" ? authHeaderName.trim() || undefined : undefined,
          enabled,
          allowed_tools: parseAllowedTools(allowedTools),
        });
        notify("MCP connection created.", "success");
      }
      onSaved();
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  const isEditing = edit !== null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-connection-modal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2 id="mcp-connection-modal-title">
            {isEditing ? "Edit MCP connection" : "New MCP connection"}
          </h2>
        </div>

        <div className="modal-body">
          {isEditing && (
            <p className="muted">
              Auth secret values are never returned by the API. Leave the field
              blank to keep the current value.
            </p>
          )}

          <label className="credential-form-field">
            <span>Name *</span>
            <input
              className="field-input"
              autoFocus
              placeholder="My MCP server"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <label className="credential-form-field">
            <span>URL *</span>
            <input
              className="field-input"
              placeholder="https://example.com/mcp"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </label>

          <label className="credential-form-field">
            <span>Transport</span>
            <select
              className="field-input"
              value={transport}
              onChange={(e) => setTransport(e.target.value)}
            >
              <option value="streamable-http">Streamable HTTP (V1)</option>
            </select>
            <small className="muted">
              Streamable HTTP is the only supported transport in this version.
            </small>
          </label>

          <label className="credential-form-field">
            <span>Auth type</span>
            <select
              className="field-input"
              value={authType}
              onChange={(e) => setAuthType(e.target.value as AuthType)}
            >
              <option value="none">None</option>
              <option value="bearer">Bearer token</option>
              <option value="header">Custom header</option>
            </select>
          </label>

          {authType !== "none" && (
            <label className="credential-form-field">
              <span>
                {authType === "bearer" ? "Bearer token *" : "Header value *"}
              </span>
              <input
                className="field-input"
                type="password"
                placeholder={
                  isEditing
                    ? "Leave blank to keep current"
                    : authType === "bearer"
                      ? "Bearer token"
                      : "Header value"
                }
                value={authSecret}
                onChange={(e) => setAuthSecret(e.target.value)}
              />
            </label>
          )}

          {authType === "header" && (
            <label className="credential-form-field">
              <span>Header name *</span>
              <input
                className="field-input"
                placeholder="X-Custom-Auth"
                value={authHeaderName}
                onChange={(e) => setAuthHeaderName(e.target.value)}
              />
            </label>
          )}

          <label className="credential-form-field checkbox-inline">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>Enable this connection</span>
          </label>

          <label className="credential-form-field">
            <span>Allowed tools</span>
            <textarea
              className="field-input"
              rows={5}
              placeholder="Leave blank to allow every discovered tool"
              value={allowedTools}
              onChange={(e) => setAllowedTools(e.target.value)}
            />
            <small className="muted">
              Enter one tool name per line, or comma-separated. Matching is exact.
            </small>
          </label>
        </div>

        {error && <p className="error-text">{error}</p>}

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={busy}
          >
            {busy ? "Saving..." : isEditing ? "Save changes" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connection list card
// ---------------------------------------------------------------------------

function formatCallTime(value: string): string {
  const ts = new Date(value);
  if (Number.isNaN(ts.getTime())) return value;
  return ts.toLocaleString();
}

function ConnectionCallHistory({ connectionId }: { connectionId: string }) {
  const [calls, setCalls] = useState<MCPToolCallAuditInfo[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setCalls(null);
    setError("");
    void (async () => {
      try {
        const rows = await api.listMcpConnectionCalls(connectionId);
        if (!cancelled) setCalls(rows);
      } catch (err) {
        if (!cancelled) {
          setError(userFriendlyError(err));
          setCalls([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [connectionId]);

  if (error) {
    return <p className="mcp-call-error">Call history unavailable. {error}</p>;
  }
  if (calls === null) {
    return (
      <div role="status" className="mcp-call-history" aria-label="Loading MCP call history">
        <span className="skeleton-line short" />
        <span className="skeleton-line" />
      </div>
    );
  }
  if (calls.length === 0) {
    return (
      <div className="mcp-call-history">
        <div className="mcp-call-history-title">Recent calls</div>
        <p className="mcp-call-empty">No external tool calls recorded.</p>
      </div>
    );
  }

  return (
    <div className="mcp-call-history">
      <div className="mcp-call-history-title">Recent calls</div>
      <ul>
        {calls.map((call) => (
          <li key={call.id} className="mcp-call-row">
            <span className={`mcp-call-status ${call.ok ? "ok" : "error"}`}>
              {call.ok ? "ok" : "err"}
            </span>
            <span className="mcp-call-main">
              <code>{call.tool || "unknown_tool"}</code>
              <span>
                {formatCallTime(call.created_at)}
                {call.duration_ms !== null ? ` · ${call.duration_ms} ms` : ""}
                {call.run_id ? ` · run ${call.run_id}` : ""}
              </span>
              {!call.ok && call.error && <span className="mcp-call-error-text">{call.error}</span>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ConnectionCard({
  conn,
  onEdit,
  onDelete,
  onSync,
  syncing,
}: {
  conn: MCPConnection;
  onEdit: () => void;
  onDelete: () => void;
  onSync: () => void;
  syncing: boolean;
}) {
  const toolCount = conn.tool_cache
    ? Array.isArray(conn.tool_cache)
      ? (conn.tool_cache as unknown[]).length
      : typeof conn.tool_cache === "object"
        ? Object.keys(conn.tool_cache as Record<string, unknown>).length
        : 0
    : 0;

  return (
    <article className="env-card">
      <div className="env-card-head">
        <div className="env-title">
          <Plug size={18} weight="fill" />
          <h3>{conn.name}</h3>
        </div>
        <span className="cred-type">{conn.transport}</span>
      </div>

      <div className="env-meta">
        <code className="mcp-url">{conn.url}</code>
      </div>

      <div className="env-meta">
        Auth: <strong>{conn.auth_type}</strong>
        {" "}· Policy: <strong>{conn.enabled === false ? "disabled" : "enabled"}</strong>
        {conn.last_synced_at && (
          <>
            {" "}· Synced:{" "}
            {new Date(conn.last_synced_at).toLocaleString()}
          </>
        )}
      </div>

      <div className="env-packages">
        {toolCount > 0 && <span className="pkg-chip">{toolCount} tools cached</span>}
        {conn.allowed_tools?.length ? (
          <span className="pkg-chip">{conn.allowed_tools.length} allowed</span>
        ) : (
          <span className="pkg-chip">all tools allowed</span>
        )}
      </div>

      <ConnectionCallHistory connectionId={conn.id} />

      <div className="env-actions">
        <button
          className="btn btn-sm"
          onClick={onSync}
          disabled={syncing}
        >
          <ArrowsClockwise
            size={14}
            weight="bold"
            className={syncing ? "spin" : ""}
          />
          {syncing ? "Syncing..." : "Sync Tools"}
        </button>
        <button className="btn btn-sm btn-ghost" onClick={onEdit}>
          Edit
        </button>
        <button className="btn btn-sm btn-ghost" onClick={onDelete}>
          <Trash size={14} />
          Delete
        </button>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function McpConnectionsPage() {
  const { notify } = useToast();
  const confirm = useConfirm();

  const [connections, setConnections] = useState<MCPConnection[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState<MCPConnection | null>(null);
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set());
  const connectionsFetchCancelledRef = useRef(false);

  useEffect(() => {
    connectionsFetchCancelledRef.current = false;
    loadConnections();
    return () => {
      connectionsFetchCancelledRef.current = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadConnections() {
    setLoading(true);
    setError("");
    try {
      const data = await api.listMcpConnections();
      if (connectionsFetchCancelledRef.current) return;
      setConnections(data);
    } catch (err) {
      if (connectionsFetchCancelledRef.current) return;
      setError(userFriendlyError(err));
      setConnections([]);
    } finally {
      if (!connectionsFetchCancelledRef.current) setLoading(false);
    }
  }

  async function handleDelete(conn: MCPConnection) {
    const ok = await confirm({
      title: "Delete MCP connection?",
      body: `"${conn.name}" will be permanently removed. Any MCP tool nodes using this connection will fail until reconfigured.`,
    });
    if (!ok) return;
    try {
      await api.deleteMcpConnection(conn.id);
      notify("MCP connection deleted.", "success");
      await loadConnections();
    } catch (err) {
      notify(
        `Could not delete MCP connection. ${userFriendlyError(err)}`,
        "error",
      );
    }
  }

  async function handleSync(conn: MCPConnection) {
    setSyncingIds((prev) => new Set(prev).add(conn.id));
    try {
      const result = await api.syncMcpConnection(conn.id);
      notify(
        `Synced ${result.tools_discovered} tool${result.tools_discovered !== 1 ? "s" : ""}.`,
        "success",
      );
      await loadConnections();
    } catch (err) {
      notify(`Sync failed. ${userFriendlyError(err)}`, "error");
    } finally {
      setSyncingIds((prev) => {
        const next = new Set(prev);
        next.delete(conn.id);
        return next;
      });
    }
  }

  function openEdit(conn: MCPConnection) {
    setEditing(conn);
    setModal(true);
  }

  function openCreate() {
    setEditing(null);
    setModal(true);
  }

  function closeModal() {
    setModal(false);
    setEditing(null);
  }

  function onSaved() {
    closeModal();
    void loadConnections();
  }

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <h1>
            MCP Connections
            {connections && (
              <span className="home-count">{connections.length}</span>
            )}
          </h1>
          <button className="btn btn-primary" onClick={openCreate}>
            New connection
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}

        {loading && (
          <div role="status" className="env-grid" aria-label="Loading MCP connections">
            {Array.from({ length: 3 }).map((_, index) => (
              <article className="env-card skeleton-card" key={index}>
                <span className="skeleton-line short" />
                <span className="skeleton-line title" />
                <span className="skeleton-line" />
              </article>
            ))}
          </div>
        )}

        {!loading && connections && connections.length === 0 && (
          <EmptyState
            icon={<Plug size={48} />}
            title="No MCP connections yet"
            description="Add an MCP server connection to use MCP tools in your workflows."
            action={
              <button className="btn btn-primary" onClick={openCreate}>
                New connection
              </button>
            }
          />
        )}

        {!loading && connections && connections.length > 0 && (
          <div className="env-grid">
            {connections.map((conn) => (
              <ConnectionCard
                key={conn.id}
                conn={conn}
                onEdit={() => openEdit(conn)}
                onDelete={() => void handleDelete(conn)}
                onSync={() => void handleSync(conn)}
                syncing={syncingIds.has(conn.id)}
              />
            ))}
          </div>
        )}
      </main>

      {modal && (
        <CreateConnectionModal
          edit={editing}
          onClose={closeModal}
          onSaved={onSaved}
        />
      )}
    </div>
  );
}
