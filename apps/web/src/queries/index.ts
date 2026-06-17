import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
  type UseQueryOptions,
} from "@tanstack/react-query";

import { api, runnerPoolsApi, type RunApprovalDecision } from "../api";
import type {
  CodeModule,
  CodeModuleFunctionPreview,
  CredentialTestResponse,
  DeploymentCreate,
  DeploymentUpdate,
  OrgMemberInfo,
  OrgSettingsInfo,
  RegistrationTokenResponse,
  RunnerInfo,
  RunnerPoolInfo,
  WorkflowGraph,
} from "../types";
import type { WorkflowPatch } from "../api";
import { queryKeys } from "./keys";

type QueryControls<TData> = Pick<
  UseQueryOptions<TData, Error, TData, QueryKey>,
  "enabled" | "refetchInterval" | "staleTime"
>;

export interface AllRunsFilters {
  workflow_id?: string;
  status?: string;
  trigger_type?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export { queryClient } from "./client";
export { queryKeys } from "./keys";

export function useWorkflows(options?: QueryControls<Awaited<ReturnType<typeof api.listWorkflows>>>) {
  return useQuery({
    queryKey: queryKeys.workflows,
    queryFn: api.listWorkflows,
    ...options,
  });
}

export function useWorkflow(
  workflowId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.getWorkflow>>>,
) {
  return useQuery({
    queryKey: workflowId ? queryKeys.workflow(workflowId) : ["workflows", "no-workflow"],
    queryFn: () => api.getWorkflow(workflowId ?? ""),
    enabled: Boolean(workflowId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function useWorkflowProviderTriggers(
  workflowId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.listWorkflowProviderTriggers>>>,
) {
  return useQuery({
    queryKey: workflowId
      ? queryKeys.workflowProviderTriggers(workflowId)
      : ["workflows", "no-workflow", "provider-triggers"],
    queryFn: () => api.listWorkflowProviderTriggers(workflowId ?? ""),
    enabled: Boolean(workflowId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function useDeployments(
  workflowId?: string,
  options?: QueryControls<Awaited<ReturnType<typeof api.listDeployments>>>,
) {
  return useQuery({
    queryKey: queryKeys.deployments(workflowId),
    queryFn: () => api.listDeployments(workflowId),
    ...options,
  });
}

export function useWorkflowVersions(
  workflowId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.listWorkflowVersions>>>,
) {
  return useQuery({
    queryKey: queryKeys.workflowVersions(workflowId ?? "none"),
    queryFn: () => api.listWorkflowVersions(workflowId ?? ""),
    enabled: Boolean(workflowId) && (options?.enabled ?? true),
    ...options,
  });
}

export function useDeploymentRuns(
  deploymentId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.listDeploymentRuns>>>,
) {
  return useQuery({
    queryKey: queryKeys.deploymentRuns(deploymentId ?? "none"),
    queryFn: () => api.listDeploymentRuns(deploymentId ?? ""),
    enabled: Boolean(deploymentId) && (options?.enabled ?? true),
    ...options,
  });
}

export function useCredentials(options?: QueryControls<Awaited<ReturnType<typeof api.listCredentials>>>) {
  return useQuery({
    queryKey: queryKeys.credentials,
    queryFn: api.listCredentials,
    ...options,
  });
}

export function useCredentialTypes(
  options?: QueryControls<Awaited<ReturnType<typeof api.listCredentialTypes>>>,
) {
  return useQuery({
    queryKey: queryKeys.credentialTypes,
    queryFn: api.listCredentialTypes,
    ...options,
  });
}

export function useEnvironments(
  options?: QueryControls<Awaited<ReturnType<typeof api.listEnvironments>>>,
) {
  return useQuery({
    queryKey: queryKeys.environments,
    queryFn: api.listEnvironments,
    ...options,
  });
}

export function useRunnerPools(
  options?: QueryControls<Awaited<ReturnType<typeof runnerPoolsApi.list>>>,
) {
  return useQuery({
    queryKey: queryKeys.runnerPools,
    queryFn: runnerPoolsApi.list,
    ...options,
  });
}

export function useRunnerFleetHealth(
  options?: QueryControls<Awaited<ReturnType<typeof runnerPoolsApi.health>>>,
) {
  return useQuery({
    queryKey: queryKeys.runnerFleetHealth,
    queryFn: runnerPoolsApi.health,
    ...options,
  });
}

export function useRunnerPoolRunners(
  poolId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof runnerPoolsApi.listRunners>>>,
) {
  return useQuery({
    queryKey: poolId ? queryKeys.runnerPoolRunners(poolId) : ["runner-pools", "no-pool", "runners"],
    queryFn: () => runnerPoolsApi.listRunners(poolId ?? ""),
    enabled: Boolean(poolId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function useSystemSettings(
  options?: QueryControls<Awaited<ReturnType<typeof api.getSystemSettings>>>,
) {
  return useQuery({
    queryKey: queryKeys.systemSettings,
    queryFn: api.getSystemSettings,
    ...options,
  });
}

export function useNodes(options?: QueryControls<Awaited<ReturnType<typeof api.nodes>>>) {
  return useQuery({
    queryKey: queryKeys.nodes,
    queryFn: api.nodes,
    ...options,
  });
}

export function useWorkflowCustomNodeManifests(
  workflowId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.workflowCustomNodeManifests>>>,
) {
  return useQuery({
    queryKey: workflowId
      ? queryKeys.workflowCustomNodes(workflowId)
      : ["workflows", "no-workflow", "custom-nodes"],
    queryFn: () => api.workflowCustomNodeManifests(workflowId ?? ""),
    enabled: Boolean(workflowId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function usePinned(
  workflowId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.listPinned>>>,
) {
  return useQuery({
    queryKey: workflowId ? queryKeys.pinned(workflowId) : ["workflows", "no-workflow", "pinned"],
    queryFn: () => api.listPinned(workflowId ?? ""),
    enabled: Boolean(workflowId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export type CodeModuleFilters = Parameters<typeof api.listCodeModules>[0];

export function useCodeModules(
  filters: CodeModuleFilters = {},
  options?: QueryControls<Awaited<ReturnType<typeof api.listCodeModules>>>,
) {
  return useQuery({
    queryKey: queryKeys.codeModules(filters),
    queryFn: () => api.listCodeModules(filters),
    ...options,
  });
}

export function useCodeModulePreview(
  moduleId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.previewCodeModule>>>,
) {
  return useQuery({
    queryKey: moduleId
      ? queryKeys.codeModulePreview(moduleId)
      : ["code-modules", "no-module", "preview"],
    queryFn: () => api.previewCodeModule(moduleId ?? ""),
    enabled: Boolean(moduleId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function useRuns(
  workflowId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.listRuns>>>,
) {
  return useQuery({
    queryKey: workflowId
      ? queryKeys.workflowRuns(workflowId)
      : ["workflows", "no-workflow", "runs"],
    queryFn: () => api.listRuns(workflowId ?? ""),
    enabled: Boolean(workflowId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function useAllRuns(
  filters: AllRunsFilters,
  options?: QueryControls<Awaited<ReturnType<typeof api.listAllRuns>>>,
) {
  return useQuery({
    queryKey: queryKeys.allRuns(filters),
    queryFn: () => api.listAllRuns(filters),
    ...options,
  });
}

export function useRuntimeMode(
  options?: QueryControls<Awaited<ReturnType<typeof api.runtimeMode>>>,
) {
  return useQuery({
    queryKey: queryKeys.runtimeMode,
    queryFn: api.runtimeMode,
    ...options,
  });
}

export function useQueueStats(
  options?: QueryControls<Awaited<ReturnType<typeof api.queueStats>>>,
) {
  return useQuery({
    queryKey: queryKeys.queueStats,
    queryFn: api.queueStats,
    ...options,
  });
}

export function useRun(
  runId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.getRun>>>,
) {
  return useQuery({
    queryKey: runId ? queryKeys.run(runId) : ["runs", "no-run"],
    queryFn: () => api.getRun(runId ?? ""),
    enabled: Boolean(runId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function useRunTimeline(
  runId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.runTimeline>>>,
) {
  return useQuery({
    queryKey: runId ? queryKeys.runTimeline(runId) : ["runs", "no-run", "timeline"],
    queryFn: () => api.runTimeline(runId ?? ""),
    enabled: Boolean(runId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function useMyOrgs(options?: QueryControls<Awaited<ReturnType<typeof api.listMyOrgs>>>) {
  return useQuery({
    queryKey: queryKeys.myOrgs,
    queryFn: api.listMyOrgs,
    ...options,
  });
}

export function useOrgMembers(
  options?: QueryControls<Awaited<ReturnType<typeof api.listOrgMembers>>>,
) {
  return useQuery({
    queryKey: queryKeys.orgMembers,
    queryFn: api.listOrgMembers,
    ...options,
  });
}

export function useOrgSettings(
  orgId: string | null,
  options?: QueryControls<Awaited<ReturnType<typeof api.getOrgSettings>>>,
) {
  return useQuery({
    queryKey: orgId ? queryKeys.orgSettings(orgId) : ["orgs", "no-org", "settings"],
    queryFn: () => api.getOrgSettings(orgId ?? ""),
    enabled: Boolean(orgId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

export function useOrgUsage(
  orgId: string | null,
  days = 14,
  options?: QueryControls<Awaited<ReturnType<typeof api.getOrgUsage>>>,
) {
  return useQuery({
    queryKey: orgId ? queryKeys.orgUsage(orgId, days) : ["orgs", "no-org", "usage", days],
    queryFn: () => api.getOrgUsage(orgId ?? "", days),
    enabled: Boolean(orgId) && (options?.enabled ?? true),
    staleTime: options?.staleTime,
    refetchInterval: options?.refetchInterval,
  });
}

function invalidateCredentials(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: queryKeys.credentials });
}

export function useCreateCredentialMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createCredential,
    onSuccess: () => invalidateCredentials(queryClient),
  });
}

export function useDeleteCredentialMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteCredential,
    onSuccess: () => invalidateCredentials(queryClient),
  });
}

export function useRefreshCredentialMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.refreshCredential,
    onSuccess: () => invalidateCredentials(queryClient),
  });
}

export function useTestCredentialMutation() {
  return useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body?: {
        workflow_id?: string | null;
        environment_id?: string | null;
        runner_pool_id?: string | null;
        context?: Record<string, unknown>;
      };
    }): Promise<CredentialTestResponse> => api.testCredential(id, body ?? {}),
  });
}

export function useCreateCodeModuleMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createCodeModule,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.codeModulesRoot });
      void queryClient.invalidateQueries({ queryKey: queryKeys.nodes });
    },
  });
}

export function useUpdateCodeModuleMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      body,
    }: Parameters<typeof api.updateCodeModule> extends [infer Id, infer Body]
      ? { id: Id; body: Body }
      : never): Promise<CodeModule> => api.updateCodeModule(id, body),
    onSuccess: (_module, vars) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.codeModulesRoot });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.codeModulePreview(String(vars.id)),
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.nodes });
    },
  });
}

export function useDeleteCodeModuleMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteCodeModule,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.codeModulesRoot });
      void queryClient.invalidateQueries({ queryKey: queryKeys.nodes });
    },
  });
}

export function usePreviewCodeModuleMutation() {
  return useMutation({
    mutationFn: api.previewCodeModule as (id: string) => Promise<CodeModuleFunctionPreview>,
  });
}

export function useCreateWorkflowMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      name,
      graph,
    }: {
      name: string;
      graph?: WorkflowGraph;
    }) => {
      const created = await api.createWorkflow(name);
      if (!graph) return created;
      return api.updateWorkflow(created.id, { graph });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.workflows });
    },
  });
}

