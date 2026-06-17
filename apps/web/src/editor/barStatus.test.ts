import { describe, it, expect } from "vitest";
import { deriveBarStatus } from "./barStatus";

describe("deriveBarStatus", () => {
  it("never published → neutral Publish", () => {
    const s = deriveBarStatus({ publishedVersion: null, active: false, hasUnpublishedChanges: false, dirty: false });
    expect(s).toMatchObject({ kind: "unpublished", pillLabel: "Publish", dot: null, versionLabel: "draft" });
  });

  it("published, active, clean → green Published", () => {
    const s = deriveBarStatus({ publishedVersion: 2, active: true, hasUnpublishedChanges: false, dirty: false });
    expect(s).toMatchObject({ kind: "published", pillLabel: "Published", dot: "green", versionLabel: "v2" });
  });

  it("local dirty edits → amber Publish changes", () => {
    const s = deriveBarStatus({ publishedVersion: 2, active: true, hasUnpublishedChanges: false, dirty: true });
    expect(s).toMatchObject({ kind: "draft", pillLabel: "Publish changes", dot: "amber", versionLabel: "draft · based on v2" });
  });

  it("backend unpublished changes → amber Publish changes", () => {
    const s = deriveBarStatus({ publishedVersion: 5, active: true, hasUnpublishedChanges: true, dirty: false });
    expect(s).toMatchObject({ kind: "draft", dot: "amber", versionLabel: "draft · based on v5" });
  });

  it("published but inactive, clean → neutral Republish", () => {
    const s = deriveBarStatus({ publishedVersion: 3, active: false, hasUnpublishedChanges: false, dirty: false });
    expect(s).toMatchObject({ kind: "inactive", pillLabel: "Republish", dot: "neutral", versionLabel: "v3 (unpublished)" });
  });

  it("draft changes take precedence over inactive", () => {
    const s = deriveBarStatus({ publishedVersion: 3, active: false, hasUnpublishedChanges: true, dirty: false });
    expect(s.kind).toBe("draft");
  });
});
