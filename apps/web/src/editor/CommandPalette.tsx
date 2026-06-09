import { useEffect, useRef, useState } from "react";

import { useEditor } from "./store";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

interface PaletteItem {
  id: string;
  label: string;
  description?: string;
  action: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const manifests = useEditor((s) => s.manifests);
  const addNode = useEditor((s) => s.addNode);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    // Restore focus to whatever was focused before the palette opened so
    // keyboard users aren't dumped back at the top of the document on close.
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const timer = window.setTimeout(() => inputRef.current?.focus(), 30);
    return () => {
      window.clearTimeout(timer);
      previouslyFocused?.focus?.();
    };
  }, [open]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const center = { x: window.innerWidth / 2, y: window.innerHeight / 2 };

  const fixedCommands: PaletteItem[] = [
    {
      id: "__run",
      label: "Run workflow",
      description: "Trigger a workflow run",
      action: () => {
        window.dispatchEvent(new Event("noodle:run-workflow"));
        onClose();
      },
    },
    {
      id: "__fit",
      label: "Fit view",
      description: "Fit all nodes in viewport",
      action: () => {
        window.dispatchEvent(new Event("noodle:fit-view"));
        onClose();
      },
    },
    {
      id: "__layout",
      label: "Auto layout",
      description: "Arrange nodes automatically",
      action: () => {
        window.dispatchEvent(new Event("noodle:auto-layout"));
        onClose();
      },
    },
    {
      id: "__shortcuts",
      label: "Open shortcuts",
      description: "View keyboard shortcuts",
      action: () => {
        window.dispatchEvent(new Event("noodle:open-shortcuts"));
        onClose();
      },
    },
    {
      id: "__sticky",
      label: "Add sticky note",
      description: "Place a sticky note on the canvas",
      action: () => {
        useEditor.getState().addStickyNote(center);
        onClose();
      },
    },
  ];

  const nodeItems: PaletteItem[] = manifests
    .filter((m) => !m.hidden)
    .map((m) => ({
      id: m.id,
      label: m.name,
      description: m.category,
      action: () => {
        addNode(m.id, center);
        onClose();
      },
    }));

  const allItems = [...fixedCommands, ...nodeItems];
  const q = query.trim().toLowerCase();
  const filtered = q
    ? allItems.filter(
        (item) =>
          item.label.toLowerCase().includes(q) ||
          item.description?.toLowerCase().includes(q),
      )
    : allItems;

  return (
    <div
      className="cmd-palette-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="cmd-palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <input
          ref={inputRef}
          className="cmd-palette-input"
          placeholder="Search commands and nodes…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          spellCheck={false}
          autoComplete="off"
        />
        <ul className="cmd-palette-list">
          {filtered.length === 0 && (
            <li className="cmd-palette-empty">No results</li>
          )}
          {filtered.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="cmd-palette-item"
                onClick={item.action}
              >
                <span className="cmd-palette-item-label">{item.label}</span>
                {item.description && (
                  <span className="cmd-palette-item-desc">
                    {item.description}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