export function useUpdateWorkflowMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: WorkflowPatch }) =>
      api.updateWorkflow(id, patch),
    onSuccess: (_workflow, vars) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.workflows });
      void queryClient.invalidateQueries({ queryKey: queryKeys.workflow(vars.id) });
    },
  });
}

export function useDeleteWorkflowMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteWorkflow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.workflows });
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });
}

export function useCreateEnvironmentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createEnvironment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.environments });
    },
  });
}

export function useUpdateEnvironmentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      body,
    }: Parameters<typeof api.updateEnvironment> extends [infer Id, infer Body]
      ? { id: Id; body: Body }
      : never) => api.updateEnvironment(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.environments });
    },
  });
}

export function useDeleteEnvironmentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteEnvironment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.environments });
    },
  });
}

export function useRebuildEnvironmentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.rebuildEnvironment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.environments });
    },
  });
}

export function useCreateDeploymentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: DeploymentCreate) => api.createDeployment(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.deploymentsRoot });
    },
  });
}

export function useRunDeploymentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.runDeployment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs });
      void queryClient.invalidateQueries({ queryKey: queryKeys.deploymentsRoot });
    },
  });
}

export function useUpdateDeploymentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DeploymentUpdate }) =>
      api.updateDeployment(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.deploymentsRoot });
    },
  });
}

