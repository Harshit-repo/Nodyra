import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";

import { getOrgId, getUser, setOrgId } from "./api";
import { useRuntimeAuthState } from "./AuthRuntime";
import { useMyOrgs } from "./queries";
import { resolveWorkspaceSelection } from "./shell/workspace";
import type { OrgInfo, UserInfo } from "./types";

export interface WorkspaceAccessContextValue {
  user: UserInfo | null;
  authRequired: boolean;
  role: string | null;
  multiTenancyEnabled: boolean;
  loading: boolean;
  isSuccess: boolean;
  isError: boolean;
  error: unknown;
  organizations: OrgInfo[];
  current: OrgInfo | null;
  needsReconcile: boolean;
  nextStoredId: string | null;
  hasActiveWorkspace: boolean;
  refetch: () => Promise<unknown>;
}

const WorkspaceAccessContext = createContext<WorkspaceAccessContextValue | null>(null);

export function WorkspaceAccessProvider({ children }: { children: ReactNode }) {
  const auth = useRuntimeAuthState();
  const user = auth?.user ?? null;
  const multiTenancyEnabled = Boolean(auth?.multi_tenancy && auth.signed_in && user);
  const organizationsQuery = useMyOrgs({
    enabled: multiTenancyEnabled,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });
  const organizations = organizationsQuery.data ?? [];
  const resolved = resolveWorkspaceSelection(organizations, getOrgId());
  const loading = multiTenancyEnabled && organizationsQuery.isPending;
  const hasActiveWorkspace = !multiTenancyEnabled || Boolean(resolved.current);

  useEffect(() => {
    if (
      multiTenancyEnabled &&
      organizationsQuery.isSuccess &&
      !resolved.current &&
      getOrgId()
    ) {
      setOrgId(null);
    }
  }, [multiTenancyEnabled, organizationsQuery.isSuccess, resolved.current]);

  const value = useMemo<WorkspaceAccessContextValue>(() => ({
    user,
    authRequired: auth?.auth_required ?? Boolean(user),
    role: multiTenancyEnabled
      ? resolved.needsReconcile ? null : resolved.current?.role ?? null
      : user?.role ?? null,
    multiTenancyEnabled,
    loading,
    isSuccess: !multiTenancyEnabled || organizationsQuery.isSuccess,
    isError: organizationsQuery.isError,
    error: organizationsQuery.error,
    organizations,
    current: resolved.needsReconcile ? null : resolved.current,
    needsReconcile: resolved.needsReconcile,
    nextStoredId: resolved.nextStoredId,
    hasActiveWorkspace,
    refetch: async () => organizationsQuery.refetch(),
  }), [
    auth?.auth_required,
    hasActiveWorkspace,
    loading,
    multiTenancyEnabled,
    organizations,
    organizationsQuery.error,
    organizationsQuery.isError,
    organizationsQuery.isSuccess,
    organizationsQuery.refetch,
    resolved.current,
    resolved.needsReconcile,
    resolved.nextStoredId,
    user,
  ]);

  return (
    <WorkspaceAccessContext.Provider value={value}>
      {children}
    </WorkspaceAccessContext.Provider>
  );
}

export function useWorkspaceAccessContext(): WorkspaceAccessContextValue {
  const context = useContext(WorkspaceAccessContext);
  const runtimeAuth = useRuntimeAuthState();
  if (context) return context;

  const user = runtimeAuth?.user ?? getUser();
  return {
    user,
    authRequired: runtimeAuth?.auth_required ?? Boolean(user),
    role: user?.role ?? null,
    multiTenancyEnabled: runtimeAuth?.multi_tenancy ?? false,
    loading: false,
    isSuccess: true,
    isError: false,
    error: null,
    organizations: [],
    current: null,
    needsReconcile: false,
    nextStoredId: null,
    hasActiveWorkspace: true,
    refetch: async () => undefined,
  };
}
