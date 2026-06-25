import type { OrgInfo } from "../types";

export interface ResolvedWorkspace {
  current: OrgInfo | null;
  needsReconcile: boolean;
  nextStoredId: string | null;
}

export function resolveWorkspaceSelection(
  organizations: OrgInfo[],
  storedId: string | null,
): ResolvedWorkspace {
  const active = organizations.filter((organization) => organization.status === "active");
  if (active.length === 0) {
    return { current: null, needsReconcile: false, nextStoredId: null };
  }

  const requestedId = storedId ?? "default";
  const requested = active.find((organization) => organization.id === requestedId) ?? null;
  if (requested) {
    return {
      current: requested,
      needsReconcile: false,
      nextStoredId: requested.id === "default" ? null : requested.id,
    };
  }

  const fallback =
    active.find((organization) => organization.id === "default") ?? active[0];
  return {
    current: fallback,
    needsReconcile: true,
    nextStoredId: fallback.id === "default" ? null : fallback.id,
  };
}
