import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CaretLeft,
  CaretRight,
  DotsThreeVertical,
  FileText,
  Key,
  Lightning,
  Palette,
  PencilSimple,
  PlayCircle,
  Plus,
  RocketLaunch,
  Rows,
  SquaresFour,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";
import { keepPreviousData } from "@tanstack/react-query";

import { api, errorMessage } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { Logo } from "./Logo";
import {
  useCreateFolderMutation,
  useCreateWorkflowMutation,
  useCredentials,
  useDeleteFolderMutation,
  useDeleteWorkflowMutation,
  useDeployments,
  useFolders,
  useUpdateFolderMutation,
  useUpdateWorkflowMutation,
  useWorkflowProviderTriggers,
  useWorkflows,
} from "./queries";
import { useToast } from "./ToastProvider";
import { useModalA11y } from "./useModalA11y";
import { safeGetItem, safeSetItem } from "./safeStorage";
import { useCan } from "./permissions";
import type {
  FolderInfo,
  ProviderTriggerStatusCounts,
  ProviderTriggerSubscription,
  WorkflowSummary,
} from "./types";
import { WORKFLOW_TEMPLATES as TEMPLATES } from "./workflowTemplates";

const WORKFLOWS_PER_PAGE = 18;

const FOLDER_COLORS: Array<{ value: string; label: string }> = [
  { value: "#4c9eff", label: "Blue" },
  { value: "#8b5cf6", label: "Purple" },
  { value: "#10b981", label: "Green" },
  { value: "#f59e0b", label: "Amber" },
  { value: "#ef4444", label: "Red" },
  { value: "#ec4899", label: "Pink" },
  { value: "#14b8a6", label: "Teal" },
  { value: "#f97316", label: "Orange" },
];

function paginationItems(current: number, total: number): Array<number | "ellipsis-start" | "ellipsis-end"> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = Array.from(
    new Set([1, total, current - 1, current, current + 1].filter((page) => page >= 1 && page <= total)),
  ).sort((a, b) => a - b);
  const items: Array<number | "ellipsis-start" | "ellipsis-end"> = [];
  pages.forEach((page, index) => {
    const previous = pages[index - 1];
    if (previous && page - previous > 1) {
      items.push(previous === 1 ? "ellipsis-start" : "ellipsis-end");
    }
    items.push(page);
  });
  return items;
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function providerCounts(wf: WorkflowSummary): ProviderTriggerStatusCounts {
  return (
    wf.provider_trigger_counts ?? {
      total: 0,
      active: 0,
      activating: 0,
      error: 0,
      deleted: 0,
    }
  );
}

function providerStatusBadge(
  counts: ProviderTriggerStatusCounts,
): { className: string; label: string; title: string } | null {
  if (counts.error > 0) {
    return {
      className: "hook-error",
      label: `${counts.error} hook error${counts.error === 1 ? "" : "s"}`,
      title: "Provider trigger error",
    };
  }
  if (counts.activating > 0) {
    return {
      className: "hook-activating",
      label: `${counts.activating} hook activating`,
      title: "Provider trigger activating",
    };
  }
  if (counts.active > 0) {
    return {
      className: "hook-active",
      label: `${counts.active} hook active`,
      title: "Provider trigger active",
    };
  }
  return null;
}

function lastDelivery(
  row: ProviderTriggerSubscription,
): Record<string, unknown> | null {
  const delivery = row.config?.last_delivery;
  if (!delivery || typeof delivery !== "object" || Array.isArray(delivery)) {
    return null;
  }
  return delivery as Record<string, unknown>;
}

