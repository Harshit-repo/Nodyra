import { CaretDown, CaretLeft, CaretRight, MagnifyingGlass, Sparkle, Star, X } from "@phosphor-icons/react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, errorMessage } from "../api";
import { CATEGORY_ORDER, categoryColor } from "../categories";
import { isBrandIconName, NodeIcon } from "../NodeIcon";
import { safeGetItem, safeSetItem } from "../safeStorage";
import type { MCPConnection, NodeManifest } from "../types";
import { cachedMcpTools, McpToolsSection } from "./McpToolsSection";
import { isTriggerManifest, useEditor } from "./store";

const FAVORITES_KEY = "nodyra_palette_favorites";
const RECENTS_KEY = "nodyra_palette_recent";
const VIEW_KEY = "nodyra_palette_view";
const MAX_RECENTS = 8;
const MAX_CORE_NODES = 48;

type PaletteView = "core" | "all";

const CORE_CATEGORIES = new Set([
  "Triggers",
  "API",
  "Logic",
  "Data",
  "Transform",
  "Utility",
  "General",
]);

const CORE_NODE_PRIORITY = [
  "manual_trigger",
  "webhook_trigger",
  "schedule_trigger",
  "respond_to_webhook",
  "http_request",
  "code",
  "set",
  "set_fields",
  "filter",
  "switch",
  "if",
  "merge",
  "limit",
  "sort",
  "aggregate",
  "split_items",
  "wait",
  "records_to_dataset",
  "dataset_to_records",
  "duckdb_sql",
  "dataset_filter",
  "dataset_limit",
  "dataset_preview",
  "csv_read",
  "csv_write",
  "json_parse",
  "json_stringify",
];

function coreManifests(manifests: NodeManifest[]): NodeManifest[] {
  const priority = new Map(CORE_NODE_PRIORITY.map((id, index) => [id, index]));
  return manifests
    .filter(
      (manifest) =>
        CORE_CATEGORIES.has(manifest.category) &&
        !manifest.deprecated &&
        !manifest.params.some((param) => param.type === "credential"),
    )
    .sort((a, b) => {
      const aRank = priority.get(a.id) ?? Number.MAX_SAFE_INTEGER;
      const bRank = priority.get(b.id) ?? Number.MAX_SAFE_INTEGER;
      return aRank === bRank ? browseSort(a, b) : aRank - bRank;
    })
    .slice(0, MAX_CORE_NODES);
}

