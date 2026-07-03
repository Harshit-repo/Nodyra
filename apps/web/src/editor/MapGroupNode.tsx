import { Handle, NodeResizer, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { memo } from "react";

import type { NodyraNodeData } from "./store";
import { useEditor } from "./store";

function MapGroupNodeComponent({ data, id, selected }: NodeProps) {
  const params = (data as NodyraNodeData).params ?? {};
  const openNdv = useEditor((s) => s.openNdv);
  const childWorkflow = useEditor((s) => s.childWorkflows[id]);

  const concurrency = (params.concurrency as number) ?? 5;
  const onError = (params.on_error as string) ?? "fail";
  const preserveOrder = params.preserve_order !== false;
  const loading = childWorkflow?.loading ?? false;
  const error = childWorkflow?.error ?? null;
  const bodyNodeCount =
    childWorkflow?.nodes.filter(
      (n) => n.data?.manifest?.id !== "manual_trigger",
    ).length ?? 0;

  return (
    <>
      <NodeResizer
        minWidth={320}
        minHeight={240}
        isVisible={Boolean(selected)}
        lineStyle={{ stroke: "rgba(245,158,11,0.4)" }}
        handleStyle={{ background: "#f59e0b", border: "none", width: 8, height: 8 }}
      />

      <Handle
        type="target"
        position={Position.Left}
        id="input"
        className="map-group-handle map-group-input-handle"
        style={{ top: 17, zIndex: 10 }}
      />

      <div
        className={`map-group-node${selected ? " is-selected" : ""}`}
        style={{ pointerEvents: "none", width: "100%", height: "100%", boxSizing: "border-box" }}
      >
        <div
          className="map-group-header"
          style={{ pointerEvents: "all" }}
          onClick={() => openNdv(id)}
          title="Click to configure map settings"
        >
          <span className="map-group-title">▶▶ MAP</span>
          <span className="map-group-chips">
            <span className="map-group-chip">×{concurrency}</span>
            <span className="map-group-chip">{onError}</span>
            {!preserveOrder && <span className="map-group-chip">unordered</span>}
          </span>
          {loading && <span className="node-spinner" style={{ marginLeft: 8 }} />}
        </div>

        <div className="map-group-body">
          {!loading && bodyNodeCount === 0 && !error && (
            <p className="map-group-hint">Drop nodes here to build the per-item body.</p>
          )}
          {error && <p className="map-group-error">{error}</p>}
        </div>

        <div className="map-group-footer" style={{ pointerEvents: "all" }}>
          <span className="map-group-title">◀◀ COLLECT</span>
          <span className="map-group-output-labels">
            <span>main</span>
            <span>errors</span>
          </span>
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        id="main"
        className="map-group-handle map-group-output-handle"
        style={{ bottom: 28, top: "auto", zIndex: 10 }}
      />
      <Handle
        type="source"
        position={Position.Right}
        id="errors"
        className="map-group-handle map-group-output-handle map-group-errors-handle"
        style={{ bottom: 8, top: "auto", zIndex: 10 }}
      />
    </>
  );
}

export const MapGroupNode = memo(
  MapGroupNodeComponent,
  (previous, next) =>
    previous.id === next.id &&
    previous.data === next.data &&
    previous.selected === next.selected,
);
