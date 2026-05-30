/** Client-side permission check that mirrors ``apps/api/app/security.py``.
 *
 * Hides actions the API would reject anyway. This is UI polish, not the
 * security boundary — the backend still enforces every permission via
 * ``require_permission(...)``. Keep this map in sync with the backend's
 * ``_PERMISSION_MIN_ROLE`` when new permissions land.
 */
import { getUser } from "./api";

export const ROLE_RANK = {
  viewer: 10,
  editor: 20,
  admin: 30,
  owner: 40,
} as const;

export type Role = keyof typeof ROLE_RANK;

export type Permission =
  | "workflow:write"
  | "workflow:run"
  | "deployment:write"
  | "deployment:run"
  | "code_module:write"
  | "pinned:write"
  | "artifact:delete"
  | "credential:read"
  | "credential:test"
  | "credential:write"
  | "environment:write"
  | "runner_pool:write"
  | "audit:read"
  | "user:manage";

const PERMISSION_MIN_ROLE: Record<Permission, Role> = {
  "workflow:write": "editor",
  "workflow:run": "editor",
  "deployment:write": "editor",
  "deployment:run": "editor",
  "code_module:write": "editor",
  "pinned:write": "editor",
  "artifact:delete": "editor",
  "credential:read": "editor",
  "credential:test": "editor",
  "credential:write": "admin",
  "environment:write": "admin",
  "runner_pool:write": "admin",
  "audit:read": "admin",
  "user:manage": "admin",
};

/** Returns true iff the current user (if any) holds ``permission``.
 *
 * If no user is signed in (auth disabled), every permission resolves true
 * — the backend's middleware does the same when ``auth_required=false``.
 */
export function useCan(permission: Permission): boolean {
  const user = getUser();
  if (!user) return true; // anonymous + auth-off → API allows it
  const role = (user.role as Role) ?? "viewer";
  const userRank = ROLE_RANK[role] ?? 0;
  const minRank = ROLE_RANK[PERMISSION_MIN_ROLE[permission]];
  return userRank >= minRank;
}

/** Imperative variant for non-component contexts (utility callbacks). */
export const can = useCan;
