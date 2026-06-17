import { NodeResizer } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { useRef, useState } from "react";

import { useEditor } from "./store";

interface StickyNoteData {
  content: string;
  color: string;
  opacity?: number;
  fontSize?: number;
}

const COLORS: { id: string; hex: string; label: string }[] = [
  { id: "yellow", hex: "#fef08a", label: "Yellow" },
  { id: "pink",   hex: "#fda4af", label: "Pink" },
  { id: "blue",   hex: "#93c5fd", label: "Blue" },
  { id: "green",  hex: "#86efac", label: "Green" },
  { id: "purple", hex: "#d8b4fe", label: "Purple" },
  { id: "orange", hex: "#fdba74", label: "Orange" },
  { id: "white",  hex: "#f1f5f9", label: "White" },
];

const COLOR_MAP = Object.fromEntries(COLORS.map((c) => [c.id, c.hex]));
const FONT_SIZES = [11, 13, 15, 18, 22, 28];

export function StickyNote({ data, id }: NodeProps) {
  const stickyData = data as unknown as StickyNoteData;
  const [content, setContent] = useState(stickyData.content ?? "");
  const [hovered, setHovered] = useState(false);
  const lingerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onNodesChange = useEditor((s) => s.onNodesChange);
  const nodes = useEditor((s) => s.nodes);

  const colorId = stickyData.color ?? "yellow";
  const bg = COLOR_MAP[colorId] ?? COLOR_MAP["yellow"]!;
  const opacity = stickyData.opacity ?? 1;
  const fontSize = stickyData.fontSize ?? 13;

  function patch(updates: Partial<StickyNoteData>) {
    const changes = nodes
      .filter((n) => n.id === id)
      .map((n) => ({
        type: "replace" as const,
        id: n.id,
        item: { ...n, data: { ...n.data, ...updates } },
      }));
    if (changes.length > 0) onNodesChange(changes);
  }

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const val = e.target.value;
    setContent(val);
    patch({ content: val });
  }

  function onEnter() {
    if (lingerRef.current) clearTimeout(lingerRef.current);
    setHovered(true);
  }

  function onLeave() {
    lingerRef.current = setTimeout(() => setHovered(false), 500);
  }

  return (
    <div
      className={`sticky-note-wrapper${hovered ? " is-hovered" : ""}`}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
    >
      <NodeResizer minWidth={120} minHeight={80} />

      {/* Hover toolbar — three collapsible sections */}
      <div className="sticky-toolbar nodrag nopan">

        {/* Color section */}
        <div className="sticky-section">
          <span className="sticky-section-trigger" title="Color">
            <span className="sticky-section-dot" style={{ background: bg }} />
          </span>
          <div className="sticky-section-content">
            {COLORS.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`sticky-color-swatch${colorId === c.id ? " is-active" : ""}`}
                style={{ background: c.hex }}
                title={c.label}
                onClick={() => patch({ color: c.id })}
              />
            ))}
          </div>
        </div>

        <div className="sticky-toolbar-sep" />

        {/* Opacity section */}
        <div className="sticky-section">
          <span className="sticky-section-trigger sticky-section-label" title="Opacity">α</span>
          <div className="sticky-section-content">
            <input
              type="range"
              className="sticky-slider"
              min={0.2}
              max={1}
              step={0.05}
              value={opacity}
              onChange={(e) => patch({ opacity: parseFloat(e.target.value) })}
            />
            <span className="sticky-section-value">{Math.round(opacity * 100)}%</span>
          </div>
        </div>

        <div className="sticky-toolbar-sep" />

        {/* Font size section */}
        <div className="sticky-section">
          <span className="sticky-section-trigger sticky-section-label" title="Text size">T</span>
          <div className="sticky-section-content">
            {FONT_SIZES.map((sz) => (
              <button
                key={sz}
                type="button"
                className={`sticky-font-btn${fontSize === sz ? " is-active" : ""}`}
                onClick={() => patch({ fontSize: sz })}
                title={`${sz}px`}
              >
                {sz}
              </button>
            ))}
          </div>
        </div>

      </div>

      <div
        className="sticky-note"
        style={{ background: bg, opacity, width: "100%", height: "100%" }}
      >
        <textarea
          className="sticky-note-textarea nodrag"
          value={content}
          onChange={handleChange}
          placeholder="Type a note…"
          style={{ fontSize }}
        />
      </div>
    </div>
  );
}