export function useDeleteDeploymentMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteDeployment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.deploymentsRoot });
    },
  });
}

function invalidateRunnerPools(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: queryKeys.runnerPools });
}

export function useCreateRunnerPoolMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: runnerPoolsApi.create,
    onSuccess: () => invalidateRunnerPools(queryClient),
  });
}

export function useUpdateRunnerPoolMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      poolId,
      body,
    }: Parameters<typeof runnerPoolsApi.update> extends [infer PoolId, infer Body]
      ? { poolId: PoolId; body: Body }
      : never): Promise<RunnerPoolInfo> => runnerPoolsApi.update(poolId, body),
    onSuccess: (_pool, vars) => {
      invalidateRunnerPools(queryClient);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.runnerPoolRunners(String(vars.poolId)),
      });
    },
  });
}

export function useDeleteRunnerPoolMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: runnerPoolsApi.delete,
    onSuccess: () => invalidateRunnerPools(queryClient),
  });
}

export function useCreateRunnerRegistrationTokenMutation() {
  return useMutation({
    mutationFn: ({
      poolId,
      body,
    }: {
      poolId: string;
      body?: {
        name?: string;
        max_concurrent_runs?: number;
        capabilities?: Record<string, unknown>;
      };
    }): Promise<RegistrationTokenResponse> =>
      runnerPoolsApi.createRegistrationToken(poolId, body),
  });
}

