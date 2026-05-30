import { Handle, type NodeProps, Position } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, MouseEvent } from "react";

import { categoryColor } from "../categories";
import { NodeIcon } from "../NodeIcon";
import { SdkModal } from "./SdkModal";
import { type NoodleNode, useEditor } from "./store";

const TILE = 72;
const TOOLBAR_HIDE_DELAY_MS = 1000;

function portTop(index: number, count: number): number {
  return (TILE * (index + 1)) / (count + 1);
}

const PORT_KIND_COLOR: Record<string, string> = {
  dataset: "#7c5cff",
  artifact: "#f59e0b",
  file: "#f59e0b",
  control: "#94a3b8",
};

function portColor(kind: string | undefined, fallback: string): string {
  if (!kind || kind === "any") return fallback;
  return PORT_KIND_COLOR[kind] ?? fallback;
}

const STATUS_GLYPH: Record<string, string> = {
  success: "✓",
  error: "!",
  skipped: "–",
  cancelled: "■",
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

export function NodeCard({ id, data, selected }: NodeProps<NoodleNode>) {
  const [toolbarVisible, setToolbarVisible] = useState(false);
  const hideTimerRef = useRef<number | null>(null);
  const { manifest, disabled, outputsOverride } = data;
  const isWebhook = manifest.id === "webhook_trigger";
  const color = categoryColor(manifest.category);
  const { inputs } = manifest;
  const outputNames = outputsOverride ?? manifest.outputs.map((o) => o.name);
  const runStatus = useEditor((s) => s.runStatus[id]);
  const runMeta = useEditor((s) => s.runMeta[id]);
  const running = useEditor((s) => s.running);
  const isPinned = useEditor((s) => Boolean(s.pinned[id]));
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
  const runFromNode = useEditor((s) => s.runFromNode);
  const runFromTrigger = useEditor((s) => s.runFromTrigger);
  const isTrigger = manifest.category === "Triggers";
  const devMode = useEditor((s) => s.devMode);
  const [sdkModalOpen, setSdkModalOpen] = useState(false);

  const tileClass = ["node-tile"];
  if (selected) tileClass.push("selected");
  if (disabled) tileClass.push("is-disabled");
  if (isPinned) tileClass.push("is-pinned");
  if (hasMissingCredential) tileClass.push("missing-credential");
  if (hasInlineSecret) tileClass.push("inline-secret");
  if (runStatus) tileClass.push(`run-${runStatus}`);

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

  return (
    <div
      className={`node${toolbarVisible ? " is-toolbar-visible" : ""}`}
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
            isWebhook
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
          disabled={running}
        >
          ▶
        </button>
        <button
          type="button"
          title={
            isTrigger
              ? "Run this trigger and its downstream nodes"
              : "Run step fresh, recomputing upstream nodes"
          }
          onClick={(e) => {
            stop(e);
            if (isTrigger) runFromTrigger(id);
            else runFromNode(id, { reuseUpstream: false });
          }}
          disabled={running}
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

      <div className={tileClass.join(" ")}>
        <NodeIcon name={manifest.icon} size={26} />

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
        <div className="node-badges" aria-hidden>
          {hasMissingCredential && <span title="Missing stored credential">key</span>}
          {hasInlineSecret && <span title="Inline secret should be moved">!</span>}
          {credentialSpecs.some((param) => isCredentialRef(data.params[param.name])) && (
            <span title="Uses stored credential">lock</span>
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



        {inputs.map((port, i) => (
          <Handle
            key={`in-${port.name}`}
            type="target"
            position={Position.Left}
            id={port.name}
            style={{ top: portTop(i, inputs.length), background: portColor(port.data_kind, color) }}
          />
        ))}

        {outputNames.map((name, i) => {
          const spec = manifest.outputs.find((o) => o.name === name);
          return (
            <Handle
              key={`out-${name}`}
              type="source"
              position={Position.Right}
              id={name}
              style={{ top: portTop(i, outputNames.length), background: portColor(spec?.data_kind, color) }}
            />
          );
        })}

        {outputNames.length > 1 &&
          outputNames.map((name, i) => (
            <span
              key={`tag-${name}`}
              className="port-tag"
              style={{ top: portTop(i, outputNames.length) }}
            >
              {name}
            </span>
          ))}
      </div>
      <div className="node-label">{manifest.name}</div>
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
