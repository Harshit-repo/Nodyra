import { useEffect, useMemo, useState } from "react";

import { api, errorMessage, getUser } from "./api";
import { HomeHeader } from "./HomeHeader";
import { useToast } from "./ToastProvider";
import type { UserAdminInfo } from "./types";

const ROLE_OPTIONS = ["viewer", "editor", "admin"];
const ROLE_CARDS = ["viewer", "editor", "admin", "owner"];

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

function roleSummary(role: string): string {
  if (role === "admin") return "Manage credentials, environments, and users.";
  if (role === "owner") return "Workspace owner with all admin rights.";
  if (role === "editor") return "Build and run workflows.";
  return "Read-only access.";
}

export function SecurityPage() {
  const currentUser = getUser();
  const { notify } = useToast();
  const [users, setUsers] = useState<UserAdminInfo[] | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");
  const [busy, setBusy] = useState(false);

  const availableRoles = useMemo(() => ROLE_OPTIONS, []);

  function refresh(): void {
    api
      .listUsers()
      .then(setUsers)
      .catch((err) => setError(String(err)));
  }

  useEffect(() => {
    refresh();
  }, []);

  async function createUser(): Promise<void> {
    if (
      busy ||
      !name.trim() ||
      !company.trim() ||
      !email.trim() ||
      password.length < 8
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await api.createUser({
        name: name.trim(),
        company: company.trim(),
        email: email.trim(),
        password,
        role,
      });
      setUsers((items) => [created, ...(items ?? [])]);
      setName("");
      setCompany("");
      setEmail("");
      setPassword("");
      setRole("viewer");
      notify("User invited.", "success");
    } catch (err) {
      // Form submission error → inline near the form (toast would be redundant).
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function updateRole(user: UserAdminInfo, nextRole: string): Promise<void> {
    try {
      const updated = await api.updateUserRole(user.id, nextRole);
      setUsers((items) =>
        (items ?? []).map((item) => (item.id === updated.id ? updated : item)),
      );
      notify("Role updated.", "success");
    } catch (err) {
      notify(`Could not update role. ${errorMessage(err)}`, "error");
    }
  }

  async function deleteUser(user: UserAdminInfo): Promise<void> {
    if (!window.confirm(`Delete ${user.email}?`)) return;
    try {
      await api.deleteUser(user.id);
      setUsers((items) => (items ?? []).filter((item) => item.id !== user.id));
      notify("User deleted.", "success");
    } catch (err) {
      notify(`Could not delete user. ${errorMessage(err)}`, "error");
    }
  }

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <div>
            <h1>
              Admin users
              {users && <span className="home-count">{users.length}</span>}
            </h1>
            <p className="muted">
              Invite users and assign roles. Owners and admins can manage access.
            </p>
          </div>
        </div>

        {error && <p className="error-text">{error}</p>}

        <section className="security-create">
          <div>
            <h2>Invite user</h2>
            <p className="muted">
              They can sign in with this temporary password and change it later.
            </p>
          </div>
          <input
            className="field-input"
            type="text"
            placeholder="Full name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <input
            className="field-input"
            type="text"
            placeholder="Company"
            value={company}
            onChange={(event) => setCompany(event.target.value)}
          />
          <input
            className="field-input"
            type="email"
            placeholder="user@example.com"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <input
            className="field-input"
            type="password"
            placeholder="temporary password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <select
            className="field-input"
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            {availableRoles.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-primary"
            disabled={
              busy ||
              !name.trim() ||
              !company.trim() ||
              !email.trim() ||
              password.length < 8
            }
            onClick={() => void createUser()}
          >
            Invite
          </button>
        </section>

        <div className="security-roles">
          {ROLE_CARDS.map((item) => (
            <div className="security-role" key={item}>
              <span className={`role-pill role-${item}`}>{item}</span>
              <p>{roleSummary(item)}</p>
            </div>
          ))}
        </div>

        {!users && !error && <p className="muted">Loading…</p>}

        {users && (
          <div className="security-users">
            {users.map((user) => (
              <div className="security-user-row" key={user.id}>
                <div>
                  <strong>{user.name || user.email}</strong>
                  <span className="muted">
                    {user.company ? `${user.company} · ` : ""}
                    {user.email}
                  </span>
                  <span className="muted">Invited {when(user.created_at)}</span>
                </div>
                <select
                  className="field-input"
                  value={user.role}
                  disabled={user.id === currentUser?.id}
                  onChange={(event) =>
                    void updateRole(user, event.target.value)
                  }
                >
                  {(user.role === "owner"
                    ? ["owner", ...availableRoles]
                    : availableRoles
                  ).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={user.id === currentUser?.id}
                  onClick={() => void deleteUser(user)}
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
