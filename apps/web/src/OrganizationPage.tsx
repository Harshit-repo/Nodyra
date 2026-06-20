import { useEffect, useMemo, useState } from "react";

import { errorMessage, getOrgId, getUser } from "./api";
import { useConfirm } from "./ConfirmProvider";
import {
  useAddOrgMemberMutation,
  useMyOrgs,
  useOrgMembers,
  useOrgSettings,
  useOrgUsage,
  useRemoveOrgMemberMutation,
  useUpdateOrgMemberMutation,
  useUpdateOrgSettingsMutation,
} from "./queries";
import { useToast } from "./ToastProvider";
import type { OrgMemberInfo } from "./types";

const MEMBER_ROLES = ["viewer", "editor", "admin", "owner"];

const QUOTA_FIELDS: { key: string; label: string; hint: string }[] = [
  {
    key: "max_concurrent_runs",
    label: "Concurrent runs",
    hint: "Runs leasing from the queue at once. 0 = unlimited.",
  },
  {
    key: "executions_per_day",
    label: "Executions / day",
    hint: "Hard daily ceiling, counted at run start (UTC). 0 = unlimited.",
  },
  {
    key: "max_map_width",
    label: "Map fan-out",
    hint: "Max rows a Map node may turn into child runs.",
  },
  {
    key: "max_loop_iterations",
    label: "Loop iterations",
    hint: "Max units a single loop may drive. 0 = unlimited.",
  },
  {
    key: "max_inflight_subworkflows",
    label: "In-flight sub-workflows",
    hint: "Concurrent sub-workflow spawns. 0 = instance default.",
  },
];

