import { describe, expect, it } from "vitest";
import {
  canonicalName,
  diffPackages,
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
