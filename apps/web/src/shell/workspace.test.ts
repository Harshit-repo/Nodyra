import { describe, expect, it } from "vitest";

import { resolveWorkspaceSelection } from "./workspace";

const orgs = [
  { id: "one", name: "One", slug: "one", status: "active", role: "admin" },
  { id: "two", name: "Two", slug: "two", status: "suspended", role: "owner" },
];

describe("resolveWorkspaceSelection", () => {
  it("uses a valid active stored workspace", () => {
    expect(resolveWorkspaceSelection(orgs, "one")).toEqual({
      current: orgs[0],
      needsReconcile: false,
      nextStoredId: "one",
    });
  });

  it("repairs a missing or inactive stored workspace to an active membership", () => {
    expect(resolveWorkspaceSelection(orgs, "removed").needsReconcile).toBe(true);
    expect(resolveWorkspaceSelection(orgs, "two").current?.id).toBe("one");
  });

  it("returns a terminal empty state when no active membership exists", () => {
    expect(
      resolveWorkspaceSelection(
        [{ id: "suspended", name: "Suspended", slug: "suspended", status: "suspended", role: "admin" }],
        "suspended",
      ),
    ).toEqual({ current: null, needsReconcile: false, nextStoredId: null });
  });
});