function readStoredList(key: string): string[] {
  try {
    const parsed = JSON.parse(safeGetItem(key) ?? "[]") as unknown;
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

type NormalizedNodeSearch = {
  id: string;
  name: string;
  category: string;
  description: string;
  aliases: string;
};

const SEARCH_INDEX_CACHE = new WeakMap<NodeManifest, NormalizedNodeSearch>();

function normalizedNodeSearch(node: NodeManifest): NormalizedNodeSearch {
  const cached = SEARCH_INDEX_CACHE.get(node);
  if (cached) return cached;
  const normalized = {
    id: node.id.toLowerCase(),
    name: node.name.toLowerCase(),
    category: node.category.toLowerCase(),
    description: (node.description ?? "").toLowerCase(),
    aliases: searchAliases(node).toLowerCase(),
  };
  SEARCH_INDEX_CACHE.set(node, normalized);
  return normalized;
}

function rankMatch(node: NodeManifest, q: string): number {
  if (!q) return 1000;
  const {
    id,
    name,
    category: cat,
    description: desc,
    aliases,
  } = normalizedNodeSearch(node);
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
  if (isTriggerManifest(manifest)) {
    return ["respond_to_webhook", "http_request", "code", "filter", "switch", "slack"];
  }
  if (manifest.id === "http_request") {
    return ["records_to_dataset", "code", "filter", "limit", "google_sheets", "slack"];
  }
  if (manifest.id === "code") {
    return ["records_to_dataset", "filter", "switch", "google_sheets", "notion_create_page_v2"];
  }
  if (manifest.outputs.some((port) => port.data_kind === "dataset")) {
    return ["dataset_preview", "duckdb_sql", "dataset_filter", "dataset_to_records", "csv_write"];
  }
  if (manifest.id.includes("stripe")) {
    return ["code", "slack", "google_sheets"];
  }
  if (manifest.outputs.length > 1) {
    return ["merge", "code", "slack"];
  }
  return ["code", "http_request", "slack"];
}

function nodeBadges(node: NodeManifest): string[] {
  const badges: string[] = [];
  if (node.deprecated) badges.push("Deprecated");
  if (isTriggerManifest(node)) badges.push("Trigger");
  else badges.push("Action");
  if (node.params.some((param) => param.type === "credential")) badges.push("Auth");
  if (["code", "execute_command", "ssh_execute"].includes(node.id)) badges.push("Unsafe");
  const hasDatasetInput = node.inputs.some((port) => port.data_kind === "dataset");
  const hasDatasetOutput = node.outputs.some((port) => port.data_kind === "dataset");
  if (hasDatasetInput || hasDatasetOutput) badges.push(hasDatasetInput && hasDatasetOutput ? "DatasetRef" : hasDatasetOutput ? "Makes DatasetRef" : "Needs DatasetRef");
  if (node.outputs.length > 1) badges.push(`${node.outputs.length} outputs`);
  return badges;
}

function isMemoryRelatedNode(node: NodeManifest): boolean {
  const text = `${node.id} ${node.name}`.toLowerCase();
  return (
    text.includes("memory") ||
    node.inputs.some((port) => port.data_kind === "ai_memory") ||
    node.outputs.some((port) => port.data_kind === "ai_memory")
  );
}

/** Read tool_cache and extract MCP tools into synthetic NodeManifest entries.
 *  tool_cache is an array of MCP tool objects: {name, description, input_schema}.
 *  Returns an empty array when no connections have tools cached. */
function buildMcpToolManifests(connections: MCPConnection[]): NodeManifest[] {
  const manifests: NodeManifest[] = [];
  for (const conn of connections) {
    if (conn.enabled === false) continue;
    for (const tool of cachedMcpTools(conn)) {
      manifests.push({
        id: tool.manifestId,
        name: tool.name,
        category: "MCP",
        version: "1.0",
        description: tool.description,
        icon: "plug",
        inputs: [{ name: "input", description: "Input data" }],
        params: [
          {
            name: "connection_id",
            type: "string",
            required: true,
            default: conn.id,
            description: "MCP connection ID",
            placeholder: "",
            choices: null,
            multiline: false,
            key_value: false,
            advanced: true,
          },
          {
            name: "tool_name",
            type: "string",
            required: true,
            default: tool.name,
            description: "MCP tool name",
            placeholder: "",
            choices: null,
            multiline: false,
            key_value: false,
            advanced: true,
          },
          {
            name: "connection_name",
            type: "string",
            required: false,
            default: conn.name,
            description: "Connection display name",
            placeholder: "",
            choices: null,
            multiline: false,
            key_value: false,
            advanced: true,
          },
        ],
        outputs: [{ name: "result", description: "Tool output" }],
      });
    }
  }
  return manifests;
}

function browseSort(a: NodeManifest, b: NodeManifest): number {
  if (a.category === "AI" && b.category === "AI") {
    const am = isMemoryRelatedNode(a);
    const bm = isMemoryRelatedNode(b);
    if (am !== bm) return am ? -1 : 1;
  }
  return a.name.localeCompare(b.name);
}

const PaletteItem = memo(function PaletteItem({
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
  const [showTip, setShowTip] = useState(false);
  const tipTimerRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (tipTimerRef.current !== null) {
        clearTimeout(tipTimerRef.current);
      }
    };
  }, []);

  const isAiGenerated = node.category === "AI Generated";

  function handleMouseEnter(): void {
    tipTimerRef.current = window.setTimeout(() => setShowTip(true), 300);
  }

  function handleMouseLeave(): void {
    if (tipTimerRef.current !== null) {
      clearTimeout(tipTimerRef.current);
      tipTimerRef.current = null;
    }
    setShowTip(false);
  }

  return (
    <div
      key={node.id}
      className={`palette-item${active ? " active" : ""}${isAiGenerated ? " palette-item--ai" : ""}`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/nodyra", node.id);
        e.dataTransfer.effectAllowed = "move";
        onUsed(node.id);
      }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {isAiGenerated && (
        <span className="palette-item-ai-badge" title="AI Generated">
          <Sparkle size={11} weight="fill" />
        </span>
      )}
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
      {showTip && (
        <div className="pal-tip" role="tooltip">
          <div className="pal-tip-name">{node.name}</div>
          {node.description && <div className="pal-tip-desc">{node.description}</div>}
          <div className="pal-tip-cat">{node.category}</div>
          <div className="pal-tip-ports">In: {node.inputs.map((p) => p.name).join(", ") || "none"} | Out: {node.outputs.map((p) => p.name).join(", ") || "none"}</div>
        </div>
      )}
    </div>
  );
});

