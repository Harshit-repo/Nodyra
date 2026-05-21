import { Handle, type NodeProps, Position } from "@xyflow/react";
import type { CSSProperties, MouseEvent } from "react";

import { categoryColor } from "../categories";
import { NodeIcon } from "../NodeIcon";
import { type NoodleNode, useEditor } from "./store";

const TILE = 72;

function portTop(index: number, count: number): number {
  return (TILE * (index + 1)) / (count + 1);
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

export function NodeCard({ id, data, selected }: NodeProps<NoodleNode>) {
  const { manifest, disabled, outputsOverride } = data;
  const color = categoryColor(manifest.category);
  const { inputs } = manifest;
  const outputNames = outputsOverride ?? manifest.outputs.map((o) => o.name);
  const runStatus = useEditor((s) => s.runStatus[id]);
  const running = useEditor((s) => s.running);
  const isPinned = useEditor((s) => Boolean(s.pinned[id]));

  const deleteNode = useEditor((s) => s.deleteNode);
  const toggleDisabled = useEditor((s) => s.toggleDisabled);
  const openNdv = useEditor((s) => s.openNdv);
  const runFromNode = useEditor((s) => s.runFromNode);

  const tileClass = ["node-tile"];
  if (selected) tileClass.push("selected");
  if (disabled) tileClass.push("is-disabled");
  if (isPinned) tileClass.push("is-pinned");
  if (runStatus) tileClass.push(`run-${runStatus}`);

  return (
    <div className="node" style={{ "--cat": color } as CSSProperties}>
      <div className="node-toolbar nodrag">
        <button
          type="button"
          title="Run from here"
          onClick={(e) => {
            stop(e);
            runFromNode(id);
          }}
          disabled={running}
        >
          ▶
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
      </div>

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

        {inputs.map((port, i) => (
          <Handle
            key={`in-${port.name}`}
            type="target"
            position={Position.Left}
            id={port.name}
            style={{ top: portTop(i, inputs.length), background: color }}
          />
        ))}

        {outputNames.map((name, i) => (
          <Handle
            key={`out-${name}`}
            type="source"
            position={Position.Right}
            id={name}
            style={{ top: portTop(i, outputNames.length), background: color }}
          />
        ))}

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
    </div>
  );
}
