import { Check, PencilSimple, Plus, Trash } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import { EmptyState } from "../EmptyState";
import { api, userFriendlyError } from "../api";
import { useConfirm } from "../ConfirmProvider";
import { useEntitlements } from "../entitlements";
import { useToast } from "../ToastProvider";
import { useModalA11y } from "../useModalA11y";
import type { CustomRoleInfo } from "../types";

// List of all assignable permissions (must match CUSTOM_ROLE_PERMISSION_REGISTRY).
const ALL_PERMISSIONS = [
  { id: "workflow:read", label: "Workflow: Read" },
  { id: "workflow:write", label: "Workflow: Write" },
  { id: "workflow:run", label: "Workflow: Run" },
  { id: "workflow:delete", label: "Workflow: Delete" },
  { id: "workflow:publish", label: "Workflow: Publish" },
  { id: "run:cancel_others", label: "Run: Cancel Others" },
  { id: "credential:read_names", label: "Credential: Read Names" },
  { id: "credential:create", label: "Credential: Create" },
  { id: "audit:read", label: "Audit: Read" },
  { id: "mcp_connection:manage", label: "MCP Connection: Manage" },
];

const BUILTIN_ROLES = [
  { name: "Owner", description: "Full access including billing and SSO" },
  { name: "Admin", description: "Full access, cannot manage billing or SSO" },
  { name: "Editor", description: "Can create and run workflows" },
  { name: "Viewer", description: "Read-only access to workflows" },
];

// ---------------------------------------------------------------------------
// Create/Edit modal
// ---------------------------------------------------------------------------

