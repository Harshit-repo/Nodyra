import { CaretDown, CaretLeft, CaretRight, MagnifyingGlass, Star, X } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";

import { CATEGORY_ORDER, categoryColor } from "../categories";
import { isBrandIconName, NodeIcon } from "../NodeIcon";
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

/** Rank a manifest against the search query.
 *
 * Lower is better. Order: exact id > exact name > starts-with id/name >
 * starts-with category > substring in name > substring in id > substring in
 * description. The ranks are far enough apart that a single tier collapses
 * even when multiple manifests share that match level — within a tier we fall
 * back to alphabetical name order at the call site.
 */
function searchAliases(node: NodeManifest): string {
  const kinds = [
    ...node.inputs.map((port) => port.data_kind),
    ...node.outputs.map((port) => port.data_kind),
  ];
  const aliases: string[] = [];
  if (kinds.includes("dataset")) aliases.push("dataset datasetref parquet table duckdb sql big data");
  if (node.id === "records_to_dataset") aliases.push("convert rows records list to datasetref");
  if (node.id === "dataset_to_records") aliases.push("materialize datasetref rows records list");
  if (node.id === "duckdb_sql") aliases.push("query datasetref sql transform table");
  return aliases.join(" ");
}

function rankMatch(node: NodeManifest, q: string): number {
  if (!q) return 1000;
  const id = node.id.toLowerCase();
  const name = node.name.toLowerCase();
  const cat = node.category.toLowerCase();
  const desc = (node.description ?? "").toLowerCase();
  const aliases = searchAliases(node).toLowerCase();
  if (id === q) return 0;
  if (name === q) return 1;
  if (id.startsWith(q)) return 10;
  if (name.startsWith(q)) return 11;
  if (cat.startsWith(q)) return 20;
  if (name.includes(q)) return 30;
  if (id.includes(q)) return 31;
  if (cat.includes(q)) return 40;
  if (desc.includes(q)) return 50;
  if (aliases.includes(q)) return 55;
  return 1000;
}

const INTEGRATION_GROUP_LABELS: Array<[string, string]> = [
  ["google_sheets_", "Google Sheets"],
  ["microsoft_outlook_", "Microsoft Outlook"],
  ["outlook_", "Microsoft Outlook"],
  ["airtable_", "Airtable"],
  ["github_", "GitHub"],
  ["notion_", "Notion"],
  ["slack_", "Slack"],
  ["stripe_", "Stripe"],
];

function integrationLabelFor(nodeId: string): string | null {
  for (const [prefix, label] of INTEGRATION_GROUP_LABELS) {
    if (nodeId.startsWith(prefix)) return label;
  }
  return null;
}

/** Group official integration nodes by provider inside the Integrations section. */
function integrationOf(node: NodeManifest): string | null {
  if (node.category !== "Integrations") return null;
  return integrationLabelFor(node.id);
}

function recommendedIdsFor(manifest: NodeManifest | null): string[] {
  if (!manifest) return [];
  if (manifest.category === "Triggers") {
    return ["http_request", "code", "filter", "switch", "slack_send_message_v2"];
  }
  if (manifest.id === "http_request") {
    return ["records_to_dataset", "code", "filter", "limit", "google_sheets_append_v2", "slack_send_message_v2"];
  }
  if (manifest.id === "code") {
    return ["records_to_dataset", "filter", "switch", "google_sheets_append_v2", "notion_create_page_v2"];
  }
  if (manifest.outputs.some((port) => port.data_kind === "dataset")) {
    return ["dataset_preview", "duckdb_sql", "dataset_filter", "dataset_to_records", "csv_write"];
  }
  if (manifest.id.includes("stripe")) {
    return ["code", "slack_send_message_v2", "google_sheets_append_v2"];
  }
  if (manifest.outputs.length > 1) {
    return ["merge", "code", "slack_send_message_v2"];
  }
  return ["code", "http_request", "slack_send_message_v2"];
}

function nodeBadges(node: NodeManifest): string[] {
  const badges: string[] = [];
  if (node.deprecated) badges.push("Deprecated");
  if (node.category === "Triggers") badges.push("Trigger");
  else badges.push("Action");
  if (node.params.some((param) => param.type === "credential")) badges.push("Auth");
  if (["code", "execute_command", "ssh_execute"].includes(node.id)) badges.push("Unsafe");
  const hasDatasetInput = node.inputs.some((port) => port.data_kind === "dataset");
  const hasDatasetOutput = node.outputs.some((port) => port.data_kind === "dataset");
  if (hasDatasetInput || hasDatasetOutput) badges.push(hasDatasetInput && hasDatasetOutput ? "DatasetRef" : hasDatasetOutput ? "Makes DatasetRef" : "Needs DatasetRef");
  if (node.outputs.length > 1) badges.push(`${node.outputs.length} outputs`);
  return badges;
}

