import { Handle, type NodeProps, Position } from "@xyflow/react";
import { Plus, X } from "@phosphor-icons/react";

import { portColor, portKindLabel } from "./NodeCard";
import { useEditor } from "./store";

interface MetaBarPort {
  id: string;
  label: string;
  data_kind?: string;
}

interface MetaBarData {
  bar: "input" | "output";
  ports: MetaBarPort[];
}

// Slim, KNIME-style boundary pillar: a thin full-height bar flush to the canvas
// edge with typed (data-kind coloured) port stubs and a trailing "+ add".
const TOP = 44; // cap height
const ROW = 52; // spacing between ports
const ADD = 40; // add-row height
const MIN_H = 300;

export function MetaBar({ data }: NodeProps) {
  const { bar, ports } = data as unknown as MetaBarData;
  const addMetaPort = useEditor((s) => s.addMetaPort);
  const removeMetaPort = useEditor((s) => s.removeMetaPort);
  const isInput = bar === "input";
  const height = Math.max(MIN_H, TOP + ports.length * ROW + ADD);
  const portTop = (i: number) => TOP + i * ROW + ROW / 2;

  return (
    <div className={`meta-pillar meta-pillar-${bar}`} style={{ height }}>
      <div className="meta-pillar-cap">{isInput ? "IN" : "OUT"}</div>

      {ports.map((p, i) => {
        const color = portColor(p.data_kind);
        return (
          <div
            className="meta-pillar-port"
            key={p.id}
            style={{ top: portTop(i) }}
          >
            <span className="meta-pillar-tag">
              <span className="meta-pillar-name">
                {p.label}
                <button
                  type="button"
                  className="meta-pillar-remove nodrag"
                  aria-label={`Remove ${bar} port ${p.label}`}
                  title="Remove port"
                  onClick={() => removeMetaPort(bar, p.id)}
                >
                  <X size={10} weight="bold" />
                </button>
              </span>
              <span className="meta-pillar-kind" style={{ color }}>
                {portKindLabel(p.data_kind)}
              </span>
            </span>
            <Handle
              type={isInput ? "source" : "target"}
              position={isInput ? Position.Right : Position.Left}
              id={p.id}
              className="meta-pillar-handle"
              style={{ top: "50%", background: color, borderColor: color }}
            />
          </div>
        );
      })}

      <button
        type="button"
        className="meta-pillar-add nodrag"
        style={{ top: TOP + ports.length * ROW + 6 }}
        title={`Add ${isInput ? "input" : "output"} port`}
        onClick={() => addMetaPort(bar)}
      >
        <Plus size={12} weight="bold" />
      </button>
    </div>
  );
}
