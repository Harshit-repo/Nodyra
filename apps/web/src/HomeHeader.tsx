import {
  Buildings,
  CaretDown,
  GearSix,
  Monitor,
  Moon,
  SignOut,
  Sun,
  UserCircle,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { api, errorMessage, getOrgId, setOrgId } from "./api";
import {
  getThemePreference,
  listenForSystemThemeChanges,
  setThemePreference,
  type ThemePreference,
} from "./theme";
import { useConfirm, usePrompt } from "./ConfirmProvider";
import { Logo } from "./Logo";
import {
  discardDirtyInstanceSettings,
  hasDirtyInstanceSettings,
  useInstanceSettingsDirty,
} from "./settingsDirty";
import { GlobalCommandMenu } from "./shell/GlobalCommandMenu";
import { getRouteScope, getRouteTitle } from "./shell/navigation";
import { requestShellOverlayOwnership } from "./shell/overlay";
import { resolveWorkspaceSelection } from "./shell/workspace";
import { useToast } from "./ToastProvider";
import type { OrgInfo } from "./types";
import { useSignOutCallback } from "./AuthRuntime";
import { useWorkspaceAccessContext } from "./WorkspaceAccess";

function initialsFor(value: string): string {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "N";
}

function useDismissiblePopover(open: boolean, onClose: () => void) {
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const animationFrame = window.requestAnimationFrame(() => {
      containerRef.current
        ?.querySelector<HTMLElement>(".nodyra-shell-popover a, .nodyra-shell-popover button")
        ?.focus();
    });
    function onPointerDown(event: MouseEvent): void {
      if (!containerRef.current?.contains(event.target as Node)) onClose();
    }
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key !== "Escape") return;
      onClose();
      triggerRef.current?.focus();
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose, open]);

  return { containerRef, triggerRef };
}

function useDirtyActionGuard() {
  const confirm = useConfirm();
  return useCallback(
    async ({ title, body, confirmLabel }: { title: string; body: string; confirmLabel: string }) => {
      const wasDirty = hasDirtyInstanceSettings();
      if (!wasDirty) return { allowed: true, wasDirty: false };
      requestShellOverlayOwnership();
      const allowed = await confirm({ title, body, confirmLabel });
      if (allowed) discardDirtyInstanceSettings();
      return { allowed, wasDirty };
    },
    [confirm],
  );
}

export function useCurrentWorkspaceRole(): string | null {
  return useWorkspaceAccessContext().role;
}