function PaletteItem({
  node,
  favorite,
  active,
  onToggleFavorite,
  onUsed,
}: {
  node: NodeManifest;
  favorite: boolean;
  active?: boolean;
  onToggleFavorite: (id: string) => void;
  onUsed: (id: string) => void;
}) {
  const color = categoryColor(node.category);
  const badges = nodeBadges(node);
  const hasBrandIcon = isBrandIconName(node.icon);
  return (
    <div
      key={node.id}
      className={`palette-item${active ? " active" : ""}`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/noodle", node.id);
        e.dataTransfer.effectAllowed = "move";
        onUsed(node.id);
      }}
      title={node.description}
    >
      <span
        className={`palette-item-glyph${hasBrandIcon ? " has-brand-icon" : ""}`}
        style={
          hasBrandIcon
            ? { color }
            : { color, background: `${color}1f` }
        }
      >
        <NodeIcon name={node.icon} size={hasBrandIcon ? 20 : 14} />
      </span>
      <span className="palette-item-body">
        <span className="palette-item-name">{node.name}</span>
        <span className="palette-item-badges" aria-label={`${node.name} badges`}>
          {badges.map((badge) => (
            <span
              key={badge}
              className={`palette-badge palette-badge-${badge.toLowerCase().replaceAll(" ", "-")}`}
            >
              {badge}
            </span>
          ))}
        </span>
      </span>
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
        <Star size={13} weight={favorite ? "fill" : "regular"} />
      </button>
    </div>
  );
}

