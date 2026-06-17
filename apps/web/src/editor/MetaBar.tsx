import { Handle, type NodeProps, Position } from "@xyflow/react";
import { Plus, X } from "@phosphor-icons/react";

import { useEditor } from "./store";

interface MetaBarData {
  bar: "input" | "output";
  ports: { id: string; label: string }[];
}

const ROW_H = 40;
const HEAD_H = 34;

export function MetaBar({ data }: NodeProps) {
  const { bar, ports } = data as unknown as MetaBarData;
  const addMetaPort = useEditor((s) => s.addMetaPort);
  const removeMetaPort = useEditor((s) => s.removeMetaPort);
  const isInput = bar === "input";
  const height = HEAD_H + ports.length * ROW_H + ROW_H;

  return (
    <div className={`meta-bar meta-bar-${bar}`} style={{ height }}>
      <div className="meta-bar-head">{isInput ? "Inputs" : "Outputs"}</div>
      {ports.map((p, i) => (
        <div className="meta-bar-row" key={p.id} style={{ top: HEAD_H + i * ROW_H }}>
          <span className="meta-bar-label">{p.label}</span>
          <button
            type="button"
            className="meta-bar-remove nodrag"
            aria-label={`Remove ${bar} port ${p.label}`}
            title="Remove port"
            onClick={() => removeMetaPort(bar, p.id)}
          >
            <X size={11} weight="bold" />
          </button>
          <Handle
            type={isInput ? "source" : "target"}
            position={isInput ? Position.Right : Position.Left}
            id={p.id}
            style={{ top: HEAD_H + i * ROW_H + ROW_H / 2 }}
          />
        </div>
      ))}
      <button
        type="button"
        className="meta-bar-add nodrag"
        style={{ top: HEAD_H + ports.length * ROW_H }}
        onClick={() => addMetaPort(bar)}
      >
        <Plus size={11} weight="bold" /> Add {isInput ? "input" : "output"}
      </button>
    </div>
  );
}
