// Interactive chart renderer backed by Plotly (basic dist: bar/scatter/pie).
//
// Converts the same `ChartRef` envelope the SVG `ChartView` uses into Plotly
// traces, so charts stay defined by the node spec — this is purely a richer,
// interactive presentation (zoom, pan, hover, box/lasso select, export PNG).
//
// Plotly's bundle is large, so this module is imported lazily by `ChartView`
// only when the user switches to the interactive view.

import { useEffect, useMemo, useRef } from "react";
import Plotly from "plotly.js-basic-dist-min";
import type { PlotData, PlotLayout } from "plotly.js-basic-dist-min";
import type { ChartRef } from "./chartValues";
import { seriesColor } from "./chartValues";

function toTraces(chart: ChartRef): PlotData[] {
  const categories = chart.categories ?? [];
  const catLabel = (i: number) =>
    categories[i] !== undefined ? categories[i] : i;

  if (chart.chart_type === "pie") {
    const series = chart.series[0];
    const points = series?.points ?? [];
    return [
      {
        type: "pie",
        labels: points.map((p, i) => p.label ?? String(catLabel(i))),
        values: points.map((p) => p.y),
        textinfo: "label+percent",
        hovertemplate: "%{label}: %{value} (%{percent})<extra></extra>",
        marker: {
          colors: points.map((_, i) => seriesColor(i)),
        },
      },
    ];
  }

  return chart.series.map((series, si) => {
    const color = seriesColor(si);
    const x = series.points.map((p, i) =>
      p.x !== undefined ? p.x : catLabel(i),
    );
    const y = series.points.map((p) => p.y);
    if (chart.chart_type === "bar") {
      return {
        type: "bar",
        name: series.name,
        x,
        y,
        marker: { color },
        hovertemplate: `${series.name}: %{y}<extra></extra>`,
      };
    }
    // line / area / scatter all map to the scatter trace with different modes.
    const isScatter = chart.chart_type === "scatter";
    return {
      type: "scatter",
      mode: isScatter ? "markers" : "lines+markers",
      name: series.name,
      x,
      y,
      fill: chart.chart_type === "area" ? "tozeroy" : "none",
      line: { color, width: 2 },
      marker: { color, size: isScatter ? 7 : 5 },
      hovertemplate: `${series.name}: %{y}<extra></extra>`,
    };
  });
}

export function PlotlyChartView({ chart }: { chart: ChartRef }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const traces = useMemo(() => toTraces(chart), [chart]);

  const layout = useMemo<PlotLayout>(
    () => ({
      title: chart.title ? { text: chart.title, font: { size: 14 } } : undefined,
      margin: { l: 52, r: 16, t: chart.title ? 36 : 16, b: 44 },
      xaxis: chart.x_label ? { title: { text: chart.x_label } } : {},
      yaxis: chart.y_label ? { title: { text: chart.y_label } } : {},
      barmode: "group",
      showlegend: chart.series.length > 1 || chart.chart_type === "pie",
      legend: { orientation: "h", y: -0.2 },
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      autosize: true,
      height: 320,
    }),
    [chart],
  );

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    let cancelled = false;
    Plotly.react(node, traces, layout, {
      responsive: true,
      displaylogo: false,
      // Trim the modebar to the useful actions for our chart types.
      modeBarButtonsToRemove: ["select2d", "lasso2d"],
      toImageButtonOptions: {
        format: "png",
        filename: (chart.title || "chart").replace(/\s+/g, "_"),
      },
    }).catch(() => {
      /* render errors are non-fatal; the SVG view remains available */
    });
    return () => {
      if (!cancelled && node) Plotly.purge(node);
      cancelled = true;
    };
  }, [traces, layout, chart.title]);

  return <div className="plotly-chart" ref={ref} />;
}

export default PlotlyChartView;
