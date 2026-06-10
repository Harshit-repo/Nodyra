import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { api, errorMessage, getOrgId, getUser, setOrgId } from "./api";
import { Logo } from "./Logo";
import type { OrgInfo } from "./types";

/** Org switcher — rendered only when the backend reports multi-tenancy on
 *  and the user belongs to at least one org. Switching persists the org and
 *  reloads so every page refetches under the new X-Org-Id. */
function OrgSwitcher() {
  const [orgs, setOrgs] = useState<OrgInfo[] | null>(null);
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .authRequired()
      .then((state) => {
        if (cancelled || !state.multi_tenancy || !state.signed_in) return null;
        return api.listMyOrgs().then((mine) => {
          if (!cancelled) setOrgs(mine);
        });
      })
      .catch(() => {
        /* org switcher is best-effort chrome; never block the header */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    function onDocumentClick(event: MouseEvent): void {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentClick);
    return () => document.removeEventListener("mousedown", onDocumentClick);
  }, []);

  if (!orgs || orgs.length === 0) return null;
  const currentId = getOrgId() ?? "default";
  const current =
    orgs.find((org) => org.id === currentId) ??
    orgs.find((org) => org.id === "default") ??
    orgs[0];

  function switchTo(org: OrgInfo): void {
    setOpen(false);
    if (org.id === current.id) return;
    setOrgId(org.id === "default" ? null : org.id);
    window.location.assign("/");
  }

  async function createOrg(): Promise<void> {
    const name = window.prompt("Organization name");
    if (!name?.trim()) return;
    try {
      const created = await api.createOrg({ name: name.trim() });
      setOrgId(created.id);
      window.location.assign("/");
    } catch (err) {
      window.alert(errorMessage(err));
    }
  }

  return (
    <div className="profile-menu org-switcher" ref={menuRef}>
      <button
        type="button"
        className="profile-trigger"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Switch organization"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="profile-trigger-text">
          <span>{current.name}</span>
          <small>organization</small>
        </span>
      </button>
      {open && (
        <div className="profile-dropdown" role="menu">
          {orgs.map((org) => (
            <button
              key={org.id}
              type="button"
              role="menuitem"
              aria-current={org.id === current.id ? "true" : undefined}
              onClick={() => switchTo(org)}
            >
              {org.name}
              {org.role ? ` — ${org.role}` : ""}
              {org.id === current.id ? " ✓" : ""}
            </button>
          ))}
          <button type="button" role="menuitem" onClick={createOrg}>
            + New organization
          </button>
        </div>
      )}
    </div>
  );
}

export function HomeHeader() {
  const { pathname } = useLocation();
  const user = getUser();
  const canAdmin = user?.role === "owner" || user?.role === "admin";
  const profileActive =
    pathname.startsWith("/settings") ||
    pathname.startsWith("/security") ||
    pathname.startsWith("/activity") ||
    pathname.startsWith("/credentials") ||
    pathname.startsWith("/code-library");
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const displayName = user?.name || user?.email || "User";
  const initials = displayName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "U";

  useEffect(() => {
    function onDocumentClick(event: MouseEvent): void {
      if (!menuRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onDocumentKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentClick);
    document.addEventListener("keydown", onDocumentKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocumentClick);
      document.removeEventListener("keydown", onDocumentKeyDown);
    };
  }, []);

  function signOut(): void {
    const handler = (window as unknown as { __noodle_sign_out?: () => void })
      .__noodle_sign_out;
    setOpen(false);
    handler?.();
  }

  return (
    <header className="home-header">
      <Link className="brand" to="/">
        <Logo size={28} />
        <span className="brand-name">noodle</span>
      </Link>
      <nav className="home-nav">
        <Link
          className={pathname === "/" ? "active" : ""}
          aria-current={pathname === "/" ? "page" : undefined}
          to="/"
        >
          Workflows
        </Link>
        <Link
          className={pathname.startsWith("/deployments") ? "active" : ""}
          aria-current={pathname.startsWith("/deployments") ? "page" : undefined}
          to="/deployments"
        >
          Deployments
        </Link>
        <Link
          className={pathname.startsWith("/executions") ? "active" : ""}
          aria-current={pathname.startsWith("/executions") ? "page" : undefined}
          to="/executions"
        >
          Executions
        </Link>
        <Link
          className={pathname.startsWith("/environments") ? "active" : ""}
          aria-current={
            pathname.startsWith("/environments") ? "page" : undefined
          }
          to="/environments"
        >
          Environments
        </Link>
        <Link
          className={pathname.startsWith("/runner-pools") ? "active" : ""}
          aria-current={pathname.startsWith("/runner-pools") ? "page" : undefined}
          to="/runner-pools"
        >
          Runners
        </Link>
        {user && <OrgSwitcher />}
        {user && (
          <div className="profile-menu" ref={menuRef}>
            <button
              type="button"
              className={`profile-trigger${profileActive ? " active" : ""}`}
              aria-expanded={open}
              aria-haspopup="menu"
              aria-label="User profile menu"
              onClick={() => setOpen((value) => !value)}
            >
              <span className="profile-avatar">{initials}</span>
              <span className="profile-trigger-text">
                <span>{displayName}</span>
                <small>{user.company || user.email}</small>
              </span>
            </button>
            {open && (
              <div className="profile-dropdown" role="menu">
                <div className="profile-dropdown-head">
                  <span className="profile-avatar large">{initials}</span>
                  <div>
                    <strong>{displayName}</strong>
                    <span>{user.email}</span>
                    {user.company && <span>{user.company}</span>}
                    <span className={`home-user-role role-${user.role}`}>
                      {user.role}
                    </span>
                  </div>
                </div>
                <Link
                  to="/settings"
                  role="menuitem"
                  onClick={() => setOpen(false)}
                >
                  Settings
                </Link>
                <Link
                  to="/credentials"
                  role="menuitem"
                  onClick={() => setOpen(false)}
                >
                  Credentials
                </Link>
                <Link
                  to="/code-library"
                  role="menuitem"
                  onClick={() => setOpen(false)}
                >
                  Code Library
                </Link>
                {canAdmin && (
                  <>
                    <Link
                      to="/security"
                      role="menuitem"
                      onClick={() => setOpen(false)}
                    >
                      Security
                    </Link>
                    <Link
                      to="/activity"
                      role="menuitem"
                      onClick={() => setOpen(false)}
                    >
                      Activity
                    </Link>
                    <Link
                      to="/environments"
                      role="menuitem"
                      onClick={() => setOpen(false)}
                    >
                      Environments
                    </Link>
                  </>
                )}
                <button type="button" role="menuitem" onClick={signOut}>
                  Sign out
                </button>
              </div>
            )}
          </div>
        )}
      </nav>
    </header>
  );
}