export function OrganizationSwitcher() {
  const workspace = useWorkspaceAccessContext();
  const settingsDirty = useInstanceSettingsDirty();
  const user = workspace.user;
  const [open, setOpen] = useState(false);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const guardDirtyAction = useDirtyActionGuard();
  const prompt = usePrompt();
  const { notify } = useToast();
  const close = useCallback(() => setOpen(false), []);
  const { containerRef, triggerRef } = useDismissiblePopover(open, close);
  const orgs = workspace.organizations;
  const multiTenancyEnabled = workspace.multiTenancyEnabled;
  const resolved = resolveWorkspaceSelection(orgs, getOrgId());
  const current = workspace.current;

  useEffect(() => {
    if (!workspace.isSuccess || !resolved.needsReconcile || settingsDirty) return;
    setOrgId(resolved.nextStoredId);
    window.location.replace("/");
  }, [resolved.needsReconcile, resolved.nextStoredId, settingsDirty, workspace.isSuccess]);

  useEffect(() => {
    if (
      workspace.isSuccess &&
      multiTenancyEnabled &&
      !workspace.hasActiveWorkspace &&
      getOrgId()
    ) {
      setOrgId(null);
    }
  }, [multiTenancyEnabled, workspace.hasActiveWorkspace, workspace.isSuccess]);

  if (!user) {
    return (
      <div className="nodyra-shell-workspace-static">
        <span className="nodyra-shell-org-mark">N</span>
        <span className="nodyra-shell-org-copy"><strong>Nodyra</strong><small>Local workspace</small></span>
      </div>
    );
  }

  if (workspace.loading) {
    return <div role="status" className="nodyra-shell-workspace-skeleton" aria-label="Loading workspace" />;
  }

  if (workspace.isSuccess && !multiTenancyEnabled) {
    const workspaceName = user.company || "Nodyra";
    return (
      <div className="nodyra-shell-workspace-static">
        <span className="nodyra-shell-org-mark">{initialsFor(workspaceName).slice(0, 1)}</span>
        <span className="nodyra-shell-org-copy"><strong>{workspaceName}</strong><small>Instance workspace</small></span>
      </div>
    );
  }

  const currentName = current?.name || (!workspace.hasActiveWorkspace ? "No active workspace" : resolved.needsReconcile ? "Refreshing workspace…" : "Loading workspace");
  const currentRole = current?.role;

  async function switchTo(org: OrgInfo): Promise<void> {
    if (switchingId || org.id === current?.id) {
      close();
      return;
    }
    close();
    const { allowed } = await guardDirtyAction({
      title: "Discard unsaved instance changes?",
      body: "Switching workspace reloads Nodyra. Your unsaved runtime and retention changes will be lost.",
      confirmLabel: "Discard and switch",
    });
    if (!allowed) return;
    setSwitchingId(org.id);
    setOrgId(org.id === "default" ? null : org.id);
    window.location.assign("/");
  }

  async function createOrganization(): Promise<void> {
    close();
    requestShellOverlayOwnership();
    const name = await prompt({
      title: "New workspace",
      label: "Workspace name",
      placeholder: "Acme Inc.",
      confirmLabel: "Create workspace",
    });
    if (!name?.trim()) return;
    const { allowed } = await guardDirtyAction({
      title: "Discard unsaved instance changes?",
      body: "Creating a workspace reloads Nodyra. Your unsaved runtime and retention changes will be lost.",
      confirmLabel: "Discard and create",
    });
    if (!allowed) return;
    try {
      setSwitchingId("new");
      const created = await api.createOrg({ name: name.trim() });
      setOrgId(created.id);
      window.location.assign("/");
    } catch (err) {
      setSwitchingId(null);
      notify(`Could not create workspace. ${errorMessage(err)}`, "error");
    }
  }

  return (
    <div className="nodyra-shell-org" ref={containerRef}>
      <button
        ref={triggerRef}
        className="nodyra-shell-org-trigger"
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        disabled={Boolean(switchingId)}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="nodyra-shell-org-mark">{initialsFor(currentName).slice(0, 1)}</span>
        <span className="nodyra-shell-org-copy"><strong>{switchingId ? "Switching…" : currentName}</strong><small>{!workspace.hasActiveWorkspace ? "Action required" : currentRole ? `${currentRole} in workspace` : "Workspace"}</small></span>
        <CaretDown size={15} aria-hidden="true" />
      </button>

      {open && (
        <div className="nodyra-shell-popover nodyra-shell-org-popover" role="dialog" aria-label="Switch workspace">
          <div className="nodyra-shell-popover-title">Workspaces</div>
          {workspace.loading ? (
            <div className="nodyra-shell-popover-state">Loading workspaces…</div>
          ) : workspace.isError ? (
            <div className="nodyra-shell-popover-state is-error">
              <span>{errorMessage(workspace.error)}</span>
              <button type="button" onClick={() => void workspace.refetch()}>Retry</button>
            </div>
          ) : (
            <div className="nodyra-shell-org-list">
              {orgs.map((org) => (
                <button
                  key={org.id}
                  type="button"
                  className={org.id === current?.id ? "is-current" : ""}
                  disabled={Boolean(switchingId) || org.status !== "active"}
                  onClick={() => void switchTo(org)}
                >
                  <span className="nodyra-shell-org-mark">{initialsFor(org.name).slice(0, 1)}</span>
                  <span><strong>{org.name}</strong><small>{org.status !== "active" ? org.status : org.role ? `${org.role} in workspace` : "Workspace member"}</small></span>
                  {org.id === current?.id && <span className="nodyra-shell-current-dot" aria-label="Current workspace" />}
                </button>
              ))}
            </div>
          )}
          <div className="nodyra-shell-popover-divider" />
          {(currentRole === "admin" || currentRole === "owner") && <Link to="/organization" onClick={close}><Buildings size={17} aria-hidden="true" />Manage workspace</Link>}
          <button type="button" onClick={() => void createOrganization()}>+ New workspace</button>
        </div>
      )}
    </div>
  );
}

function useSignOut(): () => void {
  const guardDirtyAction = useDirtyActionGuard();
  const handler = useSignOutCallback();
  return () => void (async () => {
    const { allowed } = await guardDirtyAction({
      title: "Discard unsaved instance changes?",
      body: "Signing out will discard your unsaved runtime and retention changes.",
      confirmLabel: "Discard and sign out",
    });
    if (!allowed) return;
    handler?.();
  })();
}

