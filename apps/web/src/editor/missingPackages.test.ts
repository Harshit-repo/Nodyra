import { describe, expect, it } from "vitest";
import {
  canonicalName,
  diffPackages,
  evaluateMarker,
  missingFor,
  parseRequirementsTxt,
} from "./missingPackages";

describe("canonicalName", () => {
  it("normalizes specifiers", () => {
    expect(canonicalName("scikit_learn>=1.0")).toBe("scikit-learn");
    expect(canonicalName("DuckDB[x]==1.1")).toBe("duckdb");
  });
});

describe("missingFor", () => {
  it("returns requirements absent from the env", () => {
    expect(missingFor(["duckdb>=0.9", "pandas"], ["pandas==2.1"])).toEqual([
      "duckdb>=0.9",
    ]);
  });
});

describe("parseRequirementsTxt", () => {
  it("ignores comments, blanks, and includes", () => {
    const text =
      "# comment\n\npandas==2.1\n-r other.txt\nnumpy ; python_version>'3.8'\n";
    expect(parseRequirementsTxt(text)).toEqual(["pandas==2.1", "numpy"]);
  });
});

describe("diffPackages", () => {
  it("computes add / present / removable by canonical name", () => {
    const d = diffPackages(["pandas==2.1", "numpy"], ["pandas", "duckdb"]);
    expect(d.toAdd).toEqual(["numpy"]);
    expect(d.alreadyPresent).toEqual(["pandas==2.1"]);
    expect(d.installedNotInFile).toEqual(["duckdb"]);
  });
});

describe("banner state", () => {
  it("flags a node whose requirement is absent from the env", () => {
    const manifestRequirements = ["duckdb>=0.9"];
    const envPackages = ["pandas"];
    expect(missingFor(manifestRequirements, envPackages)).toEqual(["duckdb>=0.9"]);
  });
});

describe("evaluateMarker", () => {
  it("returns true for eq match", () => {
    expect(evaluateMarker("sys_platform == 'linux'", "linux")).toBe(true);
  });

  it("returns false for eq no-match", () => {
    expect(evaluateMarker("sys_platform == 'win32'", "linux")).toBe(false);
  });

  it("returns true for ne match (different platform)", () => {
    expect(evaluateMarker("sys_platform != 'win32'", "linux")).toBe(true);
  });

  it("returns false for ne no-match (same platform)", () => {
    expect(evaluateMarker("sys_platform != 'linux'", "linux")).toBe(false);
  });

  it("returns true for unknown marker (safe fallback)", () => {
    expect(evaluateMarker("python_version >= '3.9'", "linux")).toBe(true);
  });
});

describe("missingFor with platform filtering", () => {
  it("skips requirements whose sys_platform marker does not match", () => {
    const reqs = [
      "pyzbar>=0.1.9; sys_platform!='win32'",
      "zxing-cpp>=2.2; sys_platform=='win32'",
      "pillow>=10.0",
    ];
    const missing = missingFor(reqs, [], "linux");
    expect(missing).toContain("pyzbar>=0.1.9; sys_platform!='win32'");
    expect(missing).toContain("pillow>=10.0");
    expect(missing).not.toContain("zxing-cpp>=2.2; sys_platform=='win32'");
  });

  it("shows all as missing when no platform provided (backwards compat)", () => {
    const reqs = ["pyzbar>=0.1.9; sys_platform!='win32'", "pillow>=10.0"];
    const missing = missingFor(reqs, []);
    expect(missing).toHaveLength(2);
  });
});
