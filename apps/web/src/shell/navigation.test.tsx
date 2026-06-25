import { describe, expect, it } from "vitest";

import {
  getMobileMoreRoutes,
  getMobilePrimaryRoutes,
  getNavigationRoutes,
  getRouteTitle,
} from "./navigation";

describe("application navigation registry", () => {
  it("exposes four destinations before the mobile More action", () => {
    expect(getMobilePrimaryRoutes().map((route) => route.id)).toEqual([
      "workflows",
      "deployments",
      "executions",
      "environments",
    ]);
  });

  it("separates workspace-scoped and instance-global administration", () => {
    expect(getNavigationRoutes("admin", null)).toEqual([]);
    expect(getMobileMoreRoutes({ role: "viewer" }, "viewer").map((route) => route.id)).not.toContain(
      "security",
    );
    expect(getMobileMoreRoutes({ role: "admin" }, "viewer").map((route) => route.id)).toContain(
      "security",
    );
    expect(getMobileMoreRoutes({ role: "viewer" }, "admin").map((route) => route.id)).toContain(
      "organization",
    );
    expect(getMobileMoreRoutes({ role: "viewer" }, "admin").map((route) => route.id)).not.toContain(
      "security",
    );
    expect(getMobileMoreRoutes({ role: "viewer" }, "admin").map((route) => route.id)).toContain(
      "activity",
    );
  });

  it("separates local, workspace and instance capabilities", () => {
    expect(getNavigationRoutes("admin", null, null, true).map((route) => route.id)).toEqual([
      "security",
      "activity",
    ]);
    expect(getNavigationRoutes("resource", { role: "owner" }, "viewer").map((route) => route.id)).not.toContain("credentials");
    expect(getNavigationRoutes("resource", { role: "viewer" }, "editor").map((route) => route.id)).toContain("credentials");
    expect(getNavigationRoutes("admin", { role: "admin" }, "viewer").map((route) => route.id)).not.toContain("organization");
    expect(getNavigationRoutes("resource", { role: "owner" }, "owner", false, false).map((route) => route.id)).toContain("credentials");
    expect(getNavigationRoutes("admin", { role: "owner" }, "owner", false, false).map((route) => route.id)).not.toContain("organization");
  });

  it("derives stable titles for exact and nested routes", () => {
    expect(getRouteTitle("/")).toBe("Workflows");
    expect(getRouteTitle("/workflows/abc")).toBe("Workflows");
    expect(getRouteTitle("/runner-pools/pool-a")).toBe("Runners");
    expect(getRouteTitle("/unknown", "Page not found")).toBe("Page not found");
  });
});
