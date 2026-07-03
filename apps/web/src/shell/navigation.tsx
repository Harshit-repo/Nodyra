import type { Icon } from "@phosphor-icons/react";
import {
  Archive,
  Buildings,
  ClockCounterClockwise,
  Code,
  Cpu,
  Gear,
  HardDrives,
  Key,
  Pulse,
  RocketLaunch,
  ShareNetwork,
  ShieldCheck,
} from "@phosphor-icons/react";

import type { UserInfo } from "../types";

export type NavigationGroup = "primary" | "resource" | "admin" | "settings";
export type NavigationScope = "account" | "workspace" | "instance";
export type NavigationAccess =
  | "all"
  | "workspace-editor"
  | "workspace-admin"
  | "scoped-admin"
  | "instance-admin";

export interface AppRouteDefinition {
  id:
    | "workflows"
    | "deployments"
    | "executions"
    | "artifacts"
    | "environments"
    | "runners"
    | "credentials"
    | "code-library"
    | "organization"
    | "security"
    | "activity"
    | "settings";
  label: string;
  shortLabel?: string;
  href: string;
  icon: Icon;
  group: NavigationGroup;
  scope: NavigationScope;
  access: NavigationAccess;
  mobilePrimary?: boolean;
  matches: (pathname: string) => boolean;
}

function routePrefix(href: string): (pathname: string) => boolean {
  return (pathname) => pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The single source of truth for application navigation. Keep route labels,
 * access rules and active matching here so the sidebar, mobile navigation and
 * future command surfaces cannot drift apart.
 */
export const APP_ROUTES: readonly AppRouteDefinition[] = [
  {
    id: "workflows",
    label: "Workflows",
    href: "/",
    icon: ShareNetwork,
    group: "primary",
    scope: "workspace",
    access: "all",
    mobilePrimary: true,
    matches: (pathname) => pathname === "/" || pathname.startsWith("/workflows/"),
  },
  {
    id: "deployments",
    label: "Deployments",
    shortLabel: "Deploy",
    href: "/deployments",
    icon: RocketLaunch,
    group: "primary",
    scope: "workspace",
    access: "all",
    mobilePrimary: true,
    matches: routePrefix("/deployments"),
  },
  {
    id: "executions",
    label: "Executions",
    shortLabel: "Runs",
    href: "/executions",
    icon: Pulse,
    group: "primary",
    scope: "workspace",
    access: "all",
    mobilePrimary: true,
    matches: routePrefix("/executions"),
  },
  {
    id: "artifacts",
    label: "Artifacts",
    href: "/artifacts",
    icon: Archive,
    group: "primary",
    scope: "workspace",
    access: "all",
    matches: routePrefix("/artifacts"),
  },
  {
    id: "environments",
    label: "Environments",
    shortLabel: "Envs",
    href: "/environments",
    icon: HardDrives,
    group: "primary",
    scope: "workspace",
    access: "all",
    mobilePrimary: true,
    matches: routePrefix("/environments"),
  },
  {
    id: "runners",
    label: "Runners",
    href: "/runner-pools",
    icon: Cpu,
    group: "primary",
    scope: "workspace",
    access: "all",
    matches: routePrefix("/runner-pools"),
  },
  {
    id: "credentials",
    label: "Credentials",
    href: "/credentials",
    icon: Key,
    group: "resource",
    scope: "workspace",
    access: "workspace-editor",
    matches: routePrefix("/credentials"),
  },
  {
    id: "code-library",
    label: "Code Library",
    href: "/code-library",
    icon: Code,
    group: "resource",
    scope: "workspace",
    access: "all",
    matches: routePrefix("/code-library"),
  },
  {
    id: "organization",
    label: "Workspace",
    href: "/organization",
    icon: Buildings,
    group: "admin",
    scope: "workspace",
    access: "workspace-admin",
    matches: routePrefix("/organization"),
  },
  {
    id: "security",
    label: "Team & access",
    href: "/security",
    icon: ShieldCheck,
    group: "admin",
    scope: "workspace",
    access: "instance-admin",
    matches: routePrefix("/security"),
  },
  {
    id: "activity",
    label: "Activity",
    href: "/activity",
    icon: ClockCounterClockwise,
    group: "admin",
    scope: "workspace",
    access: "scoped-admin",
    matches: routePrefix("/activity"),
  },
  {
    id: "settings",
    label: "Settings",
    href: "/settings",
    icon: Gear,
    group: "settings",
    scope: "account",
    access: "all",
    matches: routePrefix("/settings"),
  },
] as const;

export function isInstanceAdmin(user: Pick<UserInfo, "role"> | null | undefined): boolean {
  return user?.role === "owner" || user?.role === "admin";
}

export function canAccessNavigationRoute(
  route: AppRouteDefinition,
  user: Pick<UserInfo, "role"> | null | undefined,
  workspaceRole?: string | null,
  localMode = false,
  multiTenancyEnabled = true,
): boolean {
  if (route.access === "all") return true;
  if (localMode && route.access !== "workspace-admin") return true;
  if (route.access === "workspace-editor") {
    return ["owner", "admin", "editor"].includes(workspaceRole ?? "");
  }
  if (route.access === "workspace-admin") {
    if (!multiTenancyEnabled) return false;
    return workspaceRole === "owner" || workspaceRole === "admin";
  }
  if (route.access === "scoped-admin" && multiTenancyEnabled) {
    return workspaceRole === "owner" || workspaceRole === "admin";
  }
  return isInstanceAdmin(user);
}

export function getNavigationRoutes(
  group: NavigationGroup,
  user: Pick<UserInfo, "role"> | null | undefined,
  workspaceRole?: string | null,
  localMode = false,
  multiTenancyEnabled = true,
): AppRouteDefinition[] {
  return APP_ROUTES.filter(
    (route) =>
      route.group === group &&
      canAccessNavigationRoute(
        route,
        user,
        workspaceRole,
        localMode,
        multiTenancyEnabled,
      ),
  );
}

export function getMobilePrimaryRoutes(): AppRouteDefinition[] {
  return APP_ROUTES.filter((route) => route.mobilePrimary);
}

export function getMobileMoreRoutes(
  user: Pick<UserInfo, "role"> | null | undefined,
  workspaceRole?: string | null,
  localMode = false,
  multiTenancyEnabled = true,
): AppRouteDefinition[] {
  return APP_ROUTES.filter(
    (route) =>
      !route.mobilePrimary &&
      canAccessNavigationRoute(
        route,
        user,
        workspaceRole,
        localMode,
        multiTenancyEnabled,
      ),
  );
}

export function getRouteTitle(pathname: string, fallback = "Nodyra"): string {
  return APP_ROUTES.find((route) => route.matches(pathname))?.label ?? fallback;
}

export function getRouteScope(pathname: string): NavigationScope | null {
  return APP_ROUTES.find((route) => route.matches(pathname))?.scope ?? null;
}