function CreateModal({
  onClose,
  onCreated,
  initialTemplateId,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
  initialTemplateId: string;
}) {
  const initialTemplate =
    TEMPLATES.find((item) => item.id === initialTemplateId) ?? TEMPLATES[0];
  const [name, setName] = useState(
    initialTemplate.id === "blank" ? "Untitled workflow" : initialTemplate.name,
  );
  const [templateId, setTemplateId] = useState(initialTemplate.id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { notify } = useToast();
  const createWorkflow = useCreateWorkflowMutation();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const template = TEMPLATES.find((item) => item.id === templateId);
      const created = await createWorkflow.mutateAsync({
        name: name.trim() || "Untitled workflow",
        graph: template?.graph?.(),
      });
      notify(
        template?.id === "blank" ? "Workflow created." : "Template created.",
        "success",
      );
      onCreated(created.id);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-workflow-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <h2 id="new-workflow-title">New workflow</h2>
        <p className="muted">Give your automation a name. You can rename it later.</p>
        <input
          className="field-input"
          autoFocus
          aria-label="Workflow name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
        />
        <div className="template-picker">
          {TEMPLATES.map((template) => (
            <button
              type="button"
              key={template.id}
              className={templateId === template.id ? "is-selected" : ""}
              onClick={() => {
                setTemplateId(template.id);
                const previous =
                  TEMPLATES.find((item) => item.id === templateId)?.name ??
                  "Untitled workflow";
                if (name === "Untitled workflow" || name === previous) {
                  setName(
                    template.id === "blank"
                      ? "Untitled workflow"
                      : template.name,
                  );
                }
              }}
            >
              <strong>{template.name}</strong>
              <span>{template.description}</span>
            </button>
          ))}
        </div>
        {error && <p className="error-text">{error}</p>}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function WorkflowsPage() {
  const [modal, setModal] = useState(false);
  const [modalTemplateId, setModalTemplateId] = useState("blank");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sort, setSort] = useState("updated");
  const [page, setPage] = useState(1);
  const [pendingDelete, setPendingDelete] = useState<WorkflowSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [pendingRename, setPendingRename] = useState<WorkflowSummary | null>(null);
  const [renameName, setRenameName] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [folderColorPicker, setFolderColorPicker] = useState<string | null>(null);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [moveTarget, setMoveTarget] = useState<WorkflowSummary | null>(null);
  const [pendingFolderDelete, setPendingFolderDelete] = useState<FolderInfo | null>(null);
  const [pendingFolderRename, setPendingFolderRename] = useState<FolderInfo | null>(null);
  const [folderRenameName, setFolderRenameName] = useState("");
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "list">(() => {
    return safeGetItem("noodle-wf-view") === "list" ? "list" : "grid";
  });
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [providerModalWorkflow, setProviderModalWorkflow] =
    useState<WorkflowSummary | null>(null);
  const navigate = useNavigate();
  const { notify } = useToast();
  const canWrite = useCan("workflow:write");
  const foldersQuery = useFolders();
  const createFolder = useCreateFolderMutation();
  const updateFolder = useUpdateFolderMutation();
  const deleteFolder = useDeleteFolderMutation();
  const canReadCredentials = useCan("credential:read");
  const deleteWorkflow = useDeleteWorkflowMutation();
  const updateWorkflow = useUpdateWorkflowMutation();
  const duplicateWorkflow = useCreateWorkflowMutation();

  const workflowsQuery = useWorkflows({
    refetchInterval: (query) =>
      query.state.data?.some((wf) => wf.last_run_status === "running")
        ? 3000
        : false,
    placeholderData: keepPreviousData,
  });
  const deploymentsQuery = useDeployments(undefined, { placeholderData: keepPreviousData });
  const credentialsQuery = useCredentials({
    enabled: canReadCredentials,
    placeholderData: keepPreviousData,
  });
  const providerTriggersQuery = useWorkflowProviderTriggers(
    providerModalWorkflow?.id ?? null,
    { enabled: providerModalWorkflow !== null },
  );
  const workflows = workflowsQuery.data ?? null;
  const deployments = deploymentsQuery.data ?? null;
  const credentials = credentialsQuery.data ?? null;
  const error =
    workflowsQuery.isError && !workflowsQuery.data
      ? errorMessage(workflowsQuery.error)
      : "";
  const providerRows = providerTriggersQuery.data ?? null;
  const providerError =
    providerTriggersQuery.isError && !providerTriggersQuery.data
      ? errorMessage(providerTriggersQuery.error)
      : "";

  function openCreate(templateId = "blank"): void {
    setModalTemplateId(templateId);
    setModal(true);
  }

  function resetFilters(): void {
    setQuery("");
    setStatusFilter("all");
    setSort("updated");
  }

  async function remove(id: string) {
    setDeleteBusy(true);
    try {
      await deleteWorkflow.mutateAsync(id);
      notify("Workflow deleted.", "success");
      setPendingDelete(null);
    } catch (err) {
      notify(`Could not delete workflow. ${errorMessage(err)}`, "error");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function duplicate(wf: WorkflowSummary): Promise<void> {
    setOpenMenuId(null);
    try {
      const detail = await api.getWorkflow(wf.id);
      await duplicateWorkflow.mutateAsync({
        name: `Copy of ${wf.name}`,
        graph: detail.graph,
      });
      notify(`"${wf.name}" duplicated.`, "success");
    } catch {
      notify("Could not duplicate workflow.", "error");
    }
  }

  async function commitRename(): Promise<void> {
    if (!pendingRename || renameBusy) return;
    setRenameBusy(true);
    try {
      await updateWorkflow.mutateAsync({
        id: pendingRename.id,
        patch: { name: renameName.trim() || pendingRename.name },
      });
      notify("Workflow renamed.", "success");
      setPendingRename(null);
    } catch {
      notify("Could not rename workflow.", "error");
    } finally {
      setRenameBusy(false);
    }
  }

  useEffect(() => {
    if (!openMenuId) return;
    function handleOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuId(null);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [openMenuId]);

  const colorPickerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!folderColorPicker) return;
    function handleOutside(e: MouseEvent) {
      if (colorPickerRef.current && !colorPickerRef.current.contains(e.target as Node)) {
        setFolderColorPicker(null);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [folderColorPicker]);

  useEffect(() => {
    if (!canWrite) return;
    function handleKey(e: KeyboardEvent) {
      if (modal || pendingDelete || pendingRename || providerModalWorkflow) return;
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "n" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        openCreate();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [canWrite, modal, pendingDelete, pendingRename, providerModalWorkflow]);

  function setView(mode: "grid" | "list"): void {
    setViewMode(mode);
    safeSetItem("noodle-wf-view", mode);
  }

  async function openProviderStatus(
    wf: WorkflowSummary,
    event: { stopPropagation: () => void },
  ): Promise<void> {
    event.stopPropagation();
    setProviderModalWorkflow(wf);
  }

  function closeProviderStatus(): void {
    setProviderModalWorkflow(null);
  }

  // Inline dialogs in this always-mounted page: activate modal a11y only while
  // each is open (Esc, focus trap + return, dialog ARIA).
  const renameDialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(renameDialogRef, () => setPendingRename(null), {
    enabled: pendingRename !== null,
  });
  const providerDialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(providerDialogRef, closeProviderStatus, {
    enabled: providerModalWorkflow !== null,
  });

  const visible = useMemo(() => {
    const rows = workflows ?? [];
    const search = query.trim().toLowerCase();
    return rows
      .filter((wf) => {
        const counts = providerCounts(wf);
        if (selectedFolderId !== null && wf.folder_id !== selectedFolderId) return false;
        if (search && !wf.name.toLowerCase().includes(search)) return false;
        if (statusFilter === "active" && !wf.active) return false;
        if (statusFilter === "inactive" && wf.active) return false;
        if (statusFilter === "draft" && !wf.has_unpublished_changes) return false;
        if (statusFilter === "failed" && wf.last_run_status !== "error") return false;
        if (statusFilter === "running" && wf.last_run_status !== "running") return false;
        if (statusFilter === "provider_error" && counts.error === 0) return false;
        return true;
      })
      .sort((a, b) => {
        if (sort === "name") return a.name.localeCompare(b.name);
        if (sort === "last_run") {
          return (
            new Date(b.last_run_started_at ?? 0).getTime() -
            new Date(a.last_run_started_at ?? 0).getTime()
          );
        }
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      });
  }, [query, selectedFolderId, sort, statusFilter, workflows]);

  const stats = useMemo(() => {
    const rows = workflows ?? [];
    return {
      drafts: rows.filter((wf) => wf.has_unpublished_changes).length,
      failed: rows.filter((wf) => wf.last_run_status === "error").length,
      running: rows.filter((wf) => wf.last_run_status === "running").length,
      providerErrors: rows.filter((wf) => providerCounts(wf).error > 0).length,
    };
  }, [workflows]);

  const ops = useMemo(() => {
    const activeDeployments = deployments?.filter((item) => item.active).length ?? 0;
    const credentialsNeedingAttention =
      credentials?.filter((cred) => cred.keys.length === 0).length ?? 0;
    return {
      activeDeployments,
      credentialsNeedingAttention,
    };
  }, [credentials, deployments]);

  const pageCount = Math.max(1, Math.ceil(visible.length / WORKFLOWS_PER_PAGE));
  const pageStart = (page - 1) * WORKFLOWS_PER_PAGE;
  const pagedVisible = visible.slice(pageStart, pageStart + WORKFLOWS_PER_PAGE);

  useEffect(() => {
    setPage(1);
  }, [query, selectedFolderId, sort, statusFilter]);

  useEffect(() => {
    setPage((current) => Math.min(current, pageCount));
  }, [pageCount]);

  function toggleStatusFilter(next: string): void {
    setStatusFilter((current) => current === next ? "all" : next);
  }

  async function submitNewFolder(): Promise<void> {
    const name = newFolderName.trim();
    if (!name) return;
    try {
      await createFolder.mutateAsync(name);
      setNewFolderName("");
      setShowNewFolder(false);
    } catch {
      notify("Could not create folder.", "error");
    }
  }

  async function commitFolderRename(): Promise<void> {
    if (!pendingFolderRename) return;
    try {
      await updateFolder.mutateAsync({ id: pendingFolderRename.id, name: folderRenameName.trim() || pendingFolderRename.name });
      setPendingFolderRename(null);
      notify("Folder renamed.", "success");
    } catch {
      notify("Could not rename folder.", "error");
    }
  }

  async function confirmFolderDelete(): Promise<void> {
    if (!pendingFolderDelete) return;
    try {
      await deleteFolder.mutateAsync(pendingFolderDelete.id);
      if (selectedFolderId === pendingFolderDelete.id) setSelectedFolderId(null);
      setPendingFolderDelete(null);
    } catch {
      notify("Could not delete folder.", "error");
    }
  }

  async function moveToFolder(wf: WorkflowSummary, folderId: string | null): Promise<void> {
    setMoveTarget(null);
    try {
      await updateWorkflow.mutateAsync({ id: wf.id, patch: { folder_id: folderId } });
    } catch {
      notify("Could not move workflow.", "error");
    }
  }

  const moveDialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(moveDialogRef, () => setMoveTarget(null), { enabled: moveTarget !== null });
  const folderRenameDialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(folderRenameDialogRef, () => setPendingFolderRename(null), { enabled: pendingFolderRename !== null });

  const folders = foldersQuery.data ?? [];

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Workflows
            {workflows && <span className="home-count">{workflows.length}</span>}
          </h1>
          {canWrite && (
            <button className="btn btn-primary home-create-button" title="New workflow (N)" onClick={() => openCreate()}>
              <Plus size={17} weight="bold" aria-hidden="true" />
              New workflow
            </button>
          )}
        </div>

        {(folders.length > 0 || canWrite) && (
          <div className="wf-folders" role="navigation" aria-label="Workflow folders">
            <button
              type="button"
              className={`wf-folder-chip${selectedFolderId === null ? " is-selected" : ""}`}
              onClick={() => setSelectedFolderId(null)}
            >
              All
              {workflows && <span className="wf-folder-count">{workflows.length}</span>}
            </button>
            {folders.map((folder) => (
              <div
                key={folder.id}
                className="wf-folder-chip-wrap"
                style={folder.color ? { "--folder-chip-color": folder.color } as CSSProperties : undefined}
              >
                <button
                  type="button"
                  className={`wf-folder-chip${selectedFolderId === folder.id ? " is-selected" : ""}${folder.color ? " has-color" : ""}`}
                  onClick={() => setSelectedFolderId(folder.id === selectedFolderId ? null : folder.id)}
                >
                  {folder.name}
                  <span className="wf-folder-count">{folder.workflow_count}</span>
                </button>
                {canWrite && (
                  <div className="wf-folder-actions">
                    <button
                      type="button"
                      className="wf-folder-action-btn"
                      title="Rename folder"
                      onClick={(e) => { e.stopPropagation(); setPendingFolderRename(folder); setFolderRenameName(folder.name); }}
                    >
                      <PencilSimple size={11} weight="bold" />
                    </button>
                    <button
                      type="button"
                      className="wf-folder-action-btn is-danger"
                      title="Delete folder"
                      onClick={(e) => { e.stopPropagation(); setPendingFolderDelete(folder); }}
                    >
                      <Trash size={11} weight="bold" />
                    </button>
                    <div className="wf-folder-color-wrap" ref={folderColorPicker === folder.id ? colorPickerRef : null}>
                      <button
                        type="button"
                        className="wf-folder-action-btn"
                        title="Set folder color"
                        onClick={(e) => { e.stopPropagation(); setFolderColorPicker(folderColorPicker === folder.id ? null : folder.id); }}
                      >
                        <Palette size={11} weight="bold" />
                      </button>
                      {folderColorPicker === folder.id && (
                        <div className="wf-folder-color-picker">
                          <button
                            type="button"
                            className="wf-folder-color-swatch is-none"
                            title="No color"
                            onClick={() => { setFolderColorPicker(null); void updateFolder.mutateAsync({ id: folder.id, color: null }); }}
                          />
                          {FOLDER_COLORS.map(({ value, label }) => (
                            <button
                              key={value}
                              type="button"
                              className={`wf-folder-color-swatch${folder.color === value ? " is-selected" : ""}`}
                              style={{ "--swatch-color": value } as CSSProperties}
                              aria-label={label}
                              title={label}
                              onClick={() => { setFolderColorPicker(null); void updateFolder.mutateAsync({ id: folder.id, color: value }); }}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
            {canWrite && (
              showNewFolder ? (
                <div className="wf-folder-new">
                  <input
                    className="wf-folder-new-input"
                    autoFocus
                    placeholder="Folder name"
                    value={newFolderName}
                    onChange={(e) => setNewFolderName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void submitNewFolder();
                      if (e.key === "Escape") { setShowNewFolder(false); setNewFolderName(""); }
                    }}
                  />
                  <button type="button" className="btn btn-primary wf-folder-new-confirm" onClick={() => void submitNewFolder()} disabled={createFolder.isPending}>
                    Add
                  </button>
                  <button type="button" className="btn btn-ghost" onClick={() => { setShowNewFolder(false); setNewFolderName(""); }}>
                    Cancel
                  </button>
                </div>
              ) : (
                <button type="button" className="wf-folder-add-btn" onClick={() => setShowNewFolder(true)}>
                  + New folder
                </button>
              )
            )}
          </div>
        )}

        {workflows && workflows.length > 0 && (
          <div className={`dashboard-grid${canReadCredentials ? "" : " dashboard-grid--five"}`} aria-label="Operational summary">
            <button
              type="button"
              className={`is-draft${statusFilter === "draft" ? " is-selected" : ""}`}
              aria-pressed={statusFilter === "draft"}
              onClick={() => toggleStatusFilter("draft")}
            >
              <span className="dashboard-stat-head"><span className="dashboard-stat-icon"><FileText size={18} aria-hidden="true" /></span><strong>{stats.drafts}</strong></span>
              <span className="dashboard-stat-label">Drafts</span>
              <span className="dashboard-stat-description">Workflows in draft</span>
            </button>
            <button
              type="button"
              className={`is-failed${statusFilter === "failed" ? " is-selected" : ""}`}
              aria-pressed={statusFilter === "failed"}
              onClick={() => toggleStatusFilter("failed")}
            >
              <span className="dashboard-stat-head"><span className="dashboard-stat-icon"><WarningCircle size={18} aria-hidden="true" /></span><strong>{stats.failed}</strong></span>
              <span className="dashboard-stat-label">Latest run failed</span>
              <span className="dashboard-stat-description">Latest run status</span>
            </button>
            <button
              type="button"
              className={`is-running${statusFilter === "running" ? " is-selected" : ""}`}
              aria-pressed={statusFilter === "running"}
              onClick={() => toggleStatusFilter("running")}
            >
              <span className="dashboard-stat-head"><span className="dashboard-stat-icon"><PlayCircle size={18} aria-hidden="true" /></span><strong>{stats.running}</strong></span>
              <span className="dashboard-stat-label">Workflows running</span>
              <span className="dashboard-stat-description">Latest run is running</span>
            </button>
            <button
              type="button"
              className={`is-trigger${statusFilter === "provider_error" ? " is-selected" : ""}`}
              aria-pressed={statusFilter === "provider_error"}
              onClick={() => toggleStatusFilter("provider_error")}
            >
              <span className="dashboard-stat-head"><span className="dashboard-stat-icon"><Lightning size={18} aria-hidden="true" /></span><strong>{stats.providerErrors}</strong></span>
              <span className="dashboard-stat-label">Trigger issues</span>
              <span className="dashboard-stat-description">Affected workflows</span>
            </button>
            <button type="button" className="is-deployment" onClick={() => navigate("/deployments")}>
              <span className="dashboard-stat-head"><span className="dashboard-stat-icon"><RocketLaunch size={18} aria-hidden="true" /></span><strong>{ops.activeDeployments}</strong></span>
              <span className="dashboard-stat-label">Active deployments</span>
              <span className="dashboard-stat-description">Across all workflows</span>
            </button>
            {canReadCredentials && (
              <button type="button" className="is-credential" onClick={() => navigate("/credentials")}>
                <span className="dashboard-stat-head"><span className="dashboard-stat-icon"><Key size={18} aria-hidden="true" /></span><strong>{ops.credentialsNeedingAttention}</strong></span>
                <span className="dashboard-stat-label">Credential attention</span>
                <span className="dashboard-stat-description">Requires action</span>
              </button>
            )}
          </div>
        )}

        <div className="home-filters">
          <div className="home-search-row">
            <input
              className="field-input"
              aria-label="Search workflows"
              placeholder="Search workflows..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <div className="wf-view-toggle" role="group" aria-label="View mode">
              <button
                type="button"
                title="Card view"
                className={viewMode === "grid" ? "active" : ""}
                onClick={() => setView("grid")}
              >
                <SquaresFour size={13} weight={viewMode === "grid" ? "fill" : "regular"} />
              </button>
              <button
                type="button"
                title="List view"
                className={viewMode === "list" ? "active" : ""}
                onClick={() => setView("list")}
              >
                <Rows size={13} weight={viewMode === "list" ? "fill" : "regular"} />
              </button>
            </div>
          </div>
          <select
            className="field-input"
            aria-label="Filter workflows by status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
            <option value="draft">Draft changes</option>
            <option value="failed">Latest run failed</option>
            <option value="running">Running</option>
            <option value="provider_error">Trigger errors</option>
          </select>
          <select
            className="field-input"
            aria-label="Sort workflows"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="updated">Updated</option>
            <option value="last_run">Last run</option>
            <option value="name">Name</option>
          </select>
        </div>

        {error && (
          <div className="wf-load-error" role="alert">
            <WarningCircle size={22} aria-hidden="true" />
            <div><strong>Workflows could not be loaded</strong><p>{error}</p></div>
            <button className="btn" type="button" onClick={() => void workflowsQuery.refetch()}>Retry</button>
          </div>
        )}

        {!workflows && !error && (
          <div className="wf-grid" aria-label="Loading workflows">
            {Array.from({ length: 6 }).map((_, index) => (
              <div className="wf-card skeleton-card" key={index}>
                <span className="skeleton-line short" />
                <span className="skeleton-line title" />
                <span className="skeleton-line" />
                <span className="skeleton-line tiny" />
              </div>
            ))}
          </div>
        )}

        {workflows && workflows.length === 0 && (
          <div className="empty-state">
            <Logo size={44} />
            <h2>No workflows yet</h2>
            <p className="muted">
              {canWrite ? "Create your first automation and start wiring Python nodes together." : "There are no workflows in this workspace yet."}
            </p>
            {canWrite ? (
              <>
                <button className="btn btn-primary" onClick={() => openCreate()}>
                  New workflow
                </button>
                <div className="template-strip">
                  {TEMPLATES.filter((template) => template.id !== "blank").map((template) => (
                    <button
                      key={template.id}
                      type="button"
                      onClick={() => {
                        openCreate(template.id);
                        setQuery("");
                      }}
                    >
                      {template.name}
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <p className="muted">You have read-only access. Ask a workspace editor to create the first workflow.</p>
            )}
          </div>
        )}

        {workflows && workflows.length > 0 && (
          <div className={viewMode === "list" ? "wf-list" : "wf-grid"}>
            {pagedVisible.map((wf) => {
              const hookBadge = providerStatusBadge(providerCounts(wf));
              const menuOpen = openMenuId === wf.id;
              const primaryStatus = wf.last_run_status === "error"
                ? { className: "run-error", label: "error" }
                : wf.active
                  ? { className: "on", label: "active" }
                  : wf.has_unpublished_changes
                    ? { className: "draft", label: "draft" }
                    : { className: "off", label: "inactive" };
              return (
                <article
                  key={wf.id}
                  className={`wf-card${viewMode === "list" ? " wf-card--row" : ""}`}
                >
                  <button
                    type="button"
                    className="wf-card-open"
                    aria-label={`Open workflow: ${wf.name}`}
                    onClick={() => navigate(`/workflows/${wf.id}`)}
                  />
                  <div className="wf-card-top">
                    <span className={`wf-status ${primaryStatus.className}`}>
                      {primaryStatus.label}
                    </span>
                    {hookBadge && (
                      <button
                        type="button"
                        className={`wf-status wf-trigger-status ${hookBadge.className}`}
                        title={hookBadge.title}
                        onClick={(event) => void openProviderStatus(wf, event)}
                      >
                        {hookBadge.label}
                      </button>
                    )}
                  </div>
                  <h3 className="wf-name" title={wf.name}>{wf.name}</h3>
                  <div className="wf-meta">
                    <span>
                      {wf.node_count} node{wf.node_count === 1 ? "" : "s"}
                    </span>
                    <span className="dot-sep" />
                    <span>v{wf.version}</span>
                    <span className="dot-sep" />
                    <span>published v{wf.published_version}</span>
                    <span className="dot-sep" />
                    <span title={new Date(wf.updated_at).toLocaleString()}>{relativeTime(wf.updated_at)}</span>
                    {wf.has_unpublished_changes && (
                      <>
                        <span className="dot-sep" />
                        <span className="wf-meta-draft">draft</span>
                      </>
                    )}
                    {wf.last_run_status && (
                      <>
                        <span className="dot-sep" />
                        <span className={`wf-meta-run wf-meta-run-${wf.last_run_status}`}>
                          {wf.last_run_status}
                        </span>
                      </>
                    )}
                  </div>
                  {wf.last_run_started_at && (
                    <div className="wf-meta wf-run-meta" title={new Date(wf.last_run_started_at).toLocaleString()}>
                      Last run {relativeTime(wf.last_run_started_at)}
                    </div>
                  )}
                  {canWrite && <div
                    className="wf-menu-wrap"
                    ref={menuOpen ? menuRef : null}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      type="button"
                      className="wf-menu-btn"
                      title="More options"
                      aria-label="More options"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpenMenuId(menuOpen ? null : wf.id);
                      }}
                    >
                      <DotsThreeVertical size={16} weight="bold" />
                    </button>
                    {menuOpen && (
                      <div className="wf-menu" role="menu">
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => navigate(`/workflows/${wf.id}`)}
                        >
                          Open
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setOpenMenuId(null);
                            setPendingRename(wf);
                            setRenameName(wf.name);
                          }}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => void duplicate(wf)}
                        >
                          Duplicate
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => { setOpenMenuId(null); setMoveTarget(wf); }}
                        >
                          Move to folder
                        </button>
                        <div className="wf-menu-sep" />
                        <button
                          type="button"
                          role="menuitem"
                          className="danger"
                          onClick={() => {
                            setOpenMenuId(null);
                            setPendingDelete(wf);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    )}
                  </div>}
                </article>
              );
            })}
          </div>
        )}
        {workflows && workflows.length > 0 && visible.length > 0 && (
          <nav className="wf-pagination" aria-label="Workflow pages">
            <p>
              Showing {pageStart + 1}–{Math.min(pageStart + WORKFLOWS_PER_PAGE, visible.length)} of {visible.length} workflows
            </p>
            <div className="wf-pagination-controls">
              <button
                type="button"
                aria-label="Previous page"
                disabled={page === 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                <CaretLeft size={16} aria-hidden="true" />
              </button>
              {paginationItems(page, pageCount).map((item) => (
                typeof item === "number" ? (
                  <button
                    type="button"
                    className={item === page ? "is-current" : ""}
                    aria-label={`Page ${item}`}
                    aria-current={item === page ? "page" : undefined}
                    onClick={() => setPage(item)}
                    key={item}
                  >
                    {item}
                  </button>
                ) : (
                  <span className="wf-pagination-ellipsis" aria-hidden="true" key={item}>…</span>
                )
              ))}
              <button
                type="button"
                aria-label="Next page"
                disabled={page === pageCount}
                onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
              >
                <CaretRight size={16} aria-hidden="true" />
              </button>
            </div>
          </nav>
        )}
        {workflows && workflows.length > 0 && visible.length === 0 && (
          <div className="empty-state">
            <h2>No matches</h2>
            <p className="muted">Adjust search or filters.</p>
            <button className="btn" type="button" onClick={resetFilters}>
              Reset filters
            </button>
          </div>
        )}
      </main>

      {modal && canWrite && (
        <CreateModal
          onClose={() => setModal(false)}
          onCreated={(id) => navigate(`/workflows/${id}`)}
          initialTemplateId={modalTemplateId}
        />
      )}
      {pendingDelete && (
        <ConfirmDialog
          title="Delete workflow"
          body={`Delete "${pendingDelete.name}"? This removes the workflow and cannot be undone.`}
          busy={deleteBusy}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void remove(pendingDelete.id)}
        />
      )}
      {pendingRename && (
        <div className="modal-overlay" onClick={() => setPendingRename(null)}>
          <div
            className="modal"
            ref={renameDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="rename-workflow-title"
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="rename-workflow-title">Rename workflow</h2>
            <input
              className="field-input"
              autoFocus
              aria-label="New workflow name"
              value={renameName}
              onChange={(e) => setRenameName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void commitRename()}
            />
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setPendingRename(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={() => void commitRename()}
                disabled={renameBusy}
              >
                {renameBusy ? "Saving…" : "Rename"}
              </button>
            </div>
          </div>
        </div>
      )}
      {providerModalWorkflow && (
        <div className="modal-overlay" onClick={closeProviderStatus}>
          <div
            className="modal provider-trigger-modal"
            ref={providerDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="provider-trigger-title"
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="provider-trigger-title">Provider triggers</h2>
            <p className="muted">{providerModalWorkflow.name}</p>
            {providerError && <p className="error-text">{providerError}</p>}
            {!providerRows && !providerError && <p className="muted">Loading…</p>}
            {providerRows && providerRows.length === 0 && (
              <p className="muted">No provider trigger subscriptions.</p>
            )}
            {providerRows && providerRows.length > 0 && (
              <div className="provider-trigger-list">
                {providerRows.map((row) => {
                  const delivery = lastDelivery(row);
                  return (
                    <div className="provider-trigger-row" key={row.id}>
                      <div className="provider-trigger-head">
                        <strong>
                          {row.provider} · {row.trigger_key}
                        </strong>
                        <span className={`run-pill status-run-${row.status}`}>
                          {row.status}
                        </span>
                      </div>
                      <div className="provider-trigger-meta">
                        <span>{row.node_id}</span>
                        <span>
                          version {row.workflow_version_id?.slice(0, 8) ?? "draft"}
                        </span>
                        <span>
                          last event{" "}
                          {row.last_event_at
                            ? relativeTime(row.last_event_at)
                            : "never"}
                        </span>
                      </div>
                      {delivery && (
                        <div className="provider-trigger-meta">
                          {typeof delivery.response_status === "number" && (
                            <span>HTTP {delivery.response_status}</span>
                          )}
                          {typeof delivery.latency_ms === "number" && (
                            <span>{delivery.latency_ms}ms</span>
                          )}
                          {typeof delivery.event === "string" && (
                            <span>{delivery.event}</span>
                          )}
                          {typeof delivery.repository === "string" && (
                            <span>{delivery.repository}</span>
                          )}
                          {delivery.duplicate === true && <span>duplicate</span>}
                        </div>
                      )}
                      {row.callback_url && <code>{row.callback_url}</code>}
                      {row.error && <p className="error-text">{row.error}</p>}
                    </div>
                  );
                })}
              </div>
            )}
            <div className="modal-actions">
              <button className="btn" type="button" onClick={closeProviderStatus}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
      {moveTarget && (
        <div className="modal-overlay" onClick={() => setMoveTarget(null)}>
          <div
            className="modal"
            ref={moveDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="move-folder-title"
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="move-folder-title">Move to folder</h2>
            <p className="muted">Choose a folder for <strong>{moveTarget.name}</strong>.</p>
            <div className="wf-folder-picker">
              <button
                type="button"
                className={`wf-folder-pick-btn${!moveTarget.folder_id ? " is-selected" : ""}`}
                onClick={() => void moveToFolder(moveTarget, null)}
              >
                No folder
              </button>
              {folders.map((folder) => (
                <button
                  key={folder.id}
                  type="button"
                  className={`wf-folder-pick-btn${moveTarget.folder_id === folder.id ? " is-selected" : ""}`}
                  onClick={() => void moveToFolder(moveTarget, folder.id)}
                >
                  {folder.name}
                </button>
              ))}
            </div>
            {folders.length === 0 && (
              <p className="muted">No folders yet. Create one from the workflows page.</p>
            )}
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setMoveTarget(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
      {pendingFolderDelete && (
        <ConfirmDialog
          title="Delete folder"
          body={`Delete "${pendingFolderDelete.name}"? Workflows in this folder will be moved to All.`}
          onCancel={() => setPendingFolderDelete(null)}
          onConfirm={() => void confirmFolderDelete()}
        />
      )}
      {pendingFolderRename && (
        <div className="modal-overlay" onClick={() => setPendingFolderRename(null)}>
          <div
            className="modal"
            ref={folderRenameDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="folder-rename-title"
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="folder-rename-title">Rename folder</h2>
            <input
              className="field-input"
              autoFocus
              aria-label="Folder name"
              value={folderRenameName}
              onChange={(e) => setFolderRenameName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void commitFolderRename()}
            />
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setPendingFolderRename(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={() => void commitFolderRename()}
                disabled={updateFolder.isPending}
              >
                {updateFolder.isPending ? "Saving…" : "Rename"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
