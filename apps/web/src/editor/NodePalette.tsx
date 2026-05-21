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
        m.category.toLowerCase().includes(q),
    );
    const byCategory = new Map<string, NodeManifest[]>();
    for (const m of matched) {
      const list = byCategory.get(m.category) ?? [];
      list.push(m);
      byCategory.set(m.category, list);
    }
    const order = [...byCategory.keys()].sort(
      (a, b) => CATEGORY_ORDER.indexOf(a) - CATEGORY_ORDER.indexOf(b),
    );
    return order.map((category) => ({
      category,
      nodes: byCategory.get(category)!.sort((a, b) => a.name.localeCompare(b.name)),
    }));
  }, [manifests, query]);

  return (
    <aside className="palette">
      <div className="panel-head">
        <h2>Nodes</h2>
        <span className="panel-count">{manifests.length}</span>
      </div>
      <input
        className="palette-search"
        placeholder="Search nodes…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="palette-scroll">
        {groups.map((group) => (
          <div className="palette-group" key={group.category}>
            <div className="palette-group-head">
              <span
                className="cat-dot"
                style={{ background: categoryColor(group.category) }}
              />
              {group.category}
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
