export type BarStatusKind = "unpublished" | "published" | "draft" | "inactive";

export interface BarStatusInput {
  publishedVersion: number | null;
  active: boolean;
  hasUnpublishedChanges: boolean;
  dirty: boolean;
}

export interface BarStatus {
  kind: BarStatusKind;
  pillLabel: string;
  /** dot color class suffix, or null for no dot */
  dot: "green" | "amber" | "neutral" | null;
  /** text shown in the toolbar meta line, before the node count */
  versionLabel: string;
}

export function deriveBarStatus(input: BarStatusInput): BarStatus {
  const { publishedVersion, active, hasUnpublishedChanges, dirty } = input;
  const isPublished = publishedVersion != null && publishedVersion > 0;
  const hasDraftChanges = hasUnpublishedChanges || dirty;

  if (!isPublished) {
    return { kind: "unpublished", pillLabel: "Publish", dot: null, versionLabel: "draft" };
  }
  if (hasDraftChanges) {
    return {
      kind: "draft",
      pillLabel: "Publish changes",
      dot: "amber",
      versionLabel: `draft · based on v${publishedVersion}`,
    };
  }
  if (!active) {
    return {
      kind: "inactive",
      pillLabel: "Republish",
      dot: "neutral",
      versionLabel: `v${publishedVersion} (unpublished)`,
    };
  }
  return { kind: "published", pillLabel: "Published", dot: "green", versionLabel: `v${publishedVersion}` };
}
