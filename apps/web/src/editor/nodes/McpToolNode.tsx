import { Plug } from "@phosphor-icons/react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { memo, useMemo } from "react";

import { portColor } from "../NodeCard";
import type { NodyraNodeData } from "../store";

/**
 * Custom node component for `mcp_tool` nodes.
 *
 * Renders with a plug icon and shows the connection name as a subtitle
 * below the tool name. This distinguishes MCP tool nodes from regular
 * nodes on the canvas and makes the connection source visible at a glance.
 */
function McpToolNodeComponent({ data, selected }: NodeProps) {
  const nodeData = data as unknown as NodyraNodeData;
  const manifest = nodeData.manifest;
  const params = nodeData.params;
  const disabled = nodeData.disabled;
  const color = "#ff8c42"; // MCP category color

  const connectionName = String(params.connection_name ?? params.connection_id ?? "MCP");
  const toolName = String(params.tool_name ?? manifest.name);

  const tileClass = useMemo(() => {
    const classes = ["node-tile"];
    if (selected) classes.push("selected");
    if (disabled) classes.push("is-disabled");
    return classes;
  }, [selected, disabled]);

  // Build input/output handle info from manifest
  const inputs = manifest.inputs ?? [];
  const outputs = manifest.outputs ?? [];

  return (
    <div className="node" style={{ "--cat": color } as React.CSSProperties}>
      <div className={tileClass.join(" ")}>
        <Plug size={26} weight="fill" style={{ color }} />

        {disabled && <span className="node-disabled-pip">○</span>}

        {/* Input handles */}
        {inputs.map((port, i) => (
          <Handle
            key={`in-${port.name}`}
            type="target"
            position={Position.Left}
            id={port.name}
            title={`${port.name}: ${port.data_kind ?? "any"}`}
            style={{
              top: `${((i + 1) / (inputs.length + 1)) * 100}%`,
              background: portColor(port.data_kind),
            }}
          />
        ))}

        {/* Output handles */}
        {outputs.map((port, i) => (
          <Handle
            key={`out-${port.name}`}
            type="source"
            position={Position.Right}
            id={port.name}
            title={`${port.name}: ${port.data_kind ?? "any"}`}
            style={{
              top: `${((i + 1) / (outputs.length + 1)) * 100}%`,
              background: portColor(port.data_kind),
            }}
          />
        ))}
      </div>

      <div className="node-label">
        {toolName}
      </div>

      {/* Subtitle showing the connection name */}
      <div className="node-subtitle" title={`Connection: ${connectionName}`}>
        <Plug size={10} weight="fill" />
        {connectionName}
      </div>
    </div>
  );
}

export const McpToolNode = memo(
  McpToolNodeComponent,
  (previous, next) =>
    previous.id === next.id &&
    previous.data === next.data &&
    previous.selected === next.selected,
);
