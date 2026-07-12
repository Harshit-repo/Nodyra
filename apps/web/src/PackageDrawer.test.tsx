import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { PackageDrawer } from "./PackageDrawer";
import type { Environment } from "./types";

const env: Environment = {
  id: "e1",
  name: "data-science",
  is_global: false,
  python_version: "3.12",
  packages: ["pandas", "duckdb"],
  status: "ready",
  status_detail: "",
  description: "",
  runner_pool_size: 1,
  runner_pool_max: null,
  effective_pool_max: 1,
  runner_pool_id: null,
  runner_pool_name: null,
  worker_rss_estimate_bytes: null,
  backend: "venv",
  backend_config: {},
  interpreter: "cpython",
  runtime_flags: {},
  created_at: "",
  updated_at: "",
};

afterEach(() => vi.restoreAllMocks());

describe("PackageDrawer", () => {
  it("adds comma-separated packages via setPackages", async () => {
    vi.spyOn(api, "packageUsage").mockResolvedValue({ packages: [] });
    const setPackages = vi.spyOn(api, "setPackages").mockResolvedValue(env);
    const onChanged = vi.fn();
    render(<PackageDrawer env={env} onClose={() => {}} onChanged={onChanged} />);

    fireEvent.change(screen.getByPlaceholderText(/pandas, numpy/i), {
      target: { value: "numpy, httpx>=0.27" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() =>
      expect(setPackages).toHaveBeenCalledWith("e1", [
        "pandas",
        "duckdb",
        "numpy",
        "httpx>=0.27",
      ]),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("tags packages used by nodes", async () => {
    vi.spyOn(api, "packageUsage").mockResolvedValue({
      packages: [
        {
          package: "duckdb",
          used_by: [
            {
              workflow_id: "w1",
              workflow_name: "WF",
              node_id: "n1",
              node_label: "Query",
            },
          ],
        },
      ],
    });
    render(<PackageDrawer env={env} onClose={() => {}} onChanged={() => {}} />);
    expect(await screen.findByText(/node: Query/i)).toBeInTheDocument();
  });
});
