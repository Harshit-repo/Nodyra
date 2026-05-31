// Drag-and-drop, resizable report canvas. Renders ReportRef envelopes from
// the build_report node. Tiles snap to a column grid; layout lives in local
// state (rearranging here does not persist back to the run output).

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReportRef, ReportTile, ReportTileLayout } from "./chartValues";
import { asChartRef, formatNumber } from "./chartValues";
import { ChartView } from "./ChartView";

const ROW_H = 28; // px per grid row unit
const MIN_W = 2;
const MIN_H = 3;

type Layouts = Record<string, ReportTileLayout>;

interface DragState {
  id: string;
  mode: "move" | "resize";
  startMouseX: number;
  startMouseY: number;
  start: ReportTileLayout;
  colPx: number;
}

export function ReportView({ report }: { report: ReportRef }) {
  const columns = Math.max(1, report.columns || 12);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [layouts, setLayouts] = useState<Layouts>(() => initialLayouts(report.tiles));
  const dragRef = useRef<DragState | null>(null);

  // Re-seed layout if a new report object flows in.
  useEffect(() => {
    setLayouts(initialLayouts(report.tiles));
  }, [report]);

  const onMouseDown = useCallback(
    (event: React.MouseEvent, tile: ReportTile, mode: "move" | "resize") => {
      event.preventDefault();
      const container = containerRef.current;
      if (!container) return;
      const colPx = container.clientWidth / columns;
      dragRef.current = {
        id: tile.id,
        mode,
        startMouseX: event.clientX,
        startMouseY: event.clientY,
        start: layouts[tile.id] ?? tile.layout,
        colPx,
      };
    },
    [columns, layouts],
  );

  useEffect(() => {
    function onMove(event: MouseEvent) {
      const drag = dragRef.current;
      if (!drag) return;
      const dxCols = Math.round((event.clientX - drag.startMouseX) / drag.colPx);
      const dyRows = Math.round((event.clientY - drag.startMouseY) / ROW_H);
      setLayouts((prev) => {
        const cur = prev[drag.id] ?? drag.start;
        let next: ReportTileLayout;
        if (drag.mode === "move") {
          const x = Math.min(Math.max(drag.start.x + dxCols, 0), columns - cur.w);
          const y = Math.max(drag.start.y + dyRows, 0);
          next = { ...cur, x, y };
        } else {
          const w = Math.min(Math.max(drag.start.w + dxCols, MIN_W), columns - cur.x);
          const h = Math.max(drag.start.h + dyRows, MIN_H);
          next = { ...cur, w, h };
        }
        return { ...prev, [drag.id]: next };
      });
    }
    function onUp() {
      dragRef.current = null;
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [columns]);

  const canvasRows = Math.max(
    ...Object.values(layouts).map((l) => l.y + l.h),
    8,
  );

  return (
    <div className="report-view">
      <div className="report-head">
        <strong className="report-title">{report.title || "Report"}</strong>
        <span className="report-hint">Drag tiles to rearrange · drag corner to resize</span>
      </div>
      <div
        ref={containerRef}
        className="report-canvas"
        style={{ height: canvasRows * ROW_H + ROW_H }}
      >
        {report.tiles.map((tile) => {
          const layout = layouts[tile.id] ?? tile.layout;
          const style = {
            left: `${(layout.x / columns) * 100}%`,
            width: `${(layout.w / columns) * 100}%`,
            top: layout.y * ROW_H,
            height: layout.h * ROW_H,
          };
          return (
            <div key={tile.id} className="report-tile" style={style}>
              <div
                className="report-tile-head"
                onMouseDown={(e) => onMouseDown(e, tile, "move")}
              >
                <span className="report-tile-title">{tile.title || tileTypeLabel(tile)}</span>
              </div>
              <div className="report-tile-body">
                <TileBody tile={tile} />
              </div>
              <div
                className="report-tile-resize"
                onMouseDown={(e) => onMouseDown(e, tile, "resize")}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function initialLayouts(tiles: ReportTile[]): Layouts {
  const out: Layouts = {};
  for (const tile of tiles) out[tile.id] = { ...tile.layout };
  return out;
}

function tileTypeLabel(tile: ReportTile): string {
  switch (tile.type) {
    case "chart":
      return "Chart";
    case "table":
      return "Table";
    case "metric":
      return "Metrics";
    default:
      return "Note";
  }
}

function TileBody({ tile }: { tile: ReportTile }) {
  if (tile.type === "chart") {
    const chart = asChartRef(tile.data);
    return chart ? <ChartView chart={chart} /> : <div className="report-tile-empty">No chart</div>;
  }
  if (tile.type === "table") {
    return <TableTile data={tile.data} />;
  }
  if (tile.type === "metric") {
    return <MetricTile data={tile.data} />;
  }
  return <div className="report-tile-text">{String(tile.data ?? "")}</div>;
}

function TableTile({ data }: { data: unknown }) {
  const table = data as { columns?: string[]; rows?: Record<string, unknown>[] } | null;
  const columns = table?.columns ?? [];
  const rows = table?.rows ?? [];
  if (!columns.length || !rows.length) {
    return <div className="report-tile-empty">Empty table</div>;
  }
  return (
    <div className="report-table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} title={cell(row[c])}>
                  {cell(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricTile({ data }: { data: unknown }) {
  const entries = data && typeof data === "object" ? Object.entries(data as Record<string, unknown>) : [];
  if (!entries.length) return <div className="report-tile-empty">No metrics</div>;
  return (
    <div className="report-metrics">
      {entries.map(([key, value]) => (
        <div key={key} className="report-metric">
          <span className="report-metric-value">
            {typeof value === "number" ? formatNumber(value) : String(value)}
          </span>
          <span className="report-metric-key">{key}</span>
        </div>
      ))}
    </div>
  );
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
