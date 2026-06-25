import { Handle, type NodeProps, Position } from "@xyflow/react";
import { Plus, X } from "@phosphor-icons/react";
import { memo, useEffect, useRef, useState } from "react";

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

// Port kinds offered when adding a boundary port (data kinds first, then AI).
const KIND_OPTIONS: string[] = [
  "any",
  "dataset",
  "artifact",
  "file",
  "ai_language_model",
  "ai_embedding_model",
  "ai_memory",
  "ai_tool",
  "ai_output_parser",
  "ai_retriever",
  "ai_vector_store",
  "ai_document_loader",
  "ai_guardrail",
];

// Slim, KNIME-style boundary pillar: a thin full-height bar flush to the canvas
// edge with typed (data-kind coloured) port stubs and a trailing "+ add".
const TOP = 44; // cap height
const ROW = 52; // spacing between ports
const ADD = 40; // add-row height
const MIN_H = 300;

function MetaBarComponent({ data }: NodeProps) {
  const { bar, ports } = data as unknown as MetaBarData;
  const addMetaPort = useEditor((s) => s.addMetaPort);
  const removeMetaPort = useEditor((s) => s.removeMetaPort);
  const isInput = bar === "input";
  const height = Math.max(MIN_H, TOP + ports.length * ROW + ADD);
  const portTop = (i: number) => TOP + i * ROW + ROW / 2;

  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!pickerOpen) return;
    const onDown = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setPickerOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [pickerOpen]);

  const choose = (kind: string) => {
    addMetaPort(bar, kind);
    setPickerOpen(false);
  };

  return (
    <div className={`meta-pillar meta-pillar-${bar}`} style={{ height }}>
      <div className="meta-pillar-cap">{isInput ? "IN" : "OUT"}</div>

      {ports.map((p, i) => {
        const color = portColor(p.data_kind);
        return (
          <div className="meta-pillar-port" key={p.id} style={{ top: portTop(i) }}>
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

      <div
        className="meta-pillar-addwrap nodrag"
        ref={pickerRef}
        style={{ top: TOP + ports.length * ROW + 6 }}
      >
        <button
          type="button"
          className="meta-pillar-add"
          title={`Add ${isInput ? "input" : "output"} port`}
          aria-haspopup="menu"
          aria-expanded={pickerOpen}
          onClick={() => setPickerOpen((o) => !o)}
        >
          <Plus size={12} weight="bold" />
        </button>
        {pickerOpen && (
          <div className={`meta-pillar-picker meta-pillar-picker-${bar}`} role="menu">
            <div className="meta-pillar-picker-head">Port type</div>
            {KIND_OPTIONS.map((kind) => (
              <button
                type="button"
                role="menuitem"
                key={kind}
                className="meta-pillar-picker-item"
                onClick={() => choose(kind)}
              >
                <span className="meta-pillar-picker-dot" style={{ background: portColor(kind) }} />
                {portKindLabel(kind)}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export const MetaBar = memo(
  MetaBarComponent,
  (previous, next) => previous.data === next.data,
);