const COLLAPSED_KEY = "nodyra_palette_collapsed";
const EXPANDED_GROUPS_KEY = "nodyra_palette_expanded_groups";
const COLLAPSED_QUICK_KEY = "nodyra_palette_collapsed_quick";
const DEFAULT_EXPANDED_GROUPS: string[] = [];
const INITIAL_RENDERED_NODES = 60;

export function NodePalette() {
  const manifests = useEditor((s) => s.manifests);
  const setManifests = useEditor((s) => s.setManifests);
  const selectedManifest = useEditor((s) =>
    s.nodes.find((node) => node.id === s.selectedId)?.data?.manifest ?? null,
  );
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [paletteView, setPaletteView] = useState<PaletteView>(() =>
    safeGetItem(VIEW_KEY) === "all" ? "all" : "core",
  );
  const [favorites, setFavorites] = useState<string[]>(() =>
    readStoredList(FAVORITES_KEY),
  );
  const [recent, setRecent] = useState<string[]>(() => readStoredList(RECENTS_KEY));
  const [collapsed, setCollapsed] = useState(() => {
    return safeGetItem(COLLAPSED_KEY) === "1";
  });
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(safeGetItem(EXPANDED_GROUPS_KEY) ?? "[]") as unknown;
      const values = Array.isArray(stored) ? stored.filter((x): x is string => typeof x === "string") : [];
      return new Set([...DEFAULT_EXPANDED_GROUPS, ...values]);
    } catch { return new Set(DEFAULT_EXPANDED_GROUPS); }
  });
  const [collapsedQuick, setCollapsedQuick] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(safeGetItem(COLLAPSED_QUICK_KEY) ?? "[]") as unknown;
      return new Set(Array.isArray(stored) ? stored.filter((x): x is string => typeof x === "string") : []);
    } catch { return new Set(); }
  });
  const [visibleCounts, setVisibleCounts] = useState<Record<string, number>>({});
  const [mcpConnections, setMcpConnections] = useState<MCPConnection[]>([]);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [mcpError, setMcpError] = useState("");
  const [mcpSyncingId, setMcpSyncingId] = useState<string | null>(null);
  const mcpMountedRef = useRef(true);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const chipsRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    mcpMountedRef.current = true;
    return () => {
      mcpMountedRef.current = false;
    };
  }, []);

  function toggleCollapsed(): void {
    setCollapsed((v) => {
      const next = !v;
      safeSetItem(COLLAPSED_KEY, next ? "1" : "0");
      return next;
    });
  }

  function toggleGroup(category: string): void {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      safeSetItem(EXPANDED_GROUPS_KEY, JSON.stringify([...next]));
      return next;
    });
  }

  function toggleQuick(title: string): void {
    setCollapsedQuick((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      safeSetItem(COLLAPSED_QUICK_KEY, JSON.stringify([...next]));
      return next;
    });
  }

  const visibleManifests = useMemo(
    () => manifests.filter((manifest) => !manifest.hidden),
    [manifests],
  );

  const curatedManifests = useMemo(
    () => coreManifests(visibleManifests),
    [visibleManifests],
  );

  const browseManifests = paletteView === "core" ? curatedManifests : visibleManifests;

  const manifestsById = useMemo(
    () => new Map(visibleManifests.map((manifest) => [manifest.id, manifest])),
    [visibleManifests],
  );

  useEffect(() => {
    safeSetItem(FAVORITES_KEY, JSON.stringify(favorites));
  }, [favorites]);

  useEffect(() => {
    safeSetItem(RECENTS_KEY, JSON.stringify(recent));
  }, [recent]);

  useEffect(() => {
    function focusSearch(): void {
      if (collapsed) {
        setCollapsed(false);
        safeSetItem(COLLAPSED_KEY, "0");
        window.setTimeout(() => searchRef.current?.focus(), 0);
        return;
      }
      searchRef.current?.focus();
    }
    window.addEventListener("nodyra:focus-node-search", focusSearch);
    return () => window.removeEventListener("nodyra:focus-node-search", focusSearch);
  }, [collapsed]);

  useEffect(() => {
    function toggleNodePalette(): void {
      setCollapsed((v) => {
        const next = !v;
        safeSetItem(COLLAPSED_KEY, next ? "1" : "0");
        return next;
      });
    }
    window.addEventListener("nodyra:toggle-node-palette", toggleNodePalette);
    return () =>
      window.removeEventListener("nodyra:toggle-node-palette", toggleNodePalette);
  }, []);

  const installMcpManifests = useCallback(
    (connections: MCPConnection[]) => {
      const current = useEditor.getState().manifests;
      const withoutMcpTools = current.filter(
        (manifest) => !manifest.id.startsWith("mcp_tool__"),
      );
      const mcpTools = buildMcpToolManifests(connections);
      setManifests([...withoutMcpTools, ...mcpTools]);
    },
    [setManifests],
  );

  const loadMcpConnections = useCallback(async () => {
    setMcpLoading(true);
    setMcpError("");
    try {
      const connections = await api.listMcpConnections();
      if (!mcpMountedRef.current) return;
      setMcpConnections(connections);
      installMcpManifests(connections);
    } catch (err) {
      if (!mcpMountedRef.current) return;
      setMcpConnections([]);
      installMcpManifests([]);
      setMcpError(errorMessage(err));
    } finally {
      if (mcpMountedRef.current) setMcpLoading(false);
    }
  }, [installMcpManifests]);

  useEffect(() => {
    void loadMcpConnections();
  }, [loadMcpConnections]);

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
      new Set(browseManifests.map((manifest) => manifest.category)),
    );
    return names.sort((a, b) => {
      const ai = CATEGORY_ORDER.indexOf(a);
      const bi = CATEGORY_ORDER.indexOf(b);
      const ar = ai === -1 ? Number.MAX_SAFE_INTEGER : ai;
      const br = bi === -1 ? Number.MAX_SAFE_INTEGER : bi;
      return ar === br ? a.localeCompare(b) : ar - br;
    });
  }, [browseManifests]);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const source = q ? visibleManifests : browseManifests;
    const matched = source
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
      nodes: byCategory.get(category)!.sort(browseSort),
    }));
  }, [browseManifests, categoryFilter, query, visibleManifests]);
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
    setVisibleCounts({});
  }, [query, categoryFilter, paletteView]);

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

  const toggleFavorite = useCallback(function toggleFavorite(id: string): void {
    setFavorites((items) =>
      items.includes(id) ? items.filter((item) => item !== id) : [id, ...items],
    );
  }, []);

  const recordRecent = useCallback(function recordRecent(id: string): void {
    setRecent((items) => [id, ...items.filter((item) => item !== id)].slice(0, MAX_RECENTS));
  }, []);

  const handleUseMcpTool = useCallback(
    (manifestId: string): void => {
      addNode(manifestId, { x: 424, y: 224 });
      recordRecent(manifestId);
    },
    [addNode, recordRecent],
  );

  const handleSyncMcpConnection = useCallback(
    async (connection: MCPConnection): Promise<void> => {
      setMcpSyncingId(connection.id);
      setMcpError("");
      try {
        await api.syncMcpConnection(connection.id);
        await loadMcpConnections();
      } catch (err) {
        setMcpError(errorMessage(err));
      } finally {
        setMcpSyncingId(null);
      }
    },
    [loadMcpConnections],
  );

  if (collapsed) {
    return (
      <aside className="palette palette--collapsed" aria-label="Node picker">
        <button
          type="button"
          className="palette-collapse-btn"
          aria-label="Expand node picker"
          title="Expand node picker (Shift+P)"
          onClick={toggleCollapsed}
        >
          <CaretRight size={14} weight="bold" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="palette" aria-label="Node picker" data-tour-id="palette">
      <div className="panel-head">
        <h2>Nodes</h2>
        <div className="panel-head-right">
          <span className="panel-count">
            {query.trim()
              ? `${matchedCount}/${visibleManifests.length}`
              : browseManifests.length}
          </span>
          <button
            type="button"
            className="palette-collapse-btn"
            aria-label="Collapse node picker"
            title="Collapse node picker (Shift+P)"
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
          aria-label="Search nodes"
          placeholder="Search all nodes…"
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
      <div className="palette-view-switch" role="group" aria-label="Node catalog scope">
        <button
          type="button"
          className={paletteView === "core" ? "active" : ""}
          aria-pressed={paletteView === "core"}
          onClick={() => {
            setPaletteView("core");
            setCategoryFilter("all");
            setQuery("");
            safeSetItem(VIEW_KEY, "core");
          }}
        >
          Core
        </button>
        <button
          type="button"
          className={paletteView === "all" ? "active" : ""}
          aria-pressed={paletteView === "all"}
          onClick={() => {
            setPaletteView("all");
            setCategoryFilter("all");
            setQuery("");
            safeSetItem(VIEW_KEY, "all");
          }}
        >
          All nodes
        </button>
        <span>
          {paletteView === "core"
            ? `${curatedManifests.length} curated`
            : `${visibleManifests.length} installed`}
        </span>
      </div>
      <div ref={chipsRef} className="palette-chips" aria-label="Node categories">
        <button
          type="button"
          className={categoryFilter === "all" ? "active" : ""}
          onClick={() => {
            setQuery("");
            setCategoryFilter("all");
            setExpandedGroups(new Set());
            safeSetItem(EXPANDED_GROUPS_KEY, "[]");
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
                safeSetItem(EXPANDED_GROUPS_KEY, JSON.stringify([...next]));
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
        {showQuickSections && paletteView === "all" && (
          <McpToolsSection
            connections={mcpConnections}
            loading={mcpLoading}
            error={mcpError}
            syncingId={mcpSyncingId}
            onRefresh={() => void loadMcpConnections()}
            onSync={(connection) => void handleSyncMcpConnection(connection)}
            onUseTool={handleUseMcpTool}
          />
        )}
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
          const visibleCount = visibleCounts[group.category] ?? INITIAL_RENDERED_NODES;
          const visibleGroupNodes = group.nodes.slice(0, visibleCount);
          // Subgroup integration nodes by service inside the category — only
          // when browsing (no active query) and at least 2 nodes share an
          // integration prefix. Otherwise render flat.
          const subgroups: { label: string | null; nodes: NodeManifest[] }[] = [];
          if (!query.trim()) {
            const integrationBuckets = new Map<string, NodeManifest[]>();
            const standalone: NodeManifest[] = [];
            for (const node of visibleGroupNodes) {
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
          const renderNodes = subgroups.length > 0 ? null : visibleGroupNodes;
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
              {!isGroupCollapsed && visibleGroupNodes.length < group.nodes.length && (
                <button
                  type="button"
                  className="palette-load-more"
                  onClick={() =>
                    setVisibleCounts((current) => ({
                      ...current,
                      [group.category]: Math.min(
                        group.nodes.length,
                        visibleCount + INITIAL_RENDERED_NODES,
                      ),
                    }))
                  }
                >
                  Show {Math.min(INITIAL_RENDERED_NODES, group.nodes.length - visibleCount)} more
                </button>
              )}
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
      <button
        type="button"
        className="palette-generate-btn"
        onClick={() => window.dispatchEvent(new Event("nodyra:open-generate-modal"))}
      >
        <Sparkle size={14} weight="fill" />
        Generate Node
      </button>
    </aside>
  );
}