export function useSshOnboardRunnerMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      poolId,
      body,
    }: Parameters<typeof runnerPoolsApi.sshOnboard> extends [infer PoolId, infer Body]
      ? { poolId: PoolId; body: Body }
      : never) => runnerPoolsApi.sshOnboard(poolId, body),
    onSuccess: (_result, vars) => {
      invalidateRunnerPools(queryClient);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.runnerPoolRunners(String(vars.poolId)),
      });
    },
  });
}

export function useUpdateRunnerMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      poolId,
      runnerId,
      body,
    }: Parameters<typeof runnerPoolsApi.updateRunner> extends [
      infer PoolId,
      infer RunnerId,
      infer Body,
    ]
      ? { poolId: PoolId; runnerId: RunnerId; body: Body }
      : never): Promise<RunnerInfo> =>
      runnerPoolsApi.updateRunner(poolId, runnerId, body),
    onSuccess: (_runner, vars) => {
      invalidateRunnerPools(queryClient);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.runnerPoolRunners(String(vars.poolId)),
      });
    },
  });
}

export function useDeleteRunnerMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      poolId,
      runnerId,
    }: Parameters<typeof runnerPoolsApi.deleteRunner> extends [
      infer PoolId,
      infer RunnerId,
    ]
      ? { poolId: PoolId; runnerId: RunnerId }
      : never) => runnerPoolsApi.deleteRunner(poolId, runnerId),
    onSuccess: (_result, vars) => {
      invalidateRunnerPools(queryClient);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.runnerPoolRunners(String(vars.poolId)),
      });
    },
  });
}

function invalidateRunLists(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: queryKeys.runs });
  void queryClient.invalidateQueries({ queryKey: queryKeys.workflows });
}

export function useCancelRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.cancelRun,
    onSuccess: (_result, runId) => {
      invalidateRunLists(queryClient);
      void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
    },
  });
}

export function useRerunRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.rerunRun,
    onSuccess: (result, runId) => {
      invalidateRunLists(queryClient);
      void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.run(result.run_id) });
    },
  });
}

export function useRetryRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.retryRun,
    onSuccess: (result, runId) => {
      invalidateRunLists(queryClient);
      void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.run(result.run_id) });
    },
  });
}

export function useDecideRunApprovalMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      runId,
      approvalId,
      decision,
      reason,
    }: {
      runId: string;
      approvalId: string;
      decision: RunApprovalDecision;
      reason?: string;
    }) => api.decideRunApproval(runId, approvalId, decision, reason ?? ""),
    onSuccess: (_result, vars) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.run(vars.runId) });
    },
  });
}

function invalidateOrg(queryClient: ReturnType<typeof useQueryClient>, orgId?: string): void {
  void queryClient.invalidateQueries({ queryKey: queryKeys.myOrgs });
  void queryClient.invalidateQueries({ queryKey: queryKeys.orgMembers });
  if (orgId) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.orgSettings(orgId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.orgUsageRoot(orgId) });
  }
}

export function useAddOrgMemberMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.addOrgMember,
    onSuccess: () => invalidateOrg(queryClient),
  });
}

export function useUpdateOrgMemberMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      role,
    }: {
      userId: string;
      role: string;
    }): Promise<OrgMemberInfo> => api.updateOrgMember(userId, role),
    onSuccess: () => invalidateOrg(queryClient),
  });
}

export function useRemoveOrgMemberMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.removeOrgMember,
    onSuccess: () => invalidateOrg(queryClient),
  });
}

export function useUpdateOrgSettingsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      orgId,
      body,
    }: {
      orgId: string;
      body: Record<string, number>;
    }): Promise<OrgSettingsInfo> => api.updateOrgSettings(orgId, body),
    onSuccess: (_settings, vars) => invalidateOrg(queryClient, vars.orgId),
  });
}
