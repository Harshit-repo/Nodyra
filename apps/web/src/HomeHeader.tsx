import { Link, useLocation } from "react-router-dom";

import { getUser } from "./api";
import { Logo } from "./Logo";

export function HomeHeader() {
  const { pathname } = useLocation();
  const user = getUser();

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
        <Link
          className={pathname.startsWith("/activity") ? "active" : ""}
          to="/activity"
        >
          Activity
        </Link>
        {user && (
          <div className="home-user">
            <span className="home-user-email">{user.email}</span>
            <button type="button" className="home-user-out" onClick={signOut}>
              sign out
            </button>
          </div>
        )}
      </nav>
    </header>
  );
}
