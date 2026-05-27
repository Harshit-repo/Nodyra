import { useEffect, useMemo, useRef, useState } from "react";

import { CATEGORY_ORDER, categoryColor } from "../categories";
import { NodeIcon } from "../NodeIcon";
import type { NodeManifest } from "../types";
import { useEditor } from "./store";

const FAVORITES_KEY = "noodle_palette_favorites";
const RECENTS_KEY = "noodle_palette_recent";
const MAX_RECENTS = 8;

function readStoredList(key: string): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) ?? "[]") as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

function recommendedIdsFor(manifest: NodeManifest | null): string[] {
  if (!manifest) return [];
  if (manifest.category === "Triggers") {
    return ["http_request", "code", "filter", "switch", "slack_send_message"];
  }
  if (manifest.id === "http_request") {
    return ["code", "filter", "limit", "google_sheets_append", "slack_send_message"];
  }
  if (manifest.id === "code") {
    return ["filter", "switch", "google_sheets_append", "notion_create_page"];
  }
  if (manifest.id.includes("stripe")) {
    return ["code", "slack_send_message", "google_sheets_append"];
  }
  if (manifest.outputs.length > 1) {
    return ["merge", "code", "slack_send_message"];
  }
  return ["code", "http_request", "slack_send_message"];
}

function PaletteItem({
  node,
  favorite,
  onToggleFavorite,
  onUsed,
}: {
  node: NodeManifest;
  favorite: boolean;
  onToggleFavorite: (id: string) => void;
  onUsed: (id: string) => void;
}) {
  const color = categoryColor(node.category);
  return (
    <div
      key={node.id}
      className="palette-item"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/noodle", node.id);
        e.dataTransfer.effectAllowed = "move";
        onUsed(node.id);
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
      <button
        type="button"
        className={`palette-favorite${favorite ? " active" : ""}`}
        aria-label={favorite ? `Unfavorite ${node.name}` : `Favorite ${node.name}`}
        title={favorite ? "Remove from favorites" : "Add to favorites"}
        onClick={(e) => {
          e.stopPropagation();
          onToggleFavorite(node.id);
        }}
      >
        {favorite ? "★" : "☆"}
      </button>
    </div>
  );
}

export function NodePalette() {
  const manifests = useEditor((s) => s.manifests);
  const nodes = useEditor((s) => s.nodes);
  const selectedId = useEditor((s) => s.selectedId);
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [favorites, setFavorites] = useState<string[]>(() =>
    readStoredList(FAVORITES_KEY),
  );
  const [recent, setRecent] = useState<string[]>(() => readStoredList(RECENTS_KEY));
  const searchRef = useRef<HTMLInputElement | null>(null);

  const manifestsById = useMemo(
    () => new Map(manifests.map((manifest) => [manifest.id, manifest])),
    [manifests],
  );

  const selectedManifest =
    nodes.find((node) => node.id === selectedId)?.data.manifest ?? null;

  useEffect(() => {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(favorites));
  }, [favorites]);

  useEffect(() => {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(recent));
  }, [recent]);

  useEffect(() => {
    function focusSearch(): void {
      searchRef.current?.focus();
    }
    window.addEventListener("noodle:focus-node-search", focusSearch);
    return () => window.removeEventListener("noodle:focus-node-search", focusSearch);
  }, []);

  const categories = useMemo(() => {
    const names = Array.from(new Set(manifests.map((manifest) => manifest.category)));
    return names.sort((a, b) => {
      const ai = CATEGORY_ORDER.indexOf(a);
      const bi = CATEGORY_ORDER.indexOf(b);
      const ar = ai === -1 ? Number.MAX_SAFE_INTEGER : ai;
      const br = bi === -1 ? Number.MAX_SAFE_INTEGER : bi;
      return ar === br ? a.localeCompare(b) : ar - br;
    });
  }, [manifests]);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = manifests.filter(
      (m) =>
        (categoryFilter === "all" || m.category === categoryFilter) &&
        (!q ||
          m.name.toLowerCase().includes(q) ||
          m.category.toLowerCase().includes(q) ||
          m.id.toLowerCase().includes(q) ||
          (m.description ?? "").toLowerCase().includes(q)),
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
  }, [categoryFilter, manifests, query]);
  const matchedCount = groups.reduce((sum, group) => sum + group.nodes.length, 0);

  const favoriteNodes = favorites
    .map((id) => manifestsById.get(id))
    .filter((node): node is NodeManifest => Boolean(node));
  const recentNodes = recent
    .map((item) => manifestsById.get(item))
    .filter((node): node is NodeManifest => Boolean(node));
  const recommendedNodes = recommendedIdsFor(selectedManifest)
    .map((item) => manifestsById.get(item))
    .filter((node): node is NodeManifest => Boolean(node))
    .filter((node, index, rows) => rows.findIndex((item) => item.id === node.id) === index);
  const showQuickSections = !query.trim() && categoryFilter === "all";

  function toggleFavorite(id: string): void {
    setFavorites((items) =>
      items.includes(id) ? items.filter((item) => item !== id) : [id, ...items],
    );
  }

  function recordRecent(id: string): void {
    setRecent((items) => [id, ...items.filter((item) => item !== id)].slice(0, MAX_RECENTS));
  }

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
          ref={searchRef}
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
      <div className="palette-chips" aria-label="Node categories">
        <button
          type="button"
          className={categoryFilter === "all" ? "active" : ""}
          onClick={() => setCategoryFilter("all")}
        >
          All
        </button>
        {categories.map((category) => (
          <button
            type="button"
            key={category}
            className={categoryFilter === category ? "active" : ""}
            onClick={() => setCategoryFilter(category)}
          >
            <span
              className="cat-dot"
              style={{ background: categoryColor(category) }}
            />
            {category}
          </button>
        ))}
      </div>
      <div className="palette-scroll">
        {showQuickSections &&
          [
            { title: "Recommended next", nodes: recommendedNodes },
            { title: "Recent", nodes: recentNodes },
            { title: "Favorites", nodes: favoriteNodes },
          ]
            .filter((section) => section.nodes.length > 0)
            .map((section) => (
              <div className="palette-group palette-quick" key={section.title}>
                <div className="palette-group-head">
                  <span>{section.title}</span>
                  <small>{section.nodes.length}</small>
                </div>
                {section.nodes.map((node) => (
                  <PaletteItem
                    key={`${section.title}-${node.id}`}
                    node={node}
                    favorite={favorites.includes(node.id)}
                    onToggleFavorite={toggleFavorite}
                    onUsed={recordRecent}
                  />
                ))}
              </div>
            ))}
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
            {group.nodes.map((node) => (
              <PaletteItem
                key={node.id}
                node={node}
                favorite={favorites.includes(node.id)}
                onToggleFavorite={toggleFavorite}
                onUsed={recordRecent}
              />
            ))}
          </div>
        ))}
        {groups.length === 0 && (
          <p className="palette-empty">No nodes match “{query}”.</p>
        )}
      </div>
    </aside>
  );
}
