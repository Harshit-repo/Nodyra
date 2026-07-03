// Parsers + types for the chart/report envelopes produced by the
// `chart`, `metrics_chart` and `build_report` nodes (nodyra_nodes/charts.py).

export interface ChartPoint {
  x?: number | string;
  y: number;
  label?: string;
}

export interface ChartSeries {
  name: string;
  points: ChartPoint[];
}

export type ChartType = "bar" | "line" | "area" | "scatter" | "pie";

export interface ChartRef {
  __nodyra_chart__: true;
  version: number;
  chart_type: ChartType;
  title: string;
  x_label: string;
  y_label: string;
  categories: unknown[];
  series: ChartSeries[];
}

export interface ReportTileLayout {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type ReportTileType = "chart" | "table" | "metric" | "text";

export interface ReportTile {
  id: string;
  type: ReportTileType;
  title: string;
  data: unknown;
  layout: ReportTileLayout;
}

export interface ReportRef {
  __nodyra_report__: true;
  version: number;
  title: string;
  columns: number;
  tiles: ReportTile[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function asChartRef(value: unknown): ChartRef | null {
  if (!isRecord(value)) return null;
  if (value.__nodyra_chart__ !== true || value.version !== 1) return null;
  if (!Array.isArray(value.series)) return null;
  return value as unknown as ChartRef;
}

export function asReportRef(value: unknown): ReportRef | null {
  if (!isRecord(value)) return null;
  if (value.__nodyra_report__ !== true || value.version !== 1) return null;
  if (!Array.isArray(value.tiles)) return null;
  return value as unknown as ReportRef;
}

const PALETTE = [
  "#6366f1",
  "#22c55e",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#a855f7",
  "#ec4899",
  "#84cc16",
];

export function seriesColor(index: number): string {
  return PALETTE[index % PALETTE.length];
}

export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 0.001 || abs >= 1_000_000)) {
    return value.toExponential(2);
  }
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}
