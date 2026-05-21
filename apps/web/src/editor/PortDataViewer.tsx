import type { Edge } from "@xyflow/react";
import type { CSSProperties, MouseEvent as ReactMouseEvent } from "react";
import { useEffect, useRef, useState } from "react";

import { useEditor, type NoodleNode } from "./store";

const MIN_HEIGHT = 160;
const DEFAULT_HEIGHT = 248;
const MAX_HEIGHT = 430;
const COLLAPSED_HEIGHT = 44;

function outputNames(node: NoodleNode): string[] {
  return node.data.outputsOverride ?? node.data.manifest.outputs.map((o) => o.name);
}

function valueAtPort(
  outputs: unknown,
  port: string,
): { value: unknown; hasValue: boolean } {
  if (outputs && typeof outputs === "object" && !Array.isArray(outputs)) {
    const record = outputs as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(record, port)) {
      return { value: record[port], hasValue: true };
    }
  }
  return { value: undefined, hasValue: false };
}

function pretty(value: unknown): string {
  if (value === undefined) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function edgeLabel(edge: Edge | undefined, direction: "input" | "output"): string {
  if (!edge) return direction === "input" ? "not connected" : "no connection";
  if (direction === "input") {
    return `${edge.source}.${edge.sourceHandle ?? "main"}`;
  }
  return `${edge.target}.${edge.targetHandle ?? "input"}`;
}

function PortCard({
  name,
  direction,
  connectedEdge,
  value,
  hasValue,
  pinned,
}: {
  name: string;
  direction: "input" | "output";
  connectedEdge?: Edge;
  value: unknown;
  hasValue: boolean;
  pinned?: boolean;
}) {
  const className = [
    "port-data-card",
    hasValue ? "has-data" : "is-empty",
    connectedEdge ? "is-connected" : "is-disconnected",
  ].join(" ");

  return (
    <article className={className}>
      <header className="port-data-card-head">
        <span className={`port-data-kind ${direction}`}>{direction}</span>
        <span className="port-data-name">{name}</span>
        {hasValue && <span className="port-data-badge data">data</span>}
        {pinned && <span className="port-data-badge">pinned</span>}
      </header>
      <div className="port-data-wire">{edgeLabel(connectedEdge, direction)}</div>
      {hasValue ? (
        <pre className="port-data-json">{pretty(value)}</pre>
      ) : (
        <p className="port-data-empty muted">
          {connectedEdge
            ? "No data on this port yet."
            : direction === "input"
              ? "This input has no wire."
              : "This output has no downstream wire."}
        </p>
      )}
    </article>
  );
}

function clampHeight(value: number): number {
  return Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, value));
}

export function PortDataViewer() {
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [collapsed, setCollapsed] = useState(false);
  const dragStart = useRef<{ y: number; height: number } | null>(null);
  const selectedId = useEditor((s) => s.selectedId);
  const node = useEditor((s) => s.nodes.find((n) => n.id === selectedId));
  const edges = useEditor((s) => s.edges);
  const runOutputs = useEditor((s) => s.runOutputs);
  const runStatus = useEditor((s) => (selectedId ? s.runStatus[selectedId] : null));
  const pinned = useEditor((s) => (selectedId ? s.pinned[selectedId] : undefined));

  useEffect(() => {
    function onMove(event: MouseEvent) {
      if (!dragStart.current) return;
      const delta = dragStart.current.y - event.clientY;
      setHeight(clampHeight(dragStart.current.height + delta));
    }

    function onUp() {
      if (!dragStart.current) return;
      dragStart.current = null;
      document.body.classList.remove("is-resizing-vertical");
    }

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.classList.remove("is-resizing-vertical");
    };
  }, []);

  function startResize(event: ReactMouseEvent<HTMLDivElement>) {
    if (collapsed) return;
    dragStart.current = { y: event.clientY, height };
    document.body.classList.add("is-resizing-vertical");
    event.preventDefault();
  }

  const inputPorts = node?.data.manifest.inputs.map((p) => p.name) ?? [];
  const outputs = node && pinned !== undefined ? pinned : node ? runOutputs[node.id] : undefined;
  const selectedOutputNames = node ? outputNames(node) : [];
  const dataPortCount = selectedOutputNames.filter(
    (port) => valueAtPort(outputs, port).hasValue,
  ).length;

  return (
    <aside
      className={`port-data-viewer${collapsed ? " is-collapsed" : ""}`}
      style={
        {
          "--port-data-height": `${collapsed ? COLLAPSED_HEIGHT : height}px`,
        } as CSSProperties
      }
    >
      <div
        className="port-data-resize"
        role="separator"
        aria-orientation="horizontal"
        title="Drag to resize"
        onMouseDown={startResize}
      />
      <header className="port-data-head">
        <div>
          <h2>Port Data</h2>
          <span>
            {node ? `${node.data.manifest.name} · ${node.id}` : "Select a node to inspect live port data"}
          </span>
        </div>
        <div className="port-data-head-right">
          {node && (
            <div className="port-data-stats" aria-label="Selected node port summary">
              <span>{inputPorts.length} in</span>
              <span>{selectedOutputNames.length} out</span>
              <span>{dataPortCount} with data</span>
            </div>
          )}
          {runStatus && (
            <span className={`run-pill status-run-${runStatus}`}>
              {runStatus === "running" && <span className="node-spinner" />}
              {runStatus}
            </span>
          )}
          <button
            className="port-data-collapse"
            type="button"
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? "Expand" : "Collapse"}
          </button>
        </div>
      </header>

      {!node ? (
        <div className="port-data-empty-state">
          <strong>No node selected</strong>
          <span>Click a node to inspect every input and exit port in this panel.</span>
        </div>
      ) : (
        <div className="port-data-columns">
        <section className="port-data-column">
          <div className="port-data-column-head">
            <span>Input ports</span>
            <span>{inputPorts.length}</span>
          </div>
          {inputPorts.length === 0 ? (
            <p className="port-data-empty muted">This node has no input ports.</p>
          ) : (
            inputPorts.map((port) => {
              const incoming = edges.find(
                (edge) =>
                  edge.target === node.id && (edge.targetHandle ?? "input") === port,
              );
              const upstream = incoming ? runOutputs[incoming.source] : undefined;
              const sourcePort = incoming?.sourceHandle ?? "main";
              const { value, hasValue } = valueAtPort(upstream, sourcePort);
              return (
                <PortCard
                  key={port}
                  name={port}
                  direction="input"
                  connectedEdge={incoming}
                  value={value}
                  hasValue={hasValue}
                />
              );
            })
          )}
        </section>

        <section className="port-data-column">
          <div className="port-data-column-head">
            <span>Exit ports</span>
            <span>{selectedOutputNames.length}</span>
          </div>
          {selectedOutputNames.map((port) => {
            const outgoing = edges.find(
              (edge) =>
                edge.source === node.id && (edge.sourceHandle ?? "main") === port,
            );
            const { value, hasValue } = valueAtPort(outputs, port);
            return (
              <PortCard
                key={port}
                name={port}
                direction="output"
                connectedEdge={outgoing}
                value={value}
                hasValue={hasValue}
                pinned={pinned !== undefined}
              />
            );
          })}
        </section>
      </div>
      )}
    </aside>
  );
}
