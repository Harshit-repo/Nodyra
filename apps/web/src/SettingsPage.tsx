import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, getUser } from "./api";
import { HomeHeader } from "./HomeHeader";
import {
  getFontPreference,
  getThemePreference,
  setFontPreference,
  setThemePreference,
  type FontPreference,
  type ThemePreference,
} from "./theme";
import type { SystemSettings } from "./types";

const FIELD_DOCS: Record<
  keyof SystemSettings,
  { label: string; help: string; restartRequired?: boolean; unit?: string }
> = {
  max_concurrent_runs: {
    label: "Max concurrent runs (global)",
    help: "Ceiling on top-level runs in flight at once across every environment. Sub-workflows do not consume a slot.",
    unit: "runs",
    restartRequired: true,
  },
  runner_idle_seconds: {
    label: "Warm worker idle reap (sec)",
    help: "Close a warm worker process after it has been idle this many seconds. 0 disables reaping (workers stay warm forever).",
    unit: "sec",
  },
  run_retention_days: {
    label: "Run retention (days)",
    help: "Drop runs older than this many days on the next prune tick. 0 disables age-based pruning.",
    unit: "days",
  },
  run_retention_max_per_workflow: {
    label: "Max runs kept per workflow",
    help: "Keep only the N most recent runs per workflow. 0 means unlimited (age rule alone).",
    unit: "runs",
  },
  max_output_bytes: {
    label: "Max per-node output (bytes)",
    help: "Bigger node outputs are replaced with a small truncation stub before being persisted. 0 disables capping.",
    unit: "bytes",
  },
  max_artifact_bytes: {
    label: "Max artifact size (bytes)",
    help: "Refuse writes that would exceed this many bytes per artifact.",
    unit: "bytes",
  },
  max_artifacts_per_run: {
    label: "Max artifacts per run",
    help: "Refuse further artifact writes once a run has produced this many.",
    unit: "artifacts",
  },
  app_timezone: {
    label: "Default timezone",
    help: "Used by schedule_trigger nodes when they don't specify their own tz. IANA name (e.g. Australia/Sydney). Blank = server local.",
    restartRequired: true,
  },
  worker_rss_soft_budget_bytes: {
    label: "Worker memory soft budget (bytes)",
    help: "Soft ceiling for total warm-worker RAM across an env's burst pool. When an env's chosen max would exceed this, the env-edit modal shows a warning. Advisory only — workers can still spawn. 0 disables the warning.",
    unit: "bytes",
  },
};

function InfoTip({ text }: { text: string }) {
  return (
    <span className="info-tip" title={text} aria-label={text}>
      ⓘ
    </span>
  );
}

