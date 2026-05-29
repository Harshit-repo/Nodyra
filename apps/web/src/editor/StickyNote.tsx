import { NodeResizer } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { useState } from "react";

import { useEditor } from "./store";

interface StickyNoteData {
  content: string;
  color: string;
}

const COLOR_MAP: Record<string, string> = {
  yellow: "#fef08a",
  pink: "#fda4af",
  blue: "#93c5fd",
  green: "#86efac",
  purple: "#d8b4fe",
};

export function StickyNote({ data, id }: NodeProps) {
  const stickyData = data as unknown as StickyNoteData;
  const [content, setContent] = useState(stickyData.content ?? "");
  const bg = COLOR_MAP[stickyData.color ?? "yellow"] ?? COLOR_MAP["yellow"]!;
  const nodes = useEditor((s) => s.nodes);
  const onNodesChange = useEditor((s) => s.onNodesChange);

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const val = e.target.value;
    setContent(val);
    // Persist back to node data so toGraph() captures it
    const changes = nodes
      .filter((n) => n.id === id)
      .map((n) => ({
        type: "replace" as const,
        id: n.id,
        item: { ...n, data: { ...n.data, content: val } },
      }));
    if (changes.length > 0) onNodesChange(changes);
  }

  return (
    <>
      <NodeResizer minWidth={120} minHeight={80} />
      <div
        className="sticky-note"
        style={{ background: bg, width: "100%", height: "100%" }}
      >
        <textarea
          className="sticky-note-textarea nodrag"
          value={content}
          onChange={handleChange}
          placeholder="Type a note…"
        />
      </div>
    </>
  );
}
