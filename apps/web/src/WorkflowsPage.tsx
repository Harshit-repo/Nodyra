import {
  type CSSProperties,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  CaretLeft,
  CaretRight,
  Check,
  DotsThreeVertical,
  FileText,
  Key,
  Lightning,
  MagnifyingGlass,
  Palette,
  PencilSimple,
  PlayCircle,
  Plus,
  RocketLaunch,
  Rows,
  SquaresFour,
  Trash,
  UploadSimple,
  WarningCircle,
} from "@phosphor-icons/react";
import { keepPreviousData } from "@tanstack/react-query";

import { api, userFriendlyError } from "./api";
import { recordActivationEvent } from "./activation";
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
  useInstantiateTemplateMutation,
  useTemplates,
  useUpdateFolderMutation,
  useUpdateWorkflowMutation,
  useWorkflowProviderTriggers,
  useWorkflows,
} from "./queries";
import { useToast } from "./ToastProvider";
import { useModalA11y } from "./useModalA11y";
import { safeGetItem, safeSetItem } from "./safeStorage";
import { useCan } from "./permissions";
import { productState } from "./productStates";
import { WorkflowImportModal } from "./WorkflowImportModal";
import {
  WORKFLOW_TEMPLATES,
  WORKFLOW_TEMPLATE_TRUST,
  type WorkflowTemplate,
} from "./workflowTemplates";
import type {
  FolderInfo,
  ProviderTriggerStatusCounts,
  ProviderTriggerSubscription,
  WorkflowTemplateSummary,
  WorkflowSummary,
} from "./types";

const WORKFLOWS_PER_PAGE = 18;
const BLANK_TEMPLATE: WorkflowTemplateSummary = {
  id: "blank",
  name: "Blank workflow",
  description: "Start with an empty canvas.",
  tags: ["blank"],
  version: "1.0.0",
  creator: "Nodyra",
  verified: true,
  credential_free: true,
  prerequisites: [],
  expected_result: "An empty workflow draft ready for authoring.",
  permissions: [],
  compatibility: ">=0.1.0,<0.2.0",
  rating: null,
  rating_count: 0,
  screenshot_url: null,
};

type GalleryTemplate = WorkflowTemplate & { graph: NonNullable<WorkflowTemplate["graph"]> };

function hasGalleryGraph(template: WorkflowTemplate): template is GalleryTemplate {
  return template.id !== "blank" && typeof template.graph === "function";
}