function WorkspaceSettingsPanel() {
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [draft, setDraft] = useState<Partial<SystemSettings>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    api
      .getSystemSettings()
      .then((value) => {
        setSettings(value);
        setDraft(value);
      })
      .catch((err) => setError(String(err)));
  }, []);

  function update<K extends keyof SystemSettings>(
    key: K,
    value: SystemSettings[K],
  ): void {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function save() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const next = await api.updateSystemSettings(draft);
      setSettings(next);
      setDraft(next);
      setSavedAt(Date.now());
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!settings) {
    return (
      <div className="settings-panel">
        <h2>Workspace</h2>
        {error ? (
          <p className="error-text">{error}</p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </div>
    );
  }

  const numericKeys: (keyof SystemSettings)[] = [
    "max_concurrent_runs",
    "runner_idle_seconds",
    "run_retention_days",
    "run_retention_max_per_workflow",
    "max_output_bytes",
    "max_artifact_bytes",
    "max_artifacts_per_run",
  ];

  return (
    <div className="settings-panel">
      <h2>Workspace</h2>
      <p className="muted">
        Runtime limits and retention rules applied across every workflow.
        Hot-reload settings take effect on the next tick; ones marked “restart
        required” apply at next API restart.
      </p>

      {numericKeys.map((key) => {
        const meta = FIELD_DOCS[key];
        const value = (draft[key] ?? settings[key]) as number;
        return (
          <label key={key} className="settings-field">
            <span>
              {meta.label} <InfoTip text={meta.help} />
              {meta.restartRequired && (
                <span className="settings-tag">restart</span>
              )}
            </span>
            <input
              className="field-input"
              type="number"
              min={0}
              value={value}
              onChange={(e) =>
                update(key, Math.max(0, Number(e.target.value)) as never)
              }
            />
          </label>
        );
      })}

      <label className="settings-field">
        <span>
          {FIELD_DOCS.app_timezone.label}{" "}
          <InfoTip text={FIELD_DOCS.app_timezone.help} />
          <span className="settings-tag">restart</span>
        </span>
        <input
          className="field-input"
          type="text"
          placeholder="Australia/Sydney"
          value={(draft.app_timezone ?? settings.app_timezone) || ""}
          onChange={(e) => update("app_timezone", e.target.value)}
        />
      </label>

      {error && <p className="error-text">{error}</p>}

      <div className="settings-actions">
        <button
          className="btn btn-primary"
          onClick={() => void save()}
          disabled={busy}
        >
          {busy ? "Saving…" : "Save workspace settings"}
        </button>
        {savedAt && !busy && <span className="muted">Saved.</span>}
      </div>
    </div>
  );
}

export function SettingsPage() {
  const user = getUser();
  const canAdmin = user?.role === "admin" || user?.role === "owner";
  const [theme, setTheme] = useState<ThemePreference>(() => getThemePreference());
  const [font, setFont] = useState<FontPreference>(() => getFontPreference());

  function updateTheme(value: ThemePreference): void {
    setTheme(value);
    setThemePreference(value);
  }

  function updateFont(value: FontPreference): void {
    setFont(value);
    setFontPreference(value);
  }

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <div>
            <h1>Settings</h1>
            <p className="muted">Profile, workspace, and access.</p>
          </div>
        </div>

        <section className="settings-grid">
          <div className="settings-panel">
            <h2>Profile</h2>
            <dl className="settings-list">
              <div>
                <dt>Name</dt>
                <dd>{user?.name || "Not set"}</dd>
              </div>
              <div>
                <dt>Company</dt>
                <dd>{user?.company || "Not set"}</dd>
              </div>
              <div>
                <dt>Email</dt>
                <dd>{user?.email}</dd>
              </div>
              <div>
                <dt>Role</dt>
                <dd>
                  <span className={`role-pill role-${user?.role}`}>
                    {user?.role}
                  </span>
                </dd>
              </div>
            </dl>
          </div>

          <div className="settings-panel">
            <h2>Appearance</h2>
            <label className="settings-field">
              <span>Theme</span>
              <select
                className="field-input"
                value={theme}
                onChange={(event) =>
                  updateTheme(event.target.value as ThemePreference)
                }
              >
                <option value="system">System</option>
                <option value="dark">Dark</option>
                <option value="light">Light</option>
              </select>
            </label>
            <label className="settings-field">
              <span>Typeface</span>
              <select
                className="field-input"
                value={font}
                onChange={(event) =>
                  updateFont(event.target.value as FontPreference)
                }
              >
                <option value="brand">Brand Grotesk</option>
                <option value="inter">Inter</option>
                <option value="technical">Technical</option>
                <option value="system">System UI</option>
              </select>
            </label>
            <p className="muted">
              System follows your browser or operating system preference.
            </p>
          </div>

          {canAdmin && <WorkspaceSettingsPanel />}

          <div className="settings-panel">
            <h2>Admin</h2>
            <p className="muted">
              Admin users can invite teammates, manage credentials, and review
              activity.
            </p>
            <div className="settings-actions">
              {canAdmin ? (
                <>
                  <Link className="btn" to="/security">
                    Manage users
                  </Link>
                  <Link className="btn" to="/activity">
                    Activity log
                  </Link>
                </>
              ) : (
                <span className="muted">Ask an admin to change access.</span>
              )}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