const COLLAPSED_KEY = "noodle_palette_collapsed";
const EXPANDED_GROUPS_KEY = "noodle_palette_expanded_groups";
const COLLAPSED_QUICK_KEY = "noodle_palette_collapsed_quick";
const DEFAULT_EXPANDED_GROUPS: string[] = [];

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
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSED_KEY) === "1"; } catch { return false; }
  });
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(EXPANDED_GROUPS_KEY) ?? "[]") as unknown;
      const values = Array.isArray(stored) ? stored.filter((x): x is string => typeof x === "string") : [];
      return new Set([...DEFAULT_EXPANDED_GROUPS, ...values]);
    } catch { return new Set(DEFAULT_EXPANDED_GROUPS); }
  });
  const [collapsedQuick, setCollapsedQuick] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(COLLAPSED_QUICK_KEY) ?? "[]") as unknown;
      return new Set(Array.isArray(stored) ? stored.filter((x): x is string => typeof x === "string") : []);
    } catch { return new Set(); }
  });
  const searchRef = useRef<HTMLInputElement | null>(null);
  const chipsRef = useRef<HTMLDivElement | null>(null);

  function toggleCollapsed(): void {
    setCollapsed((v) => {
      const next = !v;
      try { localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0"); } catch { /* */ }
      return next;
    });
  }

  function toggleGroup(category: string): void {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      try { localStorage.setItem(EXPANDED_GROUPS_KEY, JSON.stringify([...next])); } catch { /* */ }
      return next;
    });
  }

  function toggleQuick(title: string): void {
    setCollapsedQuick((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      try { localStorage.setItem(COLLAPSED_QUICK_KEY, JSON.stringify([...next])); } catch { /* */ }
      return next;
    });
  }

  const visibleManifests = useMemo(
    () => manifests.filter((manifest) => !manifest.hidden),
    [manifests],
  );

  const manifestsById = useMemo(
    () => new Map(visibleManifests.map((manifest) => [manifest.id, manifest])),
    [visibleManifests],
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

  useEffect(() => {
    const el = chipsRef.current;
    if (!el) return;
    function onWheel(e: WheelEvent): void {
      if (e.deltaX !== 0) return; // already horizontal (trackpad)
      el!.scrollLeft += e.deltaY;
      e.preventDefault();
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const categories = useMemo(() => {
    const names = Array.from(
      new Set(visibleManifests.map((manifest) => manifest.category)),
    );
    return names.sort((a, b) => {
      const ai = CATEGORY_ORDER.indexOf(a);
      const bi = CATEGORY_ORDER.indexOf(b);
      const ar = ai === -1 ? Number.MAX_SAFE_INTEGER : ai;
      const br = bi === -1 ? Number.MAX_SAFE_INTEGER : bi;
      return ar === br ? a.localeCompare(b) : ar - br;
    });
  }, [visibleManifests]);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = visibleManifests
      .filter(
        (m) =>
          (categoryFilter === "all" || m.category === categoryFilter) &&
          (!q || rankMatch(m, q) < 1000),
      );
    // When searching, return a single flat "Results" group sorted by rank so
    // exact matches surface above substring hits. When browsing, keep the
    // category grouping the user is used to but layer integration subgroups
    // inside each category so e.g. "Slack > send_message / list_channels"
    // is easier to scan than a flat A-Z list.
    if (q) {
      const sorted = matched.slice().sort((a, b) => {
        const r = rankMatch(a, q) - rankMatch(b, q);
        return r !== 0 ? r : a.name.localeCompare(b.name);
      });
      return [{ category: "Results", nodes: sorted }];
    }
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
  }, [categoryFilter, query, visibleManifests]);
  const matchedCount = groups.reduce((sum, group) => sum + group.nodes.length, 0);

  // Flat ordered list mirroring what's rendered — drives the command-palette
  // keyboard navigation (up/down arrow + Enter to insert at canvas center).
  const flatResults = useMemo(
    () => groups.flatMap((g) => g.nodes),
    [groups],
  );
  const [activeIdx, setActiveIdx] = useState(0);
  useEffect(() => {
    setActiveIdx(0);
  }, [query, categoryFilter]);

  const addNode = useEditor((s) => s.addNode);

  function handleSearchKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, Math.max(0, flatResults.length - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      const pick = flatResults[activeIdx];
      if (pick) {
        e.preventDefault();
        // Drop near canvas center — the editor surface centers content around
        // (400, 200) by default; jitter so repeated Enters don't stack.
        const offset = (flatResults.length > 1 ? activeIdx % 3 : 0) * 24;
        addNode(pick.id, { x: 400 + offset, y: 200 + offset });
        recordRecent(pick.id);
        setQuery("");
      }
    } else if (e.key === "Escape") {
      if (query) {
        e.preventDefault();
        setQuery("");
      }
    }
  }

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

  if (collapsed) {
    return (
      <aside className="palette palette--collapsed">
        <button
          type="button"
          className="palette-collapse-btn"
          aria-label="Expand node panel"
          title="Expand node panel"
          onClick={toggleCollapsed}
        >
          <CaretRight size={14} weight="bold" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="palette">
      <div className="panel-head">
        <h2>Nodes</h2>
        <div className="panel-head-right">
          <span className="panel-count">
            {query.trim()
              ? `${matchedCount}/${visibleManifests.length}`
              : visibleManifests.length}
          </span>
          <button
            type="button"
            className="palette-collapse-btn"
            aria-label="Collapse node panel"
            title="Collapse node panel"
            onClick={toggleCollapsed}
          >
            <CaretLeft size={14} weight="bold" />
          </button>
        </div>
      </div>
      <div className="palette-search-wrap">
        <MagnifyingGlass className="palette-search-icon" size={13} weight="bold" />
        <input
          ref={searchRef}
          className="palette-search"
          placeholder="Search nodes…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleSearchKeyDown}
        />
        {query && (
          <button
            type="button"
            className="palette-search-clear"
            aria-label="Clear node search"
            onClick={() => setQuery("")}
          >
            <X size={11} weight="bold" />
          </button>
        )}
      </div>
      <div ref={chipsRef} className="palette-chips" aria-label="Node categories">
        <button
          type="button"
          className={categoryFilter === "all" ? "active" : ""}
          onClick={() => {
            setQuery("");
            setCategoryFilter("all");
            setExpandedGroups(new Set());
            try { localStorage.setItem(EXPANDED_GROUPS_KEY, "[]"); } catch { /* */ }
          }}
        >
          All
        </button>
        {categories.map((category) => (
          <button
            type="button"
            key={category}
            className={categoryFilter === category ? "active" : ""}
            onClick={() => {
              setQuery("");
              setCategoryFilter(category);
              setExpandedGroups((prev) => {
                if (prev.has(category)) return prev;
                const next = new Set(prev);
                next.add(category);
                try { localStorage.setItem(EXPANDED_GROUPS_KEY, JSON.stringify([...next])); } catch { /* */ }
                return next;
              });
            }}
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
            .map((section) => {
              const isQuickCollapsed = collapsedQuick.has(section.title);
              return (
                <div className="palette-group palette-quick" key={section.title}>
                  <button
                    type="button"
                    className="palette-group-head palette-group-head--btn"
                    onClick={() => toggleQuick(section.title)}
                    aria-expanded={!isQuickCollapsed}
                  >
                    <span>{section.title}</span>
                    <span className="palette-group-head-right">
                      <small>{section.nodes.length}</small>
                      <CaretDown
                        size={10}
                        weight="bold"
                        className={`palette-group-caret${isQuickCollapsed ? " palette-group-caret--collapsed" : ""}`}
                      />
                    </span>
                  </button>
                  {!isQuickCollapsed && section.nodes.map((node) => (
                    <PaletteItem
                      key={`${section.title}-${node.id}`}
                      node={node}
                      favorite={favorites.includes(node.id)}
                      onToggleFavorite={toggleFavorite}
                      onUsed={recordRecent}
                    />
                  ))}
                </div>
              );
            })}
        {groups.map((group) => {
          // Subgroup integration nodes by service inside the category — only
          // when browsing (no active query) and at least 2 nodes share an
          // integration prefix. Otherwise render flat.
          const subgroups: { label: string | null; nodes: NodeManifest[] }[] = [];
          if (!query.trim()) {
            const integrationBuckets = new Map<string, NodeManifest[]>();
            const standalone: NodeManifest[] = [];
            for (const node of group.nodes) {
              const integration = integrationOf(node);
              if (integration) {
                const arr = integrationBuckets.get(integration) ?? [];
                arr.push(node);
                integrationBuckets.set(integration, arr);
              } else {
                standalone.push(node);
              }
            }
            // Promote buckets with >=2 nodes into subgroups; collapse singles
            // back into the flat list so we don't create one-item sections.
            for (const [prefix, nodes] of integrationBuckets) {
              if (nodes.length >= 2) {
                subgroups.push({ label: prefix, nodes });
              } else {
                standalone.push(...nodes);
              }
            }
            if (standalone.length > 0) {
              subgroups.unshift({ label: null, nodes: standalone });
            }
            // Stable label ordering: nulls first, then alpha.
            subgroups.sort((a, b) => {
              if (a.label === null) return -1;
              if (b.label === null) return 1;
              return a.label.localeCompare(b.label);
            });
          }
          const renderNodes = subgroups.length > 0 ? null : group.nodes;
          // When searching, always show results. When browsing, collapsed by default.
          const isGroupCollapsed = !query.trim() && !expandedGroups.has(group.category);
          return (
            <div className="palette-group" key={group.category}>
              <button
                type="button"
                className="palette-group-head palette-group-head--btn"
                onClick={() => toggleGroup(group.category)}
                aria-expanded={!isGroupCollapsed}
              >
                <span>
                  <span
                    className="cat-dot"
                    style={{ background: categoryColor(group.category) }}
                  />
                  {group.category}
                </span>
                <span className="palette-group-head-right">
                  <small>{group.nodes.length}</small>
                  <CaretDown
                    size={10}
                    weight="bold"
                    className={`palette-group-caret${isGroupCollapsed ? " palette-group-caret--collapsed" : ""}`}
                  />
                </span>
              </button>
              {!isGroupCollapsed && renderNodes &&
                renderNodes.map((node) => (
                  <PaletteItem
                    key={node.id}
                    node={node}
                    favorite={favorites.includes(node.id)}
                    active={flatResults[activeIdx]?.id === node.id}
                    onToggleFavorite={toggleFavorite}
                    onUsed={recordRecent}
                  />
                ))}
              {!isGroupCollapsed && subgroups.map((sg, i) => (
                <div className="palette-subgroup" key={`${group.category}-sg-${i}`}>
                  {sg.label && (
                    <div className="palette-subgroup-head">{sg.label}</div>
                  )}
                  {sg.nodes.map((node) => (
                    <PaletteItem
                      key={node.id}
                      node={node}
                      favorite={favorites.includes(node.id)}
                      active={flatResults[activeIdx]?.id === node.id}
                      onToggleFavorite={toggleFavorite}
                      onUsed={recordRecent}
                    />
                  ))}
                </div>
              ))}
            </div>
          );
        })}
        {groups.length === 0 && (
          <div className="palette-empty">
            <MagnifyingGlass size={22} weight="thin" />
            <span>No nodes match<br /><strong>&ldquo;{query}&rdquo;</strong></span>
          </div>
        )}
      </div>
    </aside>
  );
}
