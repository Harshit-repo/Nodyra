// Dependency-free SVG chart renderer with hover tooltips.
// Renders ChartRef envelopes (bar/line/area/scatter/pie) from the chart nodes.

import { useMemo, useState } from "react";
import type { ChartRef, ChartType } from "./chartValues";
import { formatNumber, seriesColor } from "./chartValues";

const W = 520;
const H = 280;
const PAD = { top: 28, right: 16, bottom: 40, left: 48 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

interface Hover {
  x: number;
  y: number;
  lines: string[];
}

function niceLabel(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return formatNumber(value);
  return String(value);
}

export function ChartView({ chart }: { chart: ChartRef }) {
  const [hover, setHover] = useState<Hover | null>(null);

  const flatY = useMemo(
    () =>
      chart.series.flatMap((s) => s.points.map((p) => p.y)).filter((n) => Number.isFinite(n)),
    [chart],
  );
  const hasData = flatY.length > 0;

  const yMin = hasData ? Math.min(0, ...flatY) : 0;
  const yMax = hasData ? Math.max(...flatY) : 1;
  const ySpan = yMax - yMin || 1;

  const categories = chart.categories ?? [];
  const catCount = Math.max(
    categories.length,
    ...chart.series.map((s) => s.points.length),
    1,
  );

  const yToPx = (y: number) => PAD.top + PLOT_H - ((y - yMin) / ySpan) * PLOT_H;
  const catToPx = (index: number) =>
    PAD.left + (catCount === 1 ? PLOT_W / 2 : (index / (catCount - 1)) * PLOT_W);
  const bandX = (index: number) => PAD.left + (index + 0.5) * (PLOT_W / catCount);

  const yTicks = useMemo(() => {
    const ticks: number[] = [];
    const steps = 4;
    for (let i = 0; i <= steps; i += 1) ticks.push(yMin + (ySpan * i) / steps);
    return ticks;
  }, [yMin, ySpan]);

  if (!hasData && chart.chart_type !== "pie") {
    return (
      <div className="chart-view chart-empty">
        <div className="chart-title">{chart.title || "Chart"}</div>
        <div className="chart-empty-note">No numeric data to plot.</div>
      </div>
    );
  }

  return (
    <div className="chart-view">
      {chart.title && <div className="chart-title">{chart.title}</div>}
      <div className="chart-svg-wrap">
        <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" preserveAspectRatio="xMidYMid meet">
          {chart.chart_type === "pie" ? (
            <PieChart chart={chart} onHover={setHover} />
          ) : (
            <>
              <AxesAndGrid
                chart={chart}
                yTicks={yTicks}
                yToPx={yToPx}
                catToPx={catToPx}
                bandX={bandX}
                categories={categories}
                catCount={catCount}
              />
              <Plot
                chart={chart}
                type={chart.chart_type}
                yToPx={yToPx}
                catToPx={catToPx}
                bandX={bandX}
                catCount={catCount}
                yBase={yToPx(Math.max(yMin, 0))}
                onHover={setHover}
              />
            </>
          )}
        </svg>
        {hover && (
          <div
            className="chart-tooltip"
            style={{ left: `${(hover.x / W) * 100}%`, top: `${(hover.y / H) * 100}%` }}
          >
            {hover.lines.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        )}
      </div>
      {chart.series.length > 1 && (
        <div className="chart-legend">
          {chart.series.map((s, i) => (
            <span key={s.name} className="chart-legend-item">
              <span className="chart-legend-dot" style={{ background: seriesColor(i) }} />
              {s.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function AxesAndGrid({
  chart,
  yTicks,
  yToPx,
  catToPx,
  bandX,
  categories,
  catCount,
}: {
  chart: ChartRef;
  yTicks: number[];
  yToPx: (y: number) => number;
  catToPx: (i: number) => number;
  bandX: (i: number) => number;
  categories: unknown[];
  catCount: number;
}) {
  const bandLike = chart.chart_type === "bar";
  const labelStep = Math.ceil(catCount / 8);
  return (
    <g>
      {yTicks.map((t, i) => {
        const y = yToPx(t);
        return (
          <g key={i}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} className="chart-grid" />
            <text x={PAD.left - 8} y={y + 4} className="chart-axis-label" textAnchor="end">
              {formatNumber(t)}
            </text>
          </g>
        );
      })}
      {Array.from({ length: catCount }).map((_, i) => {
        if (i % labelStep !== 0 && i !== catCount - 1) return null;
        const x = bandLike ? bandX(i) : catToPx(i);
        return (
          <text
            key={i}
            x={x}
            y={H - PAD.bottom + 18}
            className="chart-axis-label"
            textAnchor="middle"
          >
            {niceLabel(categories[i])}
          </text>
        );
      })}
      {chart.x_label && (
        <text x={PAD.left + PLOT_W / 2} y={H - 4} className="chart-axis-title" textAnchor="middle">
          {chart.x_label}
        </text>
      )}
      {chart.y_label && (
        <text
          x={12}
          y={PAD.top + PLOT_H / 2}
          className="chart-axis-title"
          textAnchor="middle"
          transform={`rotate(-90 12 ${PAD.top + PLOT_H / 2})`}
        >
          {chart.y_label}
        </text>
      )}
    </g>
  );
}

function Plot({
  chart,
  type,
  yToPx,
  catToPx,
  bandX,
  catCount,
  yBase,
  onHover,
}: {
  chart: ChartRef;
  type: ChartType;
  yToPx: (y: number) => number;
  catToPx: (i: number) => number;
  bandX: (i: number) => number;
  catCount: number;
  yBase: number;
  onHover: (h: Hover | null) => void;
}) {
  if (type === "bar") {
    const seriesCount = chart.series.length || 1;
    const band = PLOT_W / catCount;
    const groupW = band * 0.72;
    const barW = groupW / seriesCount;
    return (
      <g>
        {chart.series.map((s, si) =>
          s.points.map((p, pi) => {
            const cx = bandX(pi) - groupW / 2 + si * barW;
            const top = yToPx(p.y);
            const h = Math.abs(yBase - top);
            const y = Math.min(top, yBase);
            return (
              <rect
                key={`${si}-${pi}`}
                x={cx}
                y={y}
                width={Math.max(barW - 1, 1)}
                height={Math.max(h, 0.5)}
                fill={seriesColor(si)}
                className="chart-bar"
                onMouseEnter={() =>
                  onHover({
                    x: cx + barW / 2,
                    y: top,
                    lines: [niceLabel(p.label ?? chart.categories[pi]), `${s.name}: ${formatNumber(p.y)}`],
                  })
                }
                onMouseLeave={() => onHover(null)}
              />
            );
          }),
        )}
      </g>
    );
  }

  // line / area / scatter
  return (
    <g>
      {chart.series.map((s, si) => {
        const pts = s.points.map((p, pi) => ({
          px: catToPx(pi),
          py: yToPx(p.y),
          p,
          pi,
        }));
        const path = pts.map((q, i) => `${i === 0 ? "M" : "L"}${q.px},${q.py}`).join(" ");
        const color = seriesColor(si);
        return (
          <g key={si}>
            {type === "area" && pts.length > 0 && (
              <path
                d={`${path} L${pts[pts.length - 1].px},${yBase} L${pts[0].px},${yBase} Z`}
                fill={color}
                fillOpacity={0.18}
                stroke="none"
              />
            )}
            {type !== "scatter" && (
              <path d={path} fill="none" stroke={color} strokeWidth={2} className="chart-line" />
            )}
            {pts.map((q) => (
              <circle
                key={q.pi}
                cx={q.px}
                cy={q.py}
                r={type === "scatter" ? 4 : 3}
                fill={color}
                className="chart-dot"
                onMouseEnter={() =>
                  onHover({
                    x: q.px,
                    y: q.py,
                    lines: [
                      niceLabel(q.p.label ?? chart.categories[q.pi]),
                      `${s.name}: ${formatNumber(q.p.y)}`,
                    ],
                  })
                }
                onMouseLeave={() => onHover(null)}
              />
            ))}
          </g>
        );
      })}
    </g>
  );
}

function PieChart({ chart, onHover }: { chart: ChartRef; onHover: (h: Hover | null) => void }) {
  const points = chart.series[0]?.points ?? [];
  const total = points.reduce((sum, p) => sum + (p.y > 0 ? p.y : 0), 0);
  const cx = W / 2;
  const cy = H / 2;
  const r = Math.min(PLOT_H, PLOT_W) / 2 - 6;
  if (total <= 0) {
    return (
      <text x={cx} y={cy} className="chart-empty-note" textAnchor="middle">
        No positive values to plot.
      </text>
    );
  }
  let angle = -Math.PI / 2;
  return (
    <g>
      {points.map((p, i) => {
        const frac = (p.y > 0 ? p.y : 0) / total;
        const next = angle + frac * Math.PI * 2;
        const x1 = cx + r * Math.cos(angle);
        const y1 = cy + r * Math.sin(angle);
        const x2 = cx + r * Math.cos(next);
        const y2 = cy + r * Math.sin(next);
        const large = frac > 0.5 ? 1 : 0;
        const mid = (angle + next) / 2;
        const d = `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2} Z`;
        angle = next;
        return (
          <path
            key={i}
            d={d}
            fill={seriesColor(i)}
            className="chart-pie-slice"
            onMouseEnter={() =>
              onHover({
                x: cx + (r / 1.6) * Math.cos(mid),
                y: cy + (r / 1.6) * Math.sin(mid),
                lines: [niceLabel(p.label), `${formatNumber(p.y)} (${(frac * 100).toFixed(1)}%)`],
              })
            }
            onMouseLeave={() => onHover(null)}
          />
        );
      })}
    </g>
  );
}
