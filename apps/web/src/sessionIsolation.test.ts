import { afterEach, describe, expect, it } from "vitest";

import { getOrgId, getUser, setOrgId, setUser } from "./api";
import { queryClient } from "./queries";
import { clearClientSession } from "./sessionIsolation";

afterEach(() => clearClientSession());

describe("session isolation", () => {
  it("clears cached tenant data and organization selection on sign-out", () => {
    setUser({ id: "user-a", email: "a@example.com", name: "A", company: "Nodyra", role: "owner" });
    setOrgId("org-a");
    queryClient.setQueryData(["workflows"], [{ id: "secret-a" }]);

    clearClientSession();

    expect(getUser()).toBeNull();
    expect(getOrgId()).toBeNull();
    expect(queryClient.getQueryData(["workflows"])).toBeUndefined();
  });
});
