import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChartView } from "./ChartView";
import type { ChartRef } from "./chartValues";

const chart: ChartRef = {
  __nodyra_chart__: true,
  version: 1,
  chart_type: "bar",
  title: "Revenue",
  x_label: "Month",
  y_label: "Revenue",
  categories: ["Jan", "Feb"],
  series: [
    {
      name: "Actual",
      points: [
        { y: 10 },
        { y: 20 },
      ],
    },
  ],
};

describe("ChartView", () => {
  it("uses the lightweight SVG renderer before loading Plotly", () => {
    render(<ChartView chart={chart} />);

    expect(screen.getByRole("button", { name: "Simple" })).toHaveClass("active");
    expect(screen.getByRole("button", { name: "Interactive" })).not.toHaveClass(
      "active",
    );
    expect(screen.queryByText(/Loading interactive chart/i)).not.toBeInTheDocument();
  });
});