const TEMPLATE_GALLERY = WORKFLOW_TEMPLATES.filter(hasGalleryGraph)
  .filter((template) => WORKFLOW_TEMPLATE_TRUST[template.id]?.credential_free)
  .sort((left, right) => {
    const activationOrder = ["datasetref-filter-export", "sample-csv-artifact", "sample-data-quality"];
    const leftIndex = activationOrder.indexOf(left.id);
    const rightIndex = activationOrder.indexOf(right.id);
    if (leftIndex === -1 && rightIndex === -1) return left.name.localeCompare(right.name);
    if (leftIndex === -1) return 1;
    if (rightIndex === -1) return -1;
    return leftIndex - rightIndex;
  })
  .slice(0, 8);

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
  const templatesQuery = useTemplates();
  const templates = templatesQuery.data ?? [];
  const [templateId, setTemplateId] = useState(initialTemplateId);
  // Show a first screenful rather than the whole catalogue: sixteen templates
  // made the dialog taller than a laptop viewport, which pushed Create below
  // the fold. Expanded automatically when arriving with a template preselected,
  // so a deep link to one further down the list still shows it as chosen.
  const [showAllTemplates, setShowAllTemplates] = useState(
    initialTemplateId !== "blank",
  );
  const allTemplates = [BLANK_TEMPLATE, ...templates];
  const TEMPLATE_PREVIEW_COUNT = 10;
  const visibleTemplates = showAllTemplates
    ? allTemplates
    : allTemplates.slice(0, TEMPLATE_PREVIEW_COUNT);
  const hiddenTemplateCount = allTemplates.length - visibleTemplates.length;
  const [name, setName] = useState(
    initialTemplateId === "blank" ? "Untitled workflow" : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { notify } = useToast();
  const createWorkflow = useCreateWorkflowMutation();
  const instantiateTemplate = useInstantiateTemplateMutation();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);
  const selectedTemplate =
    templateId === "blank"
      ? BLANK_TEMPLATE
      : templates.find((template) => template.id === templateId);

  useEffect(() => {
    if (templateId === "blank") {
      setName((current) => current || "Untitled workflow");
      return;
    }
    const template = templates.find((item) => item.id === templateId);
    if (!template) return;
    setName((current) =>
      !current || current === "Untitled workflow" ? template.name : current,
    );
  }, [templateId, templates]);

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const workflowName =
        name.trim() || selectedTemplate?.name || "Untitled workflow";
      const created =
        templateId === "blank"
          ? await createWorkflow.mutateAsync({ name: workflowName })
          : await instantiateTemplate.mutateAsync({
              id: templateId,
              name: workflowName,
            });
      if (templateId !== "blank") recordActivationEvent("template_selected");
      notify(templateId === "blank" ? "Workflow created." : "Template created.", "success");
      onCreated(created.id);
    } catch (err) {
      setError(userFriendlyError(err));
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
          {visibleTemplates.map((template) => (
            <button
              type="button"
              key={template.id}
              className={templateId === template.id ? "is-selected" : ""}
              onClick={() => {
                const previousName = selectedTemplate?.name ?? "Untitled workflow";
                setTemplateId(template.id);
                if (!name || name === "Untitled workflow" || name === previousName) {
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
          {hiddenTemplateCount > 0 ? (
            <button
              type="button"
              className="template-picker-more"
              onClick={() => setShowAllTemplates(true)}
            >
              <strong>Show {hiddenTemplateCount} more templates</strong>
              <span>Browse the full catalogue.</span>
            </button>
          ) : null}
        </div>
        {selectedTemplate && "version" in selectedTemplate && (
          <section className="template-selection-details" aria-label="Selected template details">
            {selectedTemplate.screenshot_url && (
              <img src={selectedTemplate.screenshot_url} alt="" loading="lazy" />
            )}
            <div>
              <p>
                <strong>v{selectedTemplate.version}</strong>
                {selectedTemplate.verified && <span className="badge badge--success">Verified</span>}
                {selectedTemplate.credential_free && <span className="badge">Credential-free</span>}
              </p>
              <small>By {selectedTemplate.creator} · Compatible {selectedTemplate.compatibility}</small>
              <span>{selectedTemplate.expected_result}</span>
            </div>
          </section>
        )}
        {templatesQuery.isError && (
          <p className="error-text">
            Could not load templates. Blank workflow is still available.
          </p>
        )}
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
  const requestedStarterId = new URLSearchParams(window.location.search).get("starter");
  const [modal, setModal] = useState(false);
  const [importModal, setImportModal] = useState(false);
  const [modalTemplateId, setModalTemplateId] = useState("blank");
  const [query, setQuery] = useState("");
  const [searchInput, setSearchInput] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(searchInput), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);
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
    return safeGetItem("nodyra-wf-view") === "list" ? "list" : "grid";
  });
  const [starterBusy, setStarterBusy] = useState<string | null>(null);
  const requestedStarterHandledRef = useRef(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [pendingBulkDelete, setPendingBulkDelete] = useState(false);
  const [bulkDeleteBusy, setBulkDeleteBusy] = useState(false);
  const [bulkMoveOpen, setBulkMoveOpen] = useState(false);
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
  const createWorkflowFromStarter = useCreateWorkflowMutation();
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
      ? userFriendlyError(workflowsQuery.error)
      : "";
  const providerRows = providerTriggersQuery.data ?? null;
  const providerError =
    providerTriggersQuery.isError && !providerTriggersQuery.data
      ? userFriendlyError(providerTriggersQuery.error)
      : "";

  function openCreate(templateId = "blank"): void {
    setModalTemplateId(templateId);
    setModal(true);
  }

  const createFromTemplate = useCallback(async (template: GalleryTemplate): Promise<void> => {
    if (starterBusy) return;
    setStarterBusy(template.id);
    try {
      const created = await createWorkflowFromStarter.mutateAsync({
        name: template.name,
        graph: template.graph(),
      });
      recordActivationEvent("template_selected");
      notify(`"${template.name}" created.`, "success");
      navigate(`/workflows/${created.id}`);
    } catch (err) {
      notify(`Could not create workflow. ${userFriendlyError(err)}`, "error");
    } finally {
      setStarterBusy(null);
    }
  }, [createWorkflowFromStarter, navigate, notify, starterBusy]);

  useEffect(() => {
    if (!requestedStarterId || !canWrite || requestedStarterHandledRef.current) return;

    requestedStarterHandledRef.current = true;
    const cleanUrl = new URL(window.location.href);
    cleanUrl.searchParams.delete("starter");
    window.history.replaceState({}, "", `${cleanUrl.pathname}${cleanUrl.search}${cleanUrl.hash}`);

    const template = WORKFLOW_TEMPLATES.find(
      (candidate): candidate is GalleryTemplate =>
        candidate.id === requestedStarterId && hasGalleryGraph(candidate),
    );
    if (!template) {
      notify("That starter template is not available in this version.", "error");
      return;
    }
    void createFromTemplate(template);
  }, [canWrite, createFromTemplate, notify, requestedStarterId]);

  async function createWithAi(): Promise<void> {
    if (starterBusy) return;
    setStarterBusy("ai");
    try {
      const created = await createWorkflowFromStarter.mutateAsync({
        name: "AI workflow draft",
      });
      navigate(`/workflows/${created.id}?ai=1`);
    } catch (err) {
      notify(`Could not create AI draft workspace. ${userFriendlyError(err)}`, "error");
    } finally {
      setStarterBusy(null);
    }
  }

  function resetFilters(): void {
    setSearchInput("");
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
      notify(`Could not delete workflow. ${userFriendlyError(err)}`, "error");
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
    safeSetItem("nodyra-wf-view", mode);
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

  function toggleSelect(id: string, e: React.MouseEvent): void {
    e.stopPropagation();
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function executeBulkDelete(): Promise<void> {
    setBulkDeleteBusy(true);
    try {
      for (const id of selectedIds) {
        await deleteWorkflow.mutateAsync(id);
      }
      notify(`Deleted ${selectedIds.size} workflow${selectedIds.size === 1 ? "" : "s"}.`, "success");
      setPendingBulkDelete(false);
      setSelectedIds(new Set());
    } catch (err) {
      notify(`Could not delete workflows. ${userFriendlyError(err)}`, "error");
    } finally {
      setBulkDeleteBusy(false);
    }
  }

  async function bulkMoveToFolder(folderId: string | null): Promise<void> {
    try {
      for (const id of selectedIds) {
        await updateWorkflow.mutateAsync({ id, patch: { folder_id: folderId } });
      }
      notify(`Moved ${selectedIds.size} workflow${selectedIds.size === 1 ? "" : "s"}.`, "success");
      setBulkMoveOpen(false);
      setSelectedIds(new Set());
    } catch {
      notify("Could not move workflows.", "error");
    }
  }

  function bulkExport(): void {
    const selected = visible.filter((wf) => selectedIds.has(wf.id));
    const json = JSON.stringify(selected, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `nodyra-workflows-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
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
        if (sort === "created") {
          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
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
    setSelectedIds(new Set());
    setBulkMoveOpen(false);
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
            <div className="home-bar-actions">
              <button className="btn" type="button" onClick={() => setImportModal(true)}>
                <UploadSimple size={17} aria-hidden="true" /> Import
              </button>
              <button className="btn btn-primary home-create-button" title="New workflow (N)" onClick={() => openCreate()}>
                <Plus size={17} weight="bold" aria-hidden="true" />
                New workflow
              </button>
            </div>
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
                      aria-label={`Rename ${folder.name}`}
                      onClick={(e) => { e.stopPropagation(); setPendingFolderRename(folder); setFolderRenameName(folder.name); }}
                    >
                      <PencilSimple size={11} weight="bold" />
                    </button>
                    <button
                      type="button"
                      className="wf-folder-action-btn is-danger"
                      title="Delete folder"
                      aria-label={`Delete ${folder.name}`}
                      onClick={(e) => { e.stopPropagation(); setPendingFolderDelete(folder); }}
                    >
                      <Trash size={11} weight="bold" />
                    </button>
                    <div className="wf-folder-color-wrap" ref={folderColorPicker === folder.id ? colorPickerRef : null}>
                      <button
                        type="button"
                        className="wf-folder-action-btn"
                        title="Set folder color"
                        aria-label={`Set ${folder.name} color`}
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
            <div className="search-input-wrap">
              <MagnifyingGlass size={16} className="search-icon" aria-hidden="true" />
              <input
                className="field-input"
                aria-label="Search workflows"
                placeholder="Search workflows..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
              />
            </div>
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
          <div className="home-filter-chips">
            {[
              { label: "All", value: "all", cls: "chip-all" },
              { label: "Active", value: "active", cls: "chip-active" },
              { label: "Paused", value: "inactive", cls: "chip-paused" },
              { label: "Draft", value: "draft", cls: "chip-draft" },
              { label: "Error", value: "failed", cls: "chip-error" },
            ].map((chip) => (
              <button
                key={chip.value}
                type="button"
                className={`home-status-chip ${chip.cls}${statusFilter === chip.value ? " is-selected" : ""}`}
                aria-pressed={statusFilter === chip.value}
                onClick={() => toggleStatusFilter(chip.value)}
              >
                {chip.label}
              </button>
            ))}
            <select
              className="field-input home-filter-sort"
              aria-label="Sort workflows"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="updated">Last updated</option>
              <option value="name">Name A-Z</option>
              <option value="created">Created (newest)</option>
              <option value="last_run">Last run</option>
            </select>
          </div>
        </div>

        {error && (
          <div className="wf-load-error" role="alert">
            <WarningCircle size={22} aria-hidden="true" />
            <div><strong>Workflows could not be loaded</strong><p>{error}</p></div>
            <button className="btn" type="button" onClick={() => void workflowsQuery.refetch()}>Retry</button>
          </div>
        )}

        {!workflows && !error && (
          <div
            className="wf-grid"
            role="status"
            aria-label="Loading workflows"
          >
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
          <section className="wf-template-empty" aria-labelledby="workflow-empty-title">
            <div className="wf-template-empty-head">
              <span className="wf-template-empty-logo" aria-hidden="true">
                <Logo size={34} />
              </span>
              <div>
                <span className="mono-tag">Fresh workspace</span>
                <h2 id="workflow-empty-title">Start with a workflow template</h2>
                <p className="muted">
                  {canWrite
                    ? "Pick a starter, draft one with AI, or open a blank canvas."
                    : "There are no workflows in this workspace yet."}
                </p>
              </div>
              {canWrite && (
                <div className="wf-template-empty-actions">
                  <button
                    className="btn btn-primary"
                    type="button"
                    onClick={() => void createWithAi()}
                    disabled={starterBusy !== null}
                  >
                    <Lightning size={15} weight="bold" aria-hidden="true" />
                    {starterBusy === "ai" ? "Creating..." : "Create with AI"}
                  </button>
                  <button
                    className="btn"
                    type="button"
                    onClick={() => openCreate()}
                    disabled={starterBusy !== null}
                  >
                    <Plus size={15} weight="bold" aria-hidden="true" />
                    Blank workflow
                  </button>
                </div>
              )}
            </div>

            {canWrite ? (
              <div className="wf-template-gallery" aria-label="Workflow templates">
                {TEMPLATE_GALLERY.map((template) => {
                  const graph = template.graph();
                  const trust = WORKFLOW_TEMPLATE_TRUST[template.id];
                  return (
                    <article className="wf-template-card" key={template.id}>
                      <div className="wf-template-card-top">
                        <span>Template</span>
                        <span>{graph.nodes.length} nodes</span>
                      </div>
                      <h3>{template.name}</h3>
                      <p>{template.description}</p>
                      {trust && (
                        <dl className="wf-template-trust">
                          <div>
                            <dt>Output</dt>
                            <dd>{trust.expected_output}</dd>
                          </div>
                          <div>
                            <dt>Runtime</dt>
                            <dd>{trust.expected_runtime}</dd>
                          </div>
                          <div>
                            <dt>Access</dt>
                            <dd>
                              {trust.network_egress.length > 0
                                ? `Network: ${trust.network_egress.join(", ")}`
                                : "Local only · no credentials"}
                            </dd>
                          </div>
                        </dl>
                      )}
                      <button
                        className="btn btn-sm btn-ghost"
                        type="button"
                        aria-label={`Use template: ${template.name}`}
                        onClick={() => void createFromTemplate(template)}
                        disabled={starterBusy !== null}
                      >
                        {starterBusy === template.id ? "Creating..." : "Use template"}
                        <CaretRight size={14} weight="bold" aria-hidden="true" />
                      </button>
                    </article>
                  );
                })}
              </div>
            ) : (
              <p className="muted wf-template-readonly">
                You have read-only access. Ask a workspace editor to create the first workflow.
              </p>
            )}
          </section>
        )}

        {workflows && workflows.length > 0 && (
          <div className={viewMode === "list" ? "wf-list" : "wf-grid"}>
            {pagedVisible.map((wf) => {
              const hookBadge = providerStatusBadge(providerCounts(wf));
              const menuOpen = openMenuId === wf.id;
              const workflowStateKey = wf.last_run_status === "error"
                ? "error"
                : wf.active
                  ? "active"
                  : wf.has_unpublished_changes
                    ? "draft"
                    : "inactive";
              const workflowState = productState("workflow", workflowStateKey);
              const primaryStatus = {
                className: workflowStateKey === "error"
                  ? "run-error"
                  : workflowStateKey === "active"
                    ? "on"
                    : workflowStateKey === "draft"
                      ? "draft"
                      : "off",
                label: workflowState.label,
              };
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
                  <button
                    type="button"
                    className={`wf-card-checkbox${selectedIds.has(wf.id) ? " is-checked" : ""}`}
                    aria-label={`Select ${wf.name}`}
                    onClick={(e) => toggleSelect(wf.id, e)}
                  >
                    {selectedIds.has(wf.id) && <Check size={14} weight="bold" />}
                  </button>
                  <div className="wf-card-top">
                    <span
                      className={`wf-status ${primaryStatus.className}`}
                      title={workflowState.explanation}
                      aria-label={workflowState.accessibility_text}
                    >
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
        {selectedIds.size > 0 && (
          <div className="wf-bulk-bar">
            <span className="wf-bulk-count">{selectedIds.size} selected</span>
            <button type="button" className="btn btn-danger btn-sm" onClick={() => setPendingBulkDelete(true)}>
              Delete
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setBulkMoveOpen(true)}>
              Move to folder
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={bulkExport}>
              Export
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setSelectedIds(new Set()); setBulkMoveOpen(false); }}>
              Cancel
            </button>
          </div>
        )}
        {bulkMoveOpen && (
          <div className="wf-bulk-backdrop" onClick={() => setBulkMoveOpen(false)} />
        )}
      </main>

      {modal && canWrite && (
        <CreateModal
          onClose={() => setModal(false)}
          onCreated={(id) => navigate(`/workflows/${id}`)}
          initialTemplateId={modalTemplateId}
        />
      )}
      {importModal && canWrite && (
        <WorkflowImportModal
          onClose={() => setImportModal(false)}
          onImported={(id) => {
            setImportModal(false);
            navigate(`/workflows/${id}`);
          }}
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
      {bulkMoveOpen && (
        <div className="wf-bulk-popover">
          <strong>Move {selectedIds.size} to folder</strong>
          <div className="wf-folder-picker">
            <button
              type="button"
              className="wf-folder-pick-btn"
              onClick={() => void bulkMoveToFolder(null)}
            >
              No folder
            </button>
            {folders.map((folder) => (
              <button
                key={folder.id}
                type="button"
                className="wf-folder-pick-btn"
                onClick={() => void bulkMoveToFolder(folder.id)}
              >
                {folder.name}
              </button>
            ))}
          </div>
          {folders.length === 0 && <p className="muted">No folders yet.</p>}
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setBulkMoveOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {pendingBulkDelete && (
        <ConfirmDialog
          title="Delete workflows"
          body={`Delete ${selectedIds.size} workflow${selectedIds.size === 1 ? "" : "s"}? This cannot be undone.`}
          busy={bulkDeleteBusy}
          onCancel={() => setPendingBulkDelete(false)}
          onConfirm={() => void executeBulkDelete()}
        />
      )}
    </div>
  );
}