function hours(seconds: number): string {
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)} h`;
  if (seconds >= 60) return `${(seconds / 60).toFixed(1)} min`;
  return `${Math.round(seconds)} s`;
}

export function OrganizationPage() {
  const currentUser = getUser();
  const { notify } = useToast();
  const confirm = useConfirm();
  const orgId = getOrgId() ?? "default";

  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [busy, setBusy] = useState(false);
  const [draftQuotas, setDraftQuotas] = useState<Record<string, string>>({});
  const orgsQuery = useMyOrgs();
  const membersQuery = useOrgMembers();
  const settingsQuery = useOrgSettings(orgId);
  const usageQuery = useOrgUsage(orgId);
  const addMemberMutation = useAddOrgMemberMutation();
  const updateMemberMutation = useUpdateOrgMemberMutation();
  const removeMemberMutation = useRemoveOrgMemberMutation();
  const updateSettingsMutation = useUpdateOrgSettingsMutation();
  const org = useMemo(
    () =>
      orgsQuery.data?.find((item) => item.id === orgId) ??
      orgsQuery.data?.find((item) => item.id === "default") ??
      null,
    [orgId, orgsQuery.data],
  );
  const members = membersQuery.data ?? null;
  const settings = settingsQuery.data ?? null;
  const usage = usageQuery.data ?? null;

  const myRole = org?.role ?? null;
  const canManage = myRole === "admin" || myRole === "owner";
  const isOwner = myRole === "owner";

  useEffect(() => {
    if (orgsQuery.isError) setError(errorMessage(orgsQuery.error));
    else if (membersQuery.isError) setError(errorMessage(membersQuery.error));
    else setError("");
  }, [membersQuery.error, membersQuery.isError, orgsQuery.error, orgsQuery.isError]);

  useEffect(() => {
    if (settings) setDraftQuotas({});
  }, [settings]);

  async function addMember(): Promise<void> {
    if (busy || !email.trim()) return;
    setBusy(true);
    setError("");
    try {
      await addMemberMutation.mutateAsync({ email: email.trim(), role });
      setEmail("");
      setRole("viewer");
      notify("Member added.", "success");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(member: OrgMemberInfo, next: string): Promise<void> {
    try {
      await updateMemberMutation.mutateAsync({ userId: member.user_id, role: next });
      notify("Role updated.", "success");
    } catch (err) {
      notify(`Could not update role. ${errorMessage(err)}`, "error");
    }
  }

  async function removeMember(member: OrgMemberInfo): Promise<void> {
    const ok = await confirm({
      title: "Remove member?",
      body: `${member.email} will lose access to this organization.`,
    });
    if (!ok) return;
    try {
      await removeMemberMutation.mutateAsync(member.user_id);
      notify("Member removed.", "success");
    } catch (err) {
      notify(`Could not remove member. ${errorMessage(err)}`, "error");
    }
  }

  async function saveQuotas(): Promise<void> {
    if (!settings) return;
    const body: Record<string, number> = {};
    for (const [key, raw] of Object.entries(draftQuotas)) {
      const trimmed = raw.trim();
      if (trimmed === "") continue;
      const value = Number(trimmed);
      if (!Number.isFinite(value) || value < -1) {
        notify(`Invalid value for ${key}.`, "error");
        return;
      }
      body[key] = Math.trunc(value);
    }
    if (Object.keys(body).length === 0) return;
    try {
      await updateSettingsMutation.mutateAsync({ orgId, body });
      setDraftQuotas({});
      notify("Quotas updated.", "success");
    } catch (err) {
      notify(`Could not update quotas. ${errorMessage(err)}`, "error");
    }
  }

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <div>
            <h1>
              {org ? org.name : "Organization"}
              {members && <span className="home-count">{members.length}</span>}
            </h1>
            <p className="muted">
              Members, roles, and resource quotas for this organization
              {org ? ` (${org.slug})` : ""}.
            </p>
          </div>
        </div>

        {error && <p className="error-text">{error}</p>}

        {canManage && (
          <section className="security-create">
            <div>
              <h2>Add member</h2>
              <p className="muted">
                The person needs an existing account on this instance.
              </p>
            </div>
            <input
              className="field-input"
              type="email"
              placeholder="user@example.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
            <select
              className="field-input"
              value={role}
              onChange={(event) => setRole(event.target.value)}
            >
              {(isOwner ? MEMBER_ROLES : MEMBER_ROLES.filter((r) => r !== "owner")).map(
                (item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ),
              )}
            </select>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || !email.trim()}
              onClick={() => void addMember()}
            >
              Add
            </button>
          </section>
        )}

        {members && (
          <div className="security-users">
            {members.map((member) => (
              <div className="security-user-row" key={member.user_id}>
                <div>
                  <strong>{member.name || member.email}</strong>
                  <span className="muted">{member.email}</span>
                </div>
                <select
                  className="field-input"
                  value={member.role}
                  disabled={!canManage || member.user_id === currentUser?.id}
                  onChange={(event) => void changeRole(member, event.target.value)}
                >
                  {(member.role === "owner" || isOwner
                    ? MEMBER_ROLES
                    : MEMBER_ROLES.filter((r) => r !== "owner")
                  ).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={!canManage || member.user_id === currentUser?.id}
                  onClick={() => void removeMember(member)}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}

        {settings && (
          <section className="security-create" style={{ marginTop: 24 }}>
            <div>
              <h2>Resource quotas</h2>
              <p className="muted">
                Blank keeps the current value; enter -1 to clear an override
                back to the instance default.
                {!isOwner && " Only an owner can change quotas."}
              </p>
            </div>
            {QUOTA_FIELDS.map((field) => (
              <label key={field.key} className="field-label" title={field.hint}>
                <span className="muted">
                  {field.label}
                  {settings.overridden.includes(field.key) ? " (override)" : ""}
                </span>
                <input
                  className="field-input"
                  type="number"
                  min={-1}
                  placeholder={String(
                    (settings as unknown as Record<string, number>)[field.key],
                  )}
                  value={draftQuotas[field.key] ?? ""}
                  disabled={!isOwner}
                  onChange={(event) =>
                    setDraftQuotas((draft) => ({
                      ...draft,
                      [field.key]: event.target.value,
                    }))
                  }
                />
              </label>
            ))}
            {isOwner && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={Object.values(draftQuotas).every(
                  (value) => value.trim() === "",
                )}
                onClick={() => void saveQuotas()}
              >
                Save quotas
              </button>
            )}
          </section>
        )}

        {usage && usage.length > 0 && (
          <section style={{ marginTop: 24 }}>
            <h2>Usage (last {usage.length} days)</h2>
            <div className="security-users">
              {usage.map((dayRow) => (
                <div className="security-user-row" key={dayRow.day}>
                  <div>
                    <strong>{dayRow.day}</strong>
                  </div>
                  <span className="muted">{dayRow.runs} runs</span>
                  <span className="muted">{hours(dayRow.compute_seconds)} compute</span>
                  <span className="muted">{dayRow.node_runs} node executions</span>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
