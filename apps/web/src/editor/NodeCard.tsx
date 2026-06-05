import { Key, Lock, Warning } from "@phosphor-icons/react";
import { Handle, type NodeProps, Position, useUpdateNodeInternals } from "@xyflow/react";
import { Fragment, useEffect, useRef, useState } from "react";
import type { CSSProperties, MouseEvent } from "react";

import { categoryColor } from "../categories";
import { isBrandIconName, NodeIcon } from "../NodeIcon";
import { missingFor } from "./missingPackages";
import { SdkModal } from "./SdkModal";
import { type NoodleNode, useEditor } from "./store";

const TILE = 72;
const AGENT_CARD_HEIGHT = 150;
const TOOLBAR_HIDE_DELAY_MS = 1000;

const AGENT_INPUT_PORTS = [
  { name: "input", label: "Chat Input" },
  { name: "model", label: "Model" },
  { name: "memory", label: "Memory" },
  { name: "tool", label: "Tools" },
  { name: "parser", label: "Parser" },
  { name: "guardrail", label: "Guardrail" },
] as const;

const AGENT_OUTPUT_LABELS: Record<string, string> = {
  main: "Response",
};

function portTop(index: number, count: number): number {
  return (TILE * (index + 1)) / (count + 1);
}

function agentPortTop(index: number, count: number): number {
  return (AGENT_CARD_HEIGHT * (index + 1)) / (count + 1);
}

const PORT_KIND_COLOR: Record<string, string> = {
  dataset: "#7c5cff",
  artifact: "#f59e0b",
  file: "#f59e0b",
  control: "#94a3b8",
  ai_language_model: "#6ea8ff",
  ai_embedding_model: "#6ea8ff",
  ai_memory: "#57c98a",
  ai_tool: "#f6b44b",
  ai_output_parser: "#c084fc",
  ai_retriever: "#22d3ee",
  ai_vector_store: "#2dd4bf",
  ai_document_loader: "#38bdf8",
  ai_guardrail: "#fb7185",
};
type AiSemanticPort = "model" | "memory" | "tools";

const AI_PORT_COLOR: Record<AiSemanticPort, string> = {
  model: "#6ea8ff",
  memory: "#57c98a",
  tools: "#f6b44b",
};
const AI_AGENT_BOTTOM_INPUTS = new Set(["model", "memory", "tools"]);

function portColor(kind: string | undefined, fallback: string): string {
  if (!kind || kind === "any") return fallback;
  return PORT_KIND_COLOR[kind] ?? fallback;
}

function aiPortSemantic(
  manifestId: string,
  portName: string,
): AiSemanticPort | undefined {
  if (portName === "model" || portName === "memory" || portName === "tools") {
    return portName;
  }
  if (manifestId === "ai_tool" && portName === "main") return "tools";
  if (manifestId === "ai_tool_box" && portName.startsWith("tool_")) return "tools";
  return undefined;
}

function semanticPortColor(
  manifestId: string,
  name: string,
  kind: string | undefined,
  fallback: string,
): string {
  const semantic = aiPortSemantic(manifestId, name);
  return semantic ? AI_PORT_COLOR[semantic] : portColor(kind, fallback);
}

function portHandleClass(
  manifestId: string,
  name: string,
  kind: string | undefined,
): string | undefined {
  const classes: string[] = [];
  if (kind === "dataset") classes.push("handle-dataset");
  if (kind === "ai_language_model" || kind === "ai_embedding_model") {
    classes.push("handle-ai-model");
  }
  if (kind === "ai_memory") classes.push("handle-ai-memory");
  if (kind === "ai_tool") classes.push("handle-ai-tools");
  const semantic = aiPortSemantic(manifestId, name);
  if (semantic) classes.push(`handle-ai-${semantic}`);
  return classes.length > 0 ? Array.from(new Set(classes)).join(" ") : undefined;
}

function portLeft(index: number, count: number): number {
  return (TILE * (index + 1)) / (count + 1);
}

function isAgentBottomInput(manifestId: string, portName: string): boolean {
  return manifestId === "ai_agent" && AI_AGENT_BOTTOM_INPUTS.has(portName);
}

