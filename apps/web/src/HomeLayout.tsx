import { Suspense, useEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { BackendLoading } from "./BackendLoading";
import {
  HomeHeader,
  MobileAccountPanel,
  OrganizationSwitcher,
} from "./HomeHeader";
import { AppSidebar, MobileNavigation } from "./shell/AppNavigation";
import { safeGetItem, safeSetItem } from "./safeStorage";
import { useWorkspaceAccessContext } from "./WorkspaceAccess";

export function HomeLayout() {
  const workspace = useWorkspaceAccessContext();
  const user = workspace.user;
  const workspaceRole = workspace.role;
  const { pathname } = useLocation();
  const contentRef = useRef<HTMLDivElement>(null);
  const previousPathRef = useRef(pathname);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => safeGetItem("noodle-shell-sidebar-collapsed") === "1",
  );

  function updateSidebarCollapsed(collapsed: boolean): void {
    setSidebarCollapsed(collapsed);
    safeSetItem("noodle-shell-sidebar-collapsed", collapsed ? "1" : "0");
  }

  useEffect(() => {
    if (previousPathRef.current !== pathname) {
      previousPathRef.current = pathname;
      window.requestAnimationFrame(() => contentRef.current?.focus());
    }
  }, [pathname]);

  return (
    <div className={`noodle-shell-layout${sidebarCollapsed ? " noodle-shell-is-collapsed" : ""}`}>
      <a className="noodle-shell-skip-link" href="#main-content">Skip to content</a>
      <AppSidebar
        user={user}
        workspaceRole={workspaceRole}
        multiTenancyEnabled={workspace.multiTenancyEnabled}
        authRequired={workspace.authRequired}
        organizationSlot={<OrganizationSwitcher />}
        collapsed={sidebarCollapsed}
        onCollapsedChange={updateSidebarCollapsed}
      />
      <div className="noodle-shell-content">
        <HomeHeader />
        <div id="main-content" ref={contentRef} className="noodle-shell-main" tabIndex={-1}>
          <Suspense fallback={<BackendLoading retrying={false} />}>
            {workspace.multiTenancyEnabled && !workspace.loading && !workspace.hasActiveWorkspace ? (
              <section className="noodle-shell-workspace-blocked" role="status">
                <span aria-hidden="true">N</span>
                <h1>{workspace.isError ? "Workspace unavailable" : "No active workspace"}</h1>
                <p>{workspace.isError ? "Noodle could not load your workspace memberships. Retry the request, or sign out if the problem continues." : "Your memberships are unavailable or suspended. Open the workspace menu to create or select an active workspace, or sign out and contact an administrator."}</p>
                {workspace.isError && (
                  <button className="btn" type="button" onClick={() => void workspace.refetch()}>
                    Retry
                  </button>
                )}
              </section>
            ) : (
              <Outlet />
            )}
          </Suspense>
        </div>
      </div>
      <MobileNavigation
        user={user}
        workspaceRole={workspaceRole}
        multiTenancyEnabled={workspace.multiTenancyEnabled}
        authRequired={workspace.authRequired}
        organizationSlot={<OrganizationSwitcher />}
        profileSlot={<MobileAccountPanel />}
      />
    </div>
  );
}
