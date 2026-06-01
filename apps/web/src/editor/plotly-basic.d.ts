// Minimal ambient types for plotly.js-basic-dist-min, which ships no .d.ts.
// We only use the small surface needed by PlotlyChartView (react/purge).
declare module "plotly.js-basic-dist-min" {
  // Plotly trace/layout/config objects are deeply dynamic; the chart spec we
  // pass is validated upstream, so a permissive type keeps this thin.
  export type PlotData = Record<string, unknown>;
  export type PlotLayout = Record<string, unknown>;
  export type PlotConfig = Record<string, unknown>;

  export function react(
    root: HTMLElement,
    data: PlotData[],
    layout?: PlotLayout,
    config?: PlotConfig,
  ): Promise<void>;

  export function purge(root: HTMLElement): void;

  const Plotly: {
    react: typeof react;
    purge: typeof purge;
  };
  export default Plotly;
}