function portKindLabel(kind: string | undefined): string {
  if (kind === "dataset") return "DatasetRef";
  if (kind === "artifact") return "Artifact";
  if (kind === "file") return "File";
  if (kind === "control") return "Control";
  if (kind === "main") return "Main data";
  if (kind === "ai_language_model") return "AI language model";
  if (kind === "ai_embedding_model") return "AI embedding model";
  if (kind === "ai_memory") return "AI memory";
  if (kind === "ai_tool") return "AI tool";
  if (kind === "ai_output_parser") return "AI output parser";
  if (kind === "ai_retriever") return "AI retriever";
  if (kind === "ai_vector_store") return "AI vector store";
  if (kind === "ai_document_loader") return "AI document loader";
  if (kind === "ai_guardrail") return "AI guardrail";
  return "Any data";
}

const STATUS_GLYPH: Record<string, string> = {
  success: "✓",
  error: "!",
  skipped: "–",
  cancelled: "■",
  waiting: "…",
};

function stop(event: MouseEvent): void {
  event.stopPropagation();
}

function isCredentialRef(value: unknown): boolean {
  return (
    Boolean(value) &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).__noodle_credential__ === true
  );
}

function compactLabel(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.trim();
}

export function NodeCard({ id, data, selected }: NodeProps<NoodleNode>) {
  const [toolbarVisible, setToolbarVisible] = useState(false);
  const hideTimerRef = useRef<number | null>(null);
  const { manifest, disabled, outputsOverride } = data;
  const isWebhook = manifest.id === "webhook_trigger";
  const isAgentV2 = manifest.id === "ai_agent_v2";
  const hasBrandIcon = isBrandIconName(manifest.icon);
  const color = categoryColor(manifest.category);
  const { inputs } = manifest;
  // In tool mode the node is invoked by the Agent, not wired from upstream, so
  // it exposes only its `tool` output — hide every incoming port.
  const sideInputs = data.toolMode
    ? []
    : inputs.filter((port) => !isAgentBottomInput(manifest.id, port.name));
  const bottomInputs = data.toolMode
    ? []
    : inputs.filter((port) => isAgentBottomInput(manifest.id, port.name));
  const outputNames = data.toolMode
    ? ["tool"]
    : outputsOverride ?? manifest.outputs.map((o) => o.name);
  // Toggling tool mode (and editing switch/code outputs) changes which handles
  // exist. React Flow caches handle bounds per node, so without an explicit
  // re-measure the new `tool` handle isn't registered and a connection can't be
  // started from it. Re-measure whenever the handle set changes.
  const updateNodeInternals = useUpdateNodeInternals();
  const handleSignature = `${data.toolMode ? "tool" : "normal"}|${sideInputs.length}|${bottomInputs.length}|${outputNames.join(",")}`;
  useEffect(() => {
    updateNodeInternals(id);
  }, [id, handleSignature, updateNodeInternals]);
  const runStatus = useEditor((s) => s.runStatus[id]);
  const runMeta = useEditor((s) => s.runMeta[id]);
  const running = useEditor((s) => s.running);
  const isPinned = useEditor((s) => Boolean(s.pinned[id]));
  const envPackages = useEditor((s) => s.envPackages);
  const missingPkgs = missingFor(manifest.requirements ?? [], envPackages);
  const agentModelLabel = useEditor((s) => {
    if (!isAgentV2) return "";
    const modelEdge = s.edges.find(
      (edge) => edge.target === id && (edge.targetHandle ?? "input") === "model",
    );
    const sourceNode = modelEdge
      ? s.nodes.find((node) => node.id === modelEdge.source)
      : undefined;
    const params = sourceNode?.data.params ?? {};
    return (
      compactLabel(params.model) ||
      compactLabel(params.deployment) ||
      compactLabel(params.model_name) ||
      compactLabel(params.deployment_name) ||
      sourceNode?.data.manifest.name ||
      ""
    );
  });
  const credentialSpecs = manifest.params.filter((param) => param.credential);
  const hasInlineSecret = credentialSpecs.some((param) => {
    const value = data.params[param.name];
    return typeof value === "string" && value.trim().length > 0;
  });
  const hasMissingCredential = credentialSpecs.some((param) => {
    const value = data.params[param.name];
    return value === null || value === undefined || value === "";
  });

  const deleteNode = useEditor((s) => s.deleteNode);
  const toggleDisabled = useEditor((s) => s.toggleDisabled);
  const openNdv = useEditor((s) => s.openNdv);
  const openChat = useEditor((s) => s.openChat);
  const isChatTrigger = manifest.id === "chat_trigger";
  const runFromNode = useEditor((s) => s.runFromNode);
  const runFromTrigger = useEditor((s) => s.runFromTrigger);
  const isTrigger = manifest.category === "Triggers";
  const devMode = useEditor((s) => s.devMode);
  const [sdkModalOpen, setSdkModalOpen] = useState(false);

  // A non-trigger node may only be run individually when it is wired
  // (directly or transitively) to a trigger. Otherwise stray action nodes
  // like Execute Command could fire on their own with no trigger context.
  const hasTriggerUpstream = useEditor((s) => {
    if (isTrigger) return true;
    const bySource = new Map<string, string[]>();
    for (const e of s.edges) {
      const arr = bySource.get(e.target);
      if (arr) arr.push(e.source);
      else bySource.set(e.target, [e.source]);
    }
    const catById = new Map(
      s.nodes.map((n) => [n.id, n.data.manifest.category]),
    );
    const visited = new Set<string>([id]);
    const queue = [id];
    while (queue.length) {
      const cur = queue.pop() as string;
      for (const prev of bySource.get(cur) ?? []) {
        if (visited.has(prev)) continue;
        visited.add(prev);
        if (catById.get(prev) === "Triggers") return true;
        queue.push(prev);
      }
    }
    return false;
  });
  const canRunStep = isTrigger || hasTriggerUpstream;

  const tileClass = ["node-tile"];
  if (selected) tileClass.push("selected");
  if (disabled) tileClass.push("is-disabled");
  if (isPinned) tileClass.push("is-pinned");
  if (hasBrandIcon) tileClass.push("has-brand-icon");
  if (hasMissingCredential) tileClass.push("missing-credential");
  if (hasInlineSecret) tileClass.push("inline-secret");
  if (runStatus) tileClass.push(`run-${runStatus}`);
  const nodeClass = ["node"];
  if (isAgentV2) nodeClass.push("agent-node");
  if (toolbarVisible) nodeClass.push("is-toolbar-visible");
  if (bottomInputs.length > 0) nodeClass.push("has-bottom-inputs");

  function clearHideTimer(): void {
    if (hideTimerRef.current !== null) {
      window.clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
  }

  function showToolbar(): void {
    clearHideTimer();
    setToolbarVisible(true);
  }

  function scheduleToolbarHide(): void {
    clearHideTimer();
    hideTimerRef.current = window.setTimeout(() => {
      hideTimerRef.current = null;
      setToolbarVisible(false);
    }, TOOLBAR_HIDE_DELAY_MS);
  }

  useEffect(
    () => () => {
      clearHideTimer();
    },
    [],
  );

  const toolbar = (
    <div
      className="node-toolbar nodrag"
      onMouseEnter={showToolbar}
      onMouseLeave={scheduleToolbarHide}
    >
      <button
        type="button"
        title={
          !canRunStep
            ? "Connect a trigger upstream to run this node"
            : isWebhook
              ? "Listen for test event"
              : isTrigger
                ? "Run this trigger and its downstream nodes"
                : "Run step using current upstream data"
        }
        onClick={(e) => {
          stop(e);
          if (isTrigger) runFromTrigger(id);
          else runFromNode(id);
        }}
        disabled={running || !canRunStep}
      >
        ▶
      </button>
      <button
        type="button"
        title={
          !canRunStep
            ? "Connect a trigger upstream to run this node"
            : isTrigger
              ? "Run this trigger and its downstream nodes"
              : "Run step fresh, recomputing upstream nodes"
        }
        onClick={(e) => {
          stop(e);
          if (isTrigger) runFromTrigger(id);
          else runFromNode(id, { reuseUpstream: false });
        }}
        disabled={running || !canRunStep}
      >
        ↻
      </button>
      <button
        type="button"
        title="Open details"
        onClick={(e) => {
          stop(e);
          openNdv(id);
        }}
      >
        ⤢
      </button>
      {isChatTrigger && (
        <button
          type="button"
          className="toolbar-chat"
          title="Open chat"
          onClick={(e) => {
            stop(e);
            openChat();
          }}
        >
          💬
        </button>
      )}
      <button
        type="button"
        className={`toolbar-disable${disabled ? " is-on" : ""}`}
        title={disabled ? "Enable node" : "Disable node"}
        onClick={(e) => {
          stop(e);
          toggleDisabled(id);
        }}
      >
        {disabled ? "●" : "◐"}
      </button>
      <button
        type="button"
        className="toolbar-delete"
        title="Delete node"
        onClick={(e) => {
          stop(e);
          deleteNode(id);
        }}
      >
        ×
      </button>
      {devMode && (
        <button
          type="button"
          className="toolbar-sdk"
          title="Python SDK snippet"
          onClick={(e) => {
            stop(e);
            setSdkModalOpen(true);
          }}
        >
          {"</>"}
        </button>
      )}
    </div>
  );

  const credentialBadges = (
    <div className="node-badges">
      {hasMissingCredential && (
        <span title="Missing stored credential">
          <Key size={11} weight="bold" aria-hidden />
        </span>
      )}
      {hasInlineSecret && (
        <span title="Inline secret should be moved to a stored credential">
          <Warning size={11} weight="bold" aria-hidden />
        </span>
      )}
      {credentialSpecs.some((param) => isCredentialRef(data.params[param.name])) && (
        <span title="Uses stored credential">
          <Lock size={11} weight="bold" aria-hidden />
        </span>
      )}
    </div>
  );

  if (isAgentV2) {
    const inputByName = new Map(manifest.inputs.map((port) => [port.name, port]));
    const agentInputs = AGENT_INPUT_PORTS.map((config) => {
      const spec = inputByName.get(config.name);
      return spec
        ? {
            ...config,
            spec,
            color: semanticPortColor(
              manifest.id,
              config.name,
              spec.data_kind,
              "#24d9a5",
            ),
          }
        : null;
    }).filter((item): item is NonNullable<typeof item> => Boolean(item));
    const agentOutputs = outputNames.map((name) => {
      const spec = manifest.outputs.find((port) => port.name === name);
      const colorForOutput = semanticPortColor(
        manifest.id,
        name,
        spec?.data_kind,
        "#24d9a5",
      );
      return {
        name,
        spec,
        label: AGENT_OUTPUT_LABELS[name] ?? name,
        color: colorForOutput,
      };
    });
    const agentCardClass = ["agent-node-card"];
    if (selected) agentCardClass.push("selected");
    if (disabled) agentCardClass.push("is-disabled");
    if (isPinned) agentCardClass.push("is-pinned");
    if (hasMissingCredential) agentCardClass.push("missing-credential");
    if (hasInlineSecret) agentCardClass.push("inline-secret");
    if (runStatus) agentCardClass.push(`run-${runStatus}`);

    return (
      <div
        className={nodeClass.join(" ")}
        style={{ "--cat": color } as CSSProperties}
        onMouseEnter={showToolbar}
        onMouseLeave={scheduleToolbarHide}
      >
        {toolbar}
        {sdkModalOpen && (
          <SdkModal
            nodeId={id}
            manifestId={manifest.id}
            onClose={() => setSdkModalOpen(false)}
          />
        )}
        <div
          className={agentCardClass.join(" ")}
          onDoubleClick={() => openNdv(id)}
          title="Double-click to open details"
        >
          <div className="agent-node-glow" />
          <div className="agent-node-face">
            <NodeIcon name={manifest.icon} size={26} />
          </div>
          <div className="agent-node-title">{manifest.name}</div>
          <div
            className={`agent-model-pill${agentModelLabel ? "" : " is-empty"}`}
            title={agentModelLabel || "Connect an AI Chat Model to the model port"}
          >
            <NodeIcon name="ai" size={16} />
            <span>{agentModelLabel || "Connect model"}</span>
          </div>

          {runStatus && (
            <span className={`node-status status-run-${runStatus}`}>
              {runStatus === "running" ? (
                <span className="node-spinner" />
              ) : (
                STATUS_GLYPH[runStatus] ?? ""
              )}
            </span>
          )}
          {disabled && <span className="node-disabled-pip">○</span>}
          {credentialBadges}

          {runMeta?.error && runStatus === "error" && (
            <div
              className="node-error-callout nodrag nopan"
              title={runMeta.error}
            >
              {runMeta.error.length > 52
                ? runMeta.error.slice(0, 49) + "…"
                : runMeta.error}
            </div>
          )}

          {agentInputs.map((item, i) => {
            const top = agentPortTop(i, agentInputs.length);
            return (
              <Fragment key={`agent-in-${item.name}`}>
                <div
                  className="agent-port-row agent-port-row-left"
                  style={
                    {
                      top,
                      "--port-color": item.color,
                    } as CSSProperties
                  }
                >
                  <span className="agent-port-chip">{item.label}</span>
                  <span className="agent-port-wire" />
                </div>
                <Handle
                  type="target"
                  position={Position.Left}
                  id={item.name}
                  title={`${item.name}: ${portKindLabel(item.spec.data_kind)}`}
                  className={portHandleClass(
                    manifest.id,
                    item.name,
                    item.spec.data_kind,
                  )}
                  style={{
                    top,
                    color: item.color,
                    background: item.color,
                  }}
                />
              </Fragment>
            );
          })}

          {agentOutputs.map((item, i) => {
            const top = agentPortTop(i, agentOutputs.length);
            return (
              <Fragment key={`agent-out-${item.name}`}>
                <Handle
                  type="source"
                  position={Position.Right}
                  id={item.name}
                  title={`${item.name}: ${portKindLabel(item.spec?.data_kind)}`}
                  className={portHandleClass(
                    manifest.id,
                    item.name,
                    item.spec?.data_kind,
                  )}
                  style={{
                    top,
                    color: item.color,
                    background: item.color,
                  }}
                />
                <div
                  className="agent-port-row agent-port-row-right"
                  style={
                    {
                      top,
                      "--port-color": item.color,
                    } as CSSProperties
                  }
                >
                  <span className="agent-port-wire" />
                  <span className="agent-port-chip">{item.label}</span>
                </div>
              </Fragment>
            );
          })}
        </div>
        {runMeta?.durationMs != null && runStatus !== "running" && (
          <div className="node-duration nodrag nopan">
            {runMeta.durationMs < 1000
              ? `${Math.round(runMeta.durationMs)}ms`
              : `${(runMeta.durationMs / 1000).toFixed(1)}s`}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className={nodeClass.join(" ")}
      style={{ "--cat": color } as CSSProperties}
      onMouseEnter={showToolbar}
      onMouseLeave={scheduleToolbarHide}
    >
      <div
        className="node-toolbar nodrag"
        onMouseEnter={showToolbar}
        onMouseLeave={scheduleToolbarHide}
      >
        <button
          type="button"
          title={
            !canRunStep
              ? "Connect a trigger upstream to run this node"
              : isWebhook
                ? "Listen for test event"
                : isTrigger
                  ? "Run this trigger and its downstream nodes"
                  : "Run step using current upstream data"
          }
          onClick={(e) => {
            stop(e);
            if (isTrigger) runFromTrigger(id);
            else runFromNode(id);
          }}
          disabled={running || !canRunStep}
        >
          ▶
        </button>
        <button
          type="button"
          title={
            !canRunStep
              ? "Connect a trigger upstream to run this node"
              : isTrigger
                ? "Run this trigger and its downstream nodes"
                : "Run step fresh, recomputing upstream nodes"
          }
          onClick={(e) => {
            stop(e);
            if (isTrigger) runFromTrigger(id);
            else runFromNode(id, { reuseUpstream: false });
          }}
          disabled={running || !canRunStep}
        >
          ↻
        </button>
        <button
          type="button"
          title="Open details"
          onClick={(e) => {
            stop(e);
            openNdv(id);
          }}
        >
          ⤢
        </button>
        {isChatTrigger && (
          <button
            type="button"
            className="toolbar-chat"
            title="Open chat"
            onClick={(e) => {
              stop(e);
              openChat();
            }}
          >
            💬
          </button>
        )}
        <button
          type="button"
          className={`toolbar-disable${disabled ? " is-on" : ""}`}
          title={disabled ? "Enable node" : "Disable node"}
          onClick={(e) => {
            stop(e);
            toggleDisabled(id);
          }}
        >
          {disabled ? "●" : "◐"}
        </button>
        <button
          type="button"
          className="toolbar-delete"
          title="Delete node"
          onClick={(e) => {
            stop(e);
            deleteNode(id);
          }}
        >
          ×
        </button>
        {devMode && (
          <button
            type="button"
            className="toolbar-sdk"
            title="Python SDK snippet"
            onClick={(e) => {
              stop(e);
              setSdkModalOpen(true);
            }}
          >
            {"</>"}
          </button>
        )}
      </div>

      {sdkModalOpen && (
        <SdkModal
          nodeId={id}
          manifestId={manifest.id}
          onClose={() => setSdkModalOpen(false)}
        />
      )}

      <div
        className={tileClass.join(" ")}
        onDoubleClick={isChatTrigger ? () => openChat() : undefined}
        title={isChatTrigger ? "Double-click to open chat" : undefined}
      >
        <NodeIcon name={manifest.icon} size={hasBrandIcon ? 54 : 26} />

        {runStatus && (
          <span className={`node-status status-run-${runStatus}`}>
            {runStatus === "running" ? (
              <span className="node-spinner" />
            ) : (
              STATUS_GLYPH[runStatus] ?? ""
            )}
          </span>
        )}

        {disabled && <span className="node-disabled-pip">○</span>}
        <div className="node-badges">
          {hasMissingCredential && (
            <span title="Missing stored credential">
              <Key size={11} weight="bold" aria-hidden />
            </span>
          )}
          {hasInlineSecret && (
            <span title="Inline secret should be moved to a stored credential">
              <Warning size={11} weight="bold" aria-hidden />
            </span>
          )}
          {credentialSpecs.some((param) => isCredentialRef(data.params[param.name])) && (
            <span title="Uses stored credential">
              <Lock size={11} weight="bold" aria-hidden />
            </span>
          )}
        </div>

        {runMeta?.error && runStatus === "error" && (
          <div
            className="node-error-callout nodrag nopan"
            title={runMeta.error}
          >
            {runMeta.error.length > 52
              ? runMeta.error.slice(0, 49) + "…"
              : runMeta.error}
          </div>
        )}



        {sideInputs.map((port, i) => (
          <Handle
            key={`in-${port.name}`}
            type="target"
            position={Position.Left}
            id={port.name}
            title={`${port.name}: ${portKindLabel(port.data_kind)}`}
            className={portHandleClass(manifest.id, port.name, port.data_kind)}
            style={{
              top: portTop(i, sideInputs.length),
              background: semanticPortColor(manifest.id, port.name, port.data_kind, color),
            }}
          />
        ))}

        {bottomInputs.map((port, i) => (
          <Handle
            key={`in-bottom-${port.name}`}
            type="target"
            position={Position.Bottom}
            id={port.name}
            title={`${port.name}: ${portKindLabel(port.data_kind)}`}
            className={portHandleClass(manifest.id, port.name, port.data_kind)}
            style={{
              left: portLeft(i, bottomInputs.length),
              background: semanticPortColor(manifest.id, port.name, port.data_kind, color),
            }}
          />
        ))}

        {outputNames.map((name, i) => {
          const spec = manifest.outputs.find((o) => o.name === name);
          const isToolPort = Boolean(data.toolMode) && name === "tool";
          return (
            <Handle
              key={`out-${name}`}
              type="source"
              position={Position.Right}
              id={name}
              title={isToolPort ? "tool: AI tool" : `${name}: ${portKindLabel(spec?.data_kind)}`}
              className={
                isToolPort
                  ? "handle-ai-tools"
                  : portHandleClass(manifest.id, name, spec?.data_kind)
              }
              style={{
                top: portTop(i, outputNames.length),
                background: isToolPort
                  ? PORT_KIND_COLOR.ai_tool
                  : semanticPortColor(manifest.id, name, spec?.data_kind, color),
              }}
            />
          );
        })}

        {outputNames.map((name, i) => {
          const spec = manifest.outputs.find((o) => o.name === name);
          const isToolPort = Boolean(data.toolMode) && name === "tool";
          const shouldShow =
            outputNames.length > 1 || spec?.data_kind === "dataset" || isToolPort;
          if (!shouldShow) return null;
          return (
            <span
              key={`tag-${name}`}
              className={`port-tag${spec?.data_kind === "dataset" ? " port-tag-dataset" : ""}`}
              style={{ top: portTop(i, outputNames.length) }}
            >
              {isToolPort ? "tool" : spec?.data_kind === "dataset" ? `${name} · DatasetRef` : name}
            </span>
          );
        })}
      </div>
      <div className="node-label">
        {manifest.name}
        {missingPkgs.length > 0 && (
          <span
            className="node-missing-pkg"
            title={`Missing package(s): ${missingPkgs.join(", ")}`}
            aria-label="Missing required package"
          >
            ⚠
          </span>
        )}
      </div>
      {runMeta?.durationMs != null && runStatus !== "running" && (
        <div className="node-duration nodrag nopan">
          {runMeta.durationMs < 1000
            ? `${Math.round(runMeta.durationMs)}ms`
            : `${(runMeta.durationMs / 1000).toFixed(1)}s`}
        </div>
      )}
    </div>
  );
}
