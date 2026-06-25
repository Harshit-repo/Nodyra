import { describe, expect, it } from "vitest";

import { SUPPORTED_PYTHON_VERSIONS } from "./EnvironmentsPage";

describe("environment Python versions", () => {
  it("offers every supported version without unsupported legacy versions", () => {
    expect(SUPPORTED_PYTHON_VERSIONS).toEqual(["3.12", "3.13", "3.14"]);
    expect(SUPPORTED_PYTHON_VERSIONS).not.toContain("3.11");
  });
});
