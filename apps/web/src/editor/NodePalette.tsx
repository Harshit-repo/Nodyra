import { useMemo, useState } from "react";

import { CATEGORY_ORDER, categoryColor } from "../categories";
import { NodeIcon } from "../NodeIcon";
import type { NodeManifest } from "../types";
import { useEditor } from "./store";

export function NodePalette() {
  const manifests = useEditor((s) => s.manifests);
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = manifests.filter(
      (m) =>
        !q ||
        m.name.toLowerCase().includes(q) ||
        m.category.toLowerCase().includes(q) ||
        m.id.toLowerCase().includes(q) ||
        (m.description ?? "").toLowerCase().includes(q),
    );
    const byCategory = new Map<string, NodeManifest[]>();
    for (const m of matched) {
      const list = byCategory.get(m.category) ?? [];
      list.push(m);
      byCategory.set(m.category, list);
    }
    const order = [...byCategory.keys()].sort(
      (a, b) => {
        const ai = CATEGORY_ORDER.indexOf(a);
        const bi = CATEGORY_ORDER.indexOf(b);
        const ar = ai === -1 ? Number.MAX_SAFE_INTEGER : ai;
        const br = bi === -1 ? Number.MAX_SAFE_INTEGER : bi;
        return ar === br ? a.localeCompare(b) : ar - br;
      },
    );
    return order.map((category) => ({
      category,
      nodes: byCategory.get(category)!.sort((a, b) => a.name.localeCompare(b.name)),
    }));
  }, [manifests, query]);
  const matchedCount = groups.reduce((sum, group) => sum + group.nodes.length, 0);

  return (
    <aside className="palette">
      <div className="panel-head">
        <h2>Nodes</h2>
        <span className="panel-count">
          {query.trim() ? `${matchedCount}/${manifests.length}` : manifests.length}
        </span>
      </div>
      <div className="palette-search-wrap">
        <input
          className="palette-search"
          placeholder="Search nodes..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button
            type="button"
            className="palette-search-clear"
            aria-label="Clear node search"
            onClick={() => setQuery("")}
          >
            x
          </button>
        )}
      </div>
      <div className="palette-scroll">
        {groups.map((group) => (
          <div className="palette-group" key={group.category}>
            <div className="palette-group-head">
              <span>
                <span
                  className="cat-dot"
                  style={{ background: categoryColor(group.category) }}
                />
                {group.category}
              </span>
              <small>{group.nodes.length}</small>
            </div>
            {group.nodes.map((node) => {
              const color = categoryColor(node.category);
              return (
                <div
                  key={node.id}
                  className="palette-item"
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData("application/noodle", node.id);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  title={node.description}
                >
                  <span
                    className="palette-item-glyph"
                    style={{ color, background: `${color}1f` }}
                  >
                    <NodeIcon name={node.icon} size={14} />
                  </span>
                  <span className="palette-item-name">{node.name}</span>
                </div>
              );
            })}
          </div>
        ))}
        {groups.length === 0 && (
          <p className="palette-empty">No nodes match “{query}”.</p>
        )}
      </div>
    </aside>
  );
}
