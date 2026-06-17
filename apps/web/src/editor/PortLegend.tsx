import { Panel } from "@xyflow/react";

const ITEMS = [
  { color: "#94a3b8", label: "Data / Any" },
  { color: "#7c5cff", label: "DatasetRef" },
  { color: "#f97316", label: "Artifact" },
  { color: "#34d399", label: "File" },
  { color: "#6ea8ff", label: "AI Model" },
  { color: "#f6b44b", label: "AI Tool" },
  { color: "#57c98a", label: "Memory" },
  { color: "#c084fc", label: "Parser" },
  { color: "#22d3ee", label: "Retriever" },
  { color: "#2dd4bf", label: "Vector Store" },
  { color: "#38bdf8", label: "Doc Loader" },
  { color: "#fb7185", label: "Guardrail" },
];

export function PortLegend() {
  return (
    <Panel position="bottom-left" className="port-legend nodrag nopan">
      <div className="port-legend-grid">
        {ITEMS.map((item) => (
          <div key={item.label} className="port-legend-row">
            <span className="port-legend-dot" style={{ background: item.color }} />
            <span>{item.label}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
