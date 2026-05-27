import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { getUser } from "./api";
import { Logo } from "./Logo";

export function HomeHeader() {
  const { pathname } = useLocation();
  const user = getUser();
  const canAdmin = user?.role === "owner" || user?.role === "admin";
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
    document.addEventListener("mousedown", onDocumentClick);
    return () => document.removeEventListener("mousedown", onDocumentClick);
  }, []);

  function signOut(): void {
    const handler = (window as unknown as { __noodle_sign_out?: () => void })
      .__noodle_sign_out;
    handler?.();
  }

  return (
    <header className="home-header">
      <Link className="brand" to="/">
        <Logo size={28} />
        <span className="brand-name">noodle</span>
      </Link>
      <nav className="home-nav">
        <Link className={pathname === "/" ? "active" : ""} to="/">
          Workflows
        </Link>
        <Link
          className={pathname.startsWith("/deployments") ? "active" : ""}
          to="/deployments"
        >
          Deployments
        </Link>
        <Link
          className={pathname.startsWith("/executions") ? "active" : ""}
          to="/executions"
        >
          Executions
        </Link>
        <Link
          className={pathname.startsWith("/environments") ? "active" : ""}
          to="/environments"
        >
          Environments
        </Link>
        <Link
          className={pathname.startsWith("/code-library") ? "active" : ""}
          to="/code-library"
        >
          Code Library
        </Link>
        <Link
          className={pathname.startsWith("/credentials") ? "active" : ""}
          to="/credentials"
        >
          Credentials
        </Link>
        {user && (
          <div className="profile-menu" ref={menuRef}>
            <button
              type="button"
              className="profile-trigger"
              aria-expanded={open}
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
                <Link to="/settings" onClick={() => setOpen(false)}>
                  Settings
                </Link>
                <Link to="/credentials" onClick={() => setOpen(false)}>
                  Credentials
                </Link>
                {canAdmin && (
                  <>
                    <Link to="/security" onClick={() => setOpen(false)}>
                      Security
                    </Link>
                    <Link to="/activity" onClick={() => setOpen(false)}>
                      Activity
                    </Link>
                    <Link to="/environments" onClick={() => setOpen(false)}>
                      Environments
                    </Link>
                  </>
                )}
                <button type="button" onClick={signOut}>
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