function RoleModal({
  edit,
  onClose,
  onSaved,
  allPermissions,
}: {
  edit: CustomRoleInfo | null;
  onClose: () => void;
  onSaved: () => void;
  allPermissions: string[];
}) {
  const { notify } = useToast();
  const [name, setName] = useState(edit?.name ?? "");
  const [selected, setSelected] = useState<Set<string>>(
    new Set(edit?.permissions ?? []),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useModalA11y(dialogRef, onClose);

  function toggle(perm: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(perm)) next.delete(perm);
      else next.add(perm);
      return next;
    });
  }

  function selectAll() {
    setSelected(new Set(allPermissions));
  }

  function clearAll() {
    setSelected(new Set());
  }

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      if (edit) {
        await api.updateCustomRole(edit.id, {
          name: name.trim(),
          permissions: [...selected],
        });
        notify("Custom role updated.", "success");
      } else {
        await api.createCustomRole({
          name: name.trim(),
          permissions: [...selected],
        });
        notify("Custom role created.", "success");
      }
      onSaved();
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  const isEditing = edit !== null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="role-modal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2 id="role-modal-title">
            {isEditing ? "Edit custom role" : "Create custom role"}
          </h2>
        </div>

        <div className="modal-body">
          <label className="credential-form-field">
            <span>Role name *</span>
            <input
              className="field-input"
              autoFocus
              placeholder="e.g. Deploy Manager"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <div className="custom-role-permissions">
            <div className="custom-role-permissions-head">
              <span>Permissions</span>
              <div className="custom-role-permissions-actions">
                <button className="btn btn-sm btn-ghost" type="button" onClick={selectAll}>
                  Select all
                </button>
                <button className="btn btn-sm btn-ghost" type="button" onClick={clearAll}>
                  Clear all
                </button>
              </div>
            </div>
            <div className="custom-role-grid">
              {ALL_PERMISSIONS.map((perm) => (
                <label key={perm.id} className={`custom-role-checkbox${selected.has(perm.id) ? " is-selected" : ""}`}>
                  <input
                    type="checkbox"
                    checked={selected.has(perm.id)}
                    onChange={() => toggle(perm.id)}
                  />
                  <Check size={14} weight="bold" className="checkbox-icon" />
                  <span>{perm.label}</span>
                </label>
              ))}
            </div>
            <small className="muted">
              Custom roles completely replace the built-in role. Members assigned
              this role get exactly the permissions selected above — not their
              built-in role permissions plus these.
            </small>
          </div>
        </div>

        {error && <p className="error-text">{error}</p>}

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={busy}
          >
            {busy ? "Saving..." : isEditing ? "Save changes" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function RolesPage() {
  const { notify } = useToast();
  const confirm = useConfirm();
  const entitlements = useEntitlements();

  const [roles, setRoles] = useState<CustomRoleInfo[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState<CustomRoleInfo | null>(null);

  const hasFeature = entitlements.has("advanced_rbac");

  useEffect(() => {
    if (hasFeature) {
      loadRoles();
    } else {
      setLoading(false);
      setRoles([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasFeature]);

  async function loadRoles() {
    setLoading(true);
    setError("");
    try {
      const data = await api.listCustomRoles();
      setRoles(data);
    } catch (err) {
      setError(userFriendlyError(err));
      setRoles([]);
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(role: CustomRoleInfo) {
    const ok = await confirm({
      title: "Delete custom role?",
      body: `"${role.name}" will be permanently removed. Members assigned this role will fall back to their built-in role.`,
    });
    if (!ok) return;
    try {
      await api.deleteCustomRole(role.id);
      notify("Custom role deleted.", "success");
      await loadRoles();
    } catch (err) {
      notify(
        `Could not delete custom role. ${userFriendlyError(err)}`,
        "error",
      );
    }
  }

  function openCreate() {
    setEditing(null);
    setModal(true);
  }

  function openEdit(role: CustomRoleInfo) {
    setEditing(role);
    setModal(true);
  }

  function closeModal() {
    setModal(false);
    setEditing(null);
  }

  function onSaved() {
    closeModal();
    void loadRoles();
  }

  if (!hasFeature) {
    return (
      <div className="home">
        <main className="home-main">
          <EmptyState
            icon={<PencilSimple size={48} />}
            title="Advanced RBAC"
            description="Custom roles are available with the Pro plan or higher."
          />
        </main>
      </div>
    );
  }

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Custom Roles
            {roles && <span className="home-count">{roles.length}</span>}
          </h1>
          <button className="btn btn-primary" onClick={openCreate}>
            <Plus size={16} weight="bold" /> New role
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}

        {loading && (
          <div className="env-grid" aria-label="Loading custom roles">
            {Array.from({ length: 2 }).map((_, index) => (
              <article className="env-card skeleton-card" key={index}>
                <span className="skeleton-line short" />
                <span className="skeleton-line title" />
                <span className="skeleton-line" />
              </article>
            ))}
          </div>
        )}

        {!loading && roles && roles.length === 0 && (
          <EmptyState
            icon={<PencilSimple size={48} />}
            title="No custom roles yet"
            description="Create custom roles with specific permission sets for your team."
            action={
              <button className="btn btn-primary" onClick={openCreate}>
                New role
              </button>
            }
          />
        )}

        {!loading && roles && roles.length > 0 && (
          <div className="custom-roles-list">
            {roles.map((role) => (
              <article key={role.id} className="env-card">
                <div className="env-card-head">
                  <div className="env-title">
                    <h3>{role.name}</h3>
                  </div>
                </div>
                <div className="env-packages">
                  {role.permissions.map((perm) => (
                    <span key={perm} className="pkg-chip">{perm}</span>
                  ))}
                </div>
                <div className="env-actions">
                  <button className="btn btn-sm btn-ghost" onClick={() => openEdit(role)}>
                    <PencilSimple size={14} /> Edit
                  </button>
                  <button className="btn btn-sm btn-ghost" onClick={() => void handleDelete(role)}>
                    <Trash size={14} /> Delete
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}

        {/* Built-in roles info */}
        <div className="custom-roles-section">
          <h3>Built-in roles</h3>
          <p className="muted">Built-in roles are predefined and cannot be modified. Assign a custom role to a member to override their built-in role permissions.</p>
          <div className="custom-roles-builtin-grid">
            {BUILTIN_ROLES.map((role) => (
              <div key={role.name} className="env-card">
                <div className="env-card-head">
                  <h3>{role.name}</h3>
                </div>
                <p className="muted">{role.description}</p>
              </div>
            ))}
          </div>
        </div>
      </main>

      {modal && (
        <RoleModal
          edit={editing}
          onClose={closeModal}
          onSaved={onSaved}
          allPermissions={ALL_PERMISSIONS.map((p) => p.id)}
        />
      )}
    </div>
  );
}
