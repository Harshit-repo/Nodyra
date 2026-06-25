import { CaretDoubleLeft, CaretDoubleRight, DotsThree, X } from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link, useLocation } from "react-router-dom";

import { Logo } from "../Logo";
import type { UserInfo } from "../types";
import {
  getMobileMoreRoutes,
  getMobilePrimaryRoutes,
  getNavigationRoutes,
  type AppRouteDefinition,
  type NavigationGroup,
} from "./navigation";
import { SHELL_OVERLAY_OPEN_EVENT } from "./overlay";
import "./shell.css";

export interface AppNavigationProps {
  user?: Pick<UserInfo, "role"> | null;
  workspaceRole?: string | null;
  multiTenancyEnabled?: boolean;
  /** False represents Noodle's authentication-disabled local workspace mode. */
  authRequired?: boolean;
  organizationSlot?: ReactNode;
  profileSlot?: ReactNode;
  className?: string;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
}

function classes(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

function NavigationLink({
  route,
  pathname,
  onNavigate,
}: {
  route: AppRouteDefinition;
  pathname: string;
  onNavigate?: () => void;
}) {
  const active = route.matches(pathname);
  const Icon = route.icon;
  return (
    <Link
      className={classes("noodle-shell-nav-link", active && "noodle-shell-is-active")}
      to={route.href}
      title={route.label}
      aria-current={active ? "page" : undefined}
      onClick={onNavigate}
    >
      <Icon size={17} weight={active ? "fill" : "regular"} aria-hidden="true" />
      <span>{route.label}</span>
    </Link>
  );
}

const GROUP_LABELS: Partial<Record<NavigationGroup, string>> = {
  resource: "Resources",
  admin: "Administration",
};

function SidebarGroup({
  group,
  pathname,
  user,
  workspaceRole,
  localMode,
  multiTenancyEnabled,
}: {
  group: NavigationGroup;
  pathname: string;
  user: AppNavigationProps["user"];
  workspaceRole: AppNavigationProps["workspaceRole"];
  localMode: boolean;
  multiTenancyEnabled: boolean;
}) {
  const routes = getNavigationRoutes(
    group,
    user,
    workspaceRole,
    localMode,
    multiTenancyEnabled,
  );
  if (routes.length === 0) return null;
  const label = GROUP_LABELS[group];
  return (
    <div className="noodle-shell-nav-group">
      {label && <p className="noodle-shell-nav-heading">{label}</p>}
      {routes.map((route) => (
        <NavigationLink route={route} pathname={pathname} key={route.id} />
      ))}
    </div>
  );
}

export function AppSidebar({
  user = null,
  workspaceRole = null,
  multiTenancyEnabled = false,
  authRequired = true,
  organizationSlot,
  profileSlot,
  className,
  collapsed = false,
  onCollapsedChange,
}: AppNavigationProps) {
  const { pathname } = useLocation();
  const localMode = !authRequired && !user;

  return (
    <aside
      className={classes(
        "noodle-shell-sidebar",
        collapsed && "noodle-shell-is-collapsed",
        className,
      )}
      aria-label="Application sidebar"
    >
      <div className="noodle-shell-sidebar-head">
        <Link className="noodle-shell-brand" to="/" aria-label="Noodle workflows">
          <Logo size={26} />
          <span>noodle</span>
        </Link>
        {organizationSlot && (
          <div className="noodle-shell-organization-slot">{organizationSlot}</div>
        )}
      </div>

      <nav className="noodle-shell-sidebar-nav" aria-label="Primary navigation">
        <SidebarGroup group="primary" pathname={pathname} user={user} workspaceRole={workspaceRole} localMode={localMode} multiTenancyEnabled={multiTenancyEnabled} />
        <SidebarGroup group="resource" pathname={pathname} user={user} workspaceRole={workspaceRole} localMode={localMode} multiTenancyEnabled={multiTenancyEnabled} />
        <SidebarGroup group="admin" pathname={pathname} user={user} workspaceRole={workspaceRole} localMode={localMode} multiTenancyEnabled={multiTenancyEnabled} />
      </nav>

      <div className="noodle-shell-sidebar-foot">
        <SidebarGroup group="settings" pathname={pathname} user={user} workspaceRole={workspaceRole} localMode={localMode} multiTenancyEnabled={multiTenancyEnabled} />
        {profileSlot ? (
          <div className="noodle-shell-profile-slot">{profileSlot}</div>
        ) : !authRequired && !user ? (
          <div className="noodle-shell-local-mode" role="status">
            <span className="noodle-shell-local-mode-dot" aria-hidden="true" />
            Local workspace
          </div>
        ) : null}
        {onCollapsedChange && (
          <button
            className="noodle-shell-collapse-button"
            type="button"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => onCollapsedChange(!collapsed)}
          >
            {collapsed ? (
              <CaretDoubleRight size={18} aria-hidden="true" />
            ) : (
              <CaretDoubleLeft size={18} aria-hidden="true" />
            )}
            <span>{collapsed ? "Expand" : "Collapse"}</span>
          </button>
        )}
      </div>
    </aside>
  );
}

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function MobileMoreDrawer({
  open,
  onClose,
  user,
  workspaceRole,
  multiTenancyEnabled,
  authRequired,
  organizationSlot,
  profileSlot,
  returnFocusRef,
  drawerId,
}: AppNavigationProps & {
  open: boolean;
  onClose: () => void;
  returnFocusRef: React.RefObject<HTMLButtonElement>;
  drawerId: string;
}) {
  const { pathname } = useLocation();
  const titleId = useId();
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const animationFrame = window.requestAnimationFrame(() => {
      const first = drawerRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      first?.focus();
    });

    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = Array.from(
        drawerRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [],
      ).filter((element) => !element.hasAttribute("hidden"));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!drawerRef.current?.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = previousOverflow;
      returnFocusRef.current?.focus();
    };
  }, [onClose, open, returnFocusRef]);

  if (!open) return null;
  const routes = getMobileMoreRoutes(
    user,
    workspaceRole,
    !authRequired && !user,
    multiTenancyEnabled,
  );

  return (
    <div className="noodle-shell-drawer-layer">
      <div
        className="noodle-shell-drawer-backdrop"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      />
      <div
        id={drawerId}
        ref={drawerRef}
        className="noodle-shell-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClickCapture={(event) => {
          const target = event.target as HTMLElement;
          if (target.closest("a[href]")) onClose();
        }}
      >
        <div className="noodle-shell-drawer-head">
          <div>
            <h2 id={titleId}>More</h2>
            <p>Workspace navigation</p>
          </div>
          <button
            className="noodle-shell-icon-button"
            type="button"
            aria-label="Close navigation menu"
            onClick={onClose}
          >
            <X size={22} aria-hidden="true" />
          </button>
        </div>

        {organizationSlot && (
          <div className="noodle-shell-drawer-slot">{organizationSlot}</div>
        )}

        <nav className="noodle-shell-drawer-nav" aria-label="Additional navigation">
          {routes.map((route) => (
            <NavigationLink
              route={route}
              pathname={pathname}
              onNavigate={onClose}
              key={route.id}
            />
          ))}
        </nav>

        {profileSlot ? (
          <div className="noodle-shell-drawer-slot noodle-shell-drawer-profile">
            {profileSlot}
          </div>
        ) : !authRequired && !user ? (
          <div className="noodle-shell-local-mode" role="status">
            <span className="noodle-shell-local-mode-dot" aria-hidden="true" />
            Local workspace
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function MobileNavigation({
  user = null,
  workspaceRole = null,
  multiTenancyEnabled = false,
  authRequired = true,
  organizationSlot,
  profileSlot,
  className,
}: AppNavigationProps) {
  const { pathname } = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const drawerId = useId();
  const primaryRoutes = getMobilePrimaryRoutes();
  const moreActive = getMobileMoreRoutes(
    user,
    workspaceRole,
    !authRequired && !user,
    multiTenancyEnabled,
  ).some((route) => route.matches(pathname));
  const closeMore = useCallback(() => setMoreOpen(false), []);

  useEffect(() => {
    setMoreOpen(false);
  }, [pathname]);

  useEffect(() => {
    const media = window.matchMedia?.("(min-width: 1024px)");
    const closeForDesktop = (event?: MediaQueryListEvent) => {
      if (event?.matches ?? media?.matches) setMoreOpen(false);
    };
    const closeForOverlay = () => setMoreOpen(false);
    media?.addEventListener("change", closeForDesktop);
    window.addEventListener(SHELL_OVERLAY_OPEN_EVENT, closeForOverlay);
    return () => {
      media?.removeEventListener("change", closeForDesktop);
      window.removeEventListener(SHELL_OVERLAY_OPEN_EVENT, closeForOverlay);
    };
  }, []);

  return (
    <>
      <nav
        className={classes("noodle-shell-mobile-nav", className)}
        aria-label="Primary navigation"
      >
        {primaryRoutes.map((route) => {
          const active = route.matches(pathname);
          const Icon = route.icon;
          return (
            <Link
              className={classes(
                "noodle-shell-mobile-link",
                active && "noodle-shell-is-active",
              )}
              to={route.href}
              aria-current={active ? "page" : undefined}
              key={route.id}
            >
              <Icon size={22} weight={active ? "fill" : "regular"} aria-hidden="true" />
              <span>{route.shortLabel ?? route.label}</span>
            </Link>
          );
        })}
        <button
          ref={moreButtonRef}
          type="button"
          className={classes(
            "noodle-shell-mobile-link",
            moreActive && "noodle-shell-is-active",
          )}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          aria-controls={drawerId}
          onClick={() => setMoreOpen(true)}
        >
          <DotsThree size={22} weight={moreActive ? "fill" : "bold"} aria-hidden="true" />
          <span>More</span>
        </button>
      </nav>
      <MobileMoreDrawer
        open={moreOpen}
        onClose={closeMore}
        user={user}
        workspaceRole={workspaceRole}
        multiTenancyEnabled={multiTenancyEnabled}
        authRequired={authRequired}
        organizationSlot={organizationSlot}
        profileSlot={profileSlot}
        returnFocusRef={moreButtonRef}
        drawerId={drawerId}
      />
    </>
  );
}