export function MobileAccountPanel() {
  const user = useWorkspaceAccessContext().user;
  const signOut = useSignOut();
  if (!user) return null;
  const displayName = user.name || user.email;
  return (
    <div className="nodyra-shell-mobile-account">
      <div className="nodyra-shell-account-head">
        <span className="nodyra-shell-avatar">{initialsFor(displayName)}</span>
        <span><strong>{displayName}</strong><small>{user.email}</small><em>Instance {user.role}</em></span>
      </div>
      <div className="nodyra-shell-mobile-account-actions">
        <Link to="/settings"><GearSix size={18} aria-hidden="true" />Account settings</Link>
        <button type="button" className="is-danger" onClick={signOut}><SignOut size={18} aria-hidden="true" />Sign out</button>
      </div>
    </div>
  );
}

export function HomeHeader() {
  const { pathname } = useLocation();
  const workspace = useWorkspaceAccessContext();
  const user = workspace.user;
  const workspaceRole = workspace.role;
  const title = getRouteTitle(pathname, "Nodyra");
  const scope = getRouteScope(pathname);
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const { containerRef, triggerRef } = useDismissiblePopover(open, close);
  const signOut = useSignOut();

  const [themePref, setThemePref] = useState<ThemePreference>(getThemePreference);

  useEffect(() => listenForSystemThemeChanges(), []);

  function cycleTheme(): void {
    const order: ThemePreference[] = ["dark", "light", "system"];
    const idx = order.indexOf(themePref);
    const next = order[(idx + 1) % order.length];
    setThemePreference(next);
    setThemePref(next);
  }

  const ThemeIcon = themePref === "dark" ? Sun : themePref === "light" ? Moon : Monitor;
  const themeTitle = themePref === "dark" ? "Dark mode (click for light)" : themePref === "light" ? "Light mode (click for dark)" : "System theme (click for dark)";

  useEffect(() => {
    document.title = `${title} · Nodyra`;
    close();
  }, [close, title]);

  const displayName = user?.name || user?.email || "Local workspace";

  return (
    <header className="nodyra-shell-topbar">
      <div className="nodyra-shell-page-context">
        <Link className="nodyra-shell-topbar-brand" to="/" aria-label="Nodyra home">
          <Logo size={22} />
          <span>Nodyra</span>
        </Link>
        <span className="nodyra-shell-page-scope">{scope ? `${scope[0].toUpperCase()}${scope.slice(1)}` : "Nodyra"}</span>
        <strong>{title}</strong>
      </div>
      <GlobalCommandMenu
        user={user}
        workspaceRole={workspaceRole}
        localMode={!user}
        multiTenancyEnabled={workspace.multiTenancyEnabled}
      />
      <div className="nodyra-shell-top-actions">
        <button
          type="button"
          className="nodyra-shell-theme-toggle"
          aria-label="Switch theme"
          title={themeTitle}
          onClick={cycleTheme}
        >
          <ThemeIcon size={18} aria-hidden="true" />
        </button>
        {user ? (
          <div className="nodyra-shell-account" ref={containerRef}>
            <button
              ref={triggerRef}
              className="nodyra-shell-account-trigger"
              type="button"
              aria-expanded={open}
              aria-haspopup="dialog"
              onClick={() => setOpen((value) => !value)}
            >
              <span className="nodyra-shell-avatar">{initialsFor(displayName)}</span>
              <span className="nodyra-shell-account-trigger-copy"><strong>{displayName}</strong><small>Instance {user.role}</small></span>
              <CaretDown size={15} aria-hidden="true" />
            </button>
            {open && (
              <div className="nodyra-shell-popover nodyra-shell-account-popover" role="dialog" aria-label="Account menu">
                <div className="nodyra-shell-account-head">
                  <span className="nodyra-shell-avatar is-large">{initialsFor(displayName)}</span>
                  <span><strong>{displayName}</strong><small>{user.email}</small>{user.company && <small>{user.company}</small>}<em>Instance {user.role}</em></span>
                </div>
                <div className="nodyra-shell-popover-divider" />
                <Link to="/settings" onClick={close}><UserCircle size={17} aria-hidden="true" />Account settings</Link>
                {workspace.multiTenancyEnabled && (workspaceRole === "admin" || workspaceRole === "owner") && <Link to="/organization" onClick={close}><Buildings size={17} aria-hidden="true" />Manage workspace</Link>}
                <div className="nodyra-shell-popover-divider" />
                <button type="button" className="is-danger" onClick={signOut}><SignOut size={17} aria-hidden="true" />Sign out</button>
              </div>
            )}
          </div>
        ) : (
          <div className="nodyra-shell-local-status">Local workspace</div>
        )}
      </div>
    </header>
  );
}
