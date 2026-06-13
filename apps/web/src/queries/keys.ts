import type { AllRunsFilters } from "./index";

export const queryKeys = {
  workflows: ["workflows"] as const,
  workflow: (workflowId: string) => ["workflows", workflowId] as const,
  workflowProviderTriggers: (workflowId: string) =>
    ["workflows", workflowId, "provider-triggers"] as const,
  workflowRuns: (workflowId: string) =>
    ["workflows", workflowId, "runs"] as const,

  deployments: (workflowId?: string) =>
    ["deployments", workflowId ?? "all"] as const,
  // Root prefixes for mutation invalidation — keep key shapes owned here so
  // a registry restructure can't silently strand stale cache entries.
  deploymentsRoot: ["deployments"] as const,
  codeModulesRoot: ["code-modules"] as const,
  orgUsageRoot: (orgId: string) => ["orgs", orgId, "usage"] as const,

  credentials: ["credentials"] as const,
  credentialTypes: ["credentials", "types"] as const,

  environments: ["environments"] as const,
  runnerPools: ["runner-pools"] as const,
  runnerFleetHealth: ["runner-pools", "health"] as const,
  runnerPoolRunners: (poolId: string) =>
    ["runner-pools", poolId, "runners"] as const,
  systemSettings: ["system-settings"] as const,

  nodes: ["nodes"] as const,
  workflowCustomNodes: (workflowId: string) =>
    ["workflows", workflowId, "custom-nodes"] as const,
  pinned: (workflowId: string) => ["workflows", workflowId, "pinned"] as const,
  codeModules: (filters?: unknown) => ["code-modules", filters ?? "all"] as const,
  codeModulePreview: (moduleId: string) =>
    ["code-modules", moduleId, "preview"] as const,

  runs: ["runs"] as const,
  allRuns: (filters: AllRunsFilters) => ["runs", "all", filters] as const,
  run: (runId: string) => ["runs", runId] as const,
  runTimeline: (runId: string) => ["runs", runId, "timeline"] as const,

  runtimeMode: ["ops", "runtime-mode"] as const,
  queueStats: ["ops", "queue"] as const,

  myOrgs: ["orgs", "mine"] as const,
  orgMembers: ["orgs", "current", "members"] as const,
  orgSettings: (orgId: string) => ["orgs", orgId, "settings"] as const,
  orgUsage: (orgId: string, days: number) =>
    ["orgs", orgId, "usage", days] as const,
};
