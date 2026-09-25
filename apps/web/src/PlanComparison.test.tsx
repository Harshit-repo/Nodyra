import { render, screen, waitFor } from "@testing-library/react";
import { axe } from "vitest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { formatCap, PlanComparison, type Plan } from "./PlanComparison";

const PLANS: Plan[] = [
  {
    edition: "community",
    name: "Community",
    price_id: "",
    features: ["sandbox"],
    limits: { seats: 5, environments: 3, runners: 1, deployments: 10 },
    current: true,
  },
  {
    edition: "pro",
    name: "Pro",
    price_id: "price_pro",
    features: ["sandbox", "observability", "git_sync"],
    limits: { seats: 10, environments: 10, runners: 5, deployments: 0 },
    current: false,
  },
  {
    edition: "enterprise",
    name: "Enterprise",
    price_id: "price_ent",
    features: ["sandbox", "observability", "git_sync", "sso", "audit_logs"],
    limits: { seats: 0, environments: 0, runners: 0, deployments: 0 },
    current: false,
  },
];

afterEach(() => vi.restoreAllMocks());

describe("PlanComparison", () => {
  it("shows every edition and marks the active one", async () => {
    vi.spyOn(api, "listPlans").mockResolvedValue(PLANS);
    render(<PlanComparison />);

    await screen.findByText("Community");
    expect(screen.getByText("Pro")).toBeInTheDocument();
    expect(screen.getByText("Enterprise")).toBeInTheDocument();
    expect(screen.getByText("Current plan")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contact sales for a license" })).toHaveAttribute("href", "mailto:sharma.har97@gmail.com?subject=Nodyra%20license%20enquiry");
  });

  it("names what an upgrade would actually unlock", async () => {
    // The reason this component exists: a capped user needs to see the
    // difference, not just a list of tiers.
    vi.spyOn(api, "listPlans").mockResolvedValue(PLANS);
    render(<PlanComparison />);

    await screen.findByText("Pro");
    // Only Enterprise adds SSO over Community.
    expect(screen.getByText("Single sign-on")).toBeInTheDocument();
    // Both Pro and Enterprise add GitOps sync relative to Community, and both
    // say so — the comparison is against the plan you are on, per card.
    expect(screen.getAllByText("GitOps sync")).toHaveLength(2);
    // Already on Community, so its own feature is not listed as an addition.
    expect(screen.queryByText("Sandboxed execution")).not.toBeInTheDocument();
  });

  it("renders zero caps as Unlimited, not as zero", async () => {
    // 0 means unlimited throughout the licensing code. Showing "0 seats" on
    // Enterprise would read as the most restrictive plan.
    vi.spyOn(api, "listPlans").mockResolvedValue(PLANS);
    render(<PlanComparison />);

    await screen.findByText("Enterprise");
    expect(screen.getAllByText("Unlimited").length).toBeGreaterThan(0);
    expect(formatCap(0)).toBe("Unlimited");
    expect(formatCap(5)).toBe("5");
  });

  it("degrades to a note when plans cannot be loaded", async () => {
    // The licence-key field above still works, so a failure here must not take
    // the whole panel down.
    vi.spyOn(api, "listPlans").mockRejectedValue(new Error("network down"));
    render(<PlanComparison />);

    await waitFor(() =>
      expect(screen.getByText(/Plan comparison unavailable/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/network down/)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    vi.spyOn(api, "listPlans").mockResolvedValue(PLANS);
    const { container } = render(<PlanComparison />);
    await screen.findByText("Pro");

    const results = await axe(container);
    expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });
});
