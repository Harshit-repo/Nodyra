import {
  ArrowCounterClockwise,
  Buildings,
  Check,
  CreditCard,
  Eye,
  EyeSlash,
  GearSix,
  IdentificationCard,
  Info,
  Monitor,
  Moon,
  Palette,
  ShieldCheck,
  Sun,
  UsersThree,
  WarningCircle,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker } from "react-router-dom";

import { api, errorMessage } from "./api";
import { useConfirm } from "./ConfirmProvider";
import { useWorkspaceAccessContext } from "./WorkspaceAccess";
import {
  discardDirtyInstanceSettings,
  hasDirtyInstanceSettings,
  setInstanceSettingsDirty,
  useInstanceSettingsDirty,
  useInstanceSettingsDiscardRevision,
} from "./settingsDirty";
import {
  getFontPreference,
  getThemePreference,
  setFontPreference,
  setThemePreference,
  type FontPreference,
  type ThemePreference,
} from "./theme";
import type { LicenseInfo, SystemSettings } from "./types";

import "./settings.css";

type SettingsKey = keyof SystemSettings;
type DraftSettings = Record<SettingsKey, string>;

const FIELD_DOCS: Record<
  SettingsKey,
  {
    label: string;
    help: string;
    restartRequired?: boolean;
    unit?: string;
    min?: number;
    max?: number;
  }
> = {
  max_concurrent_runs: {
    label: "Maximum concurrent runs",
    help: "Ceiling on top-level runs in flight across every environment. Sub-workflows do not consume a slot.",
    unit: "runs",
    restartRequired: true,
    min: 1,
    max: 1024,
  },
  runner_idle_seconds: {
    label: "Warm worker idle timeout",
    help: "Close a warm worker after it has been idle this long. Use 0 to keep workers warm indefinitely.",
    unit: "seconds",
    min: 0,
    max: 86400,
  },
  run_retention_days: {
    label: "Run retention",
    help: "Remove runs older than this on the next prune tick. Use 0 to disable age-based pruning.",
    unit: "days",
    min: 0,
    max: 3650,
  },
  run_retention_max_per_workflow: {
    label: "Runs retained per workflow",
    help: "Keep only the most recent runs for each workflow. Use 0 for no count limit.",
    unit: "runs",
    min: 0,
    max: 100000,
  },
  max_output_bytes: {
    label: "Maximum node output",
    help: "Larger node outputs are replaced with a truncation stub before they are persisted. Use 0 for no cap.",
    unit: "bytes",
    min: 0,
    max: 10485760,
  },
  max_artifact_bytes: {
    label: "Maximum artifact size",
    help: "Artifact writes above this size are refused.",
    unit: "bytes",
    min: 0,
    max: 10737418240,
  },
  max_artifacts_per_run: {
    label: "Maximum artifacts per run",
    help: "Further artifact writes are refused after this count is reached.",
    unit: "artifacts",
    min: 0,
    max: 10000,
  },
  app_timezone: {
    label: "Default timezone",
    help: "Used by schedule triggers that do not specify a timezone. Leave blank to use the server timezone.",
    restartRequired: true,
  },
  worker_rss_soft_budget_bytes: {
    label: "Worker memory soft budget",
    help: "Advisory ceiling for total warm-worker memory in an environment burst pool. Use 0 to disable the warning.",
    unit: "bytes",
    min: 0,
    max: 10995116277760,
  },
};

const NUMERIC_KEYS: SettingsKey[] = [
  "max_concurrent_runs",
  "runner_idle_seconds",
  "run_retention_days",
  "run_retention_max_per_workflow",
  "max_output_bytes",
  "max_artifact_bytes",
  "max_artifacts_per_run",
  "worker_rss_soft_budget_bytes",
];

const SETTINGS_NAV = [
  { id: "account", label: "Account", icon: IdentificationCard },
  { id: "appearance", label: "Appearance", icon: Palette },
  { id: "instance", label: "Instance", icon: GearSix },
  { id: "license", label: "Plan & license", icon: CreditCard },
  { id: "management", label: "Management", icon: ShieldCheck },
];

function toDraft(settings: SystemSettings): DraftSettings {
  return Object.fromEntries(
    Object.keys(FIELD_DOCS).map((key) => [
      key,
      String(settings[key as SettingsKey] ?? ""),
    ]),
  ) as DraftSettings;
}

function isValidTimezone(value: string): boolean {
  if (!value.trim()) return true;
  try {
    new Intl.DateTimeFormat(undefined, { timeZone: value.trim() }).format();
    return true;
  } catch {
    return false;
  }
}

function validateDraft(draft: DraftSettings): Partial<Record<SettingsKey, string>> {
  const errors: Partial<Record<SettingsKey, string>> = {};
  for (const key of NUMERIC_KEYS) {
    const value = draft[key].trim();
    if (!/^\d+$/.test(value)) {
      errors[key] = "Enter a whole number of 0 or greater.";
      continue;
    }
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed)) {
      errors[key] = "This value is too large.";
      continue;
    }
    const meta = FIELD_DOCS[key];
    const min = meta.min ?? 0;
    const max = meta.max ?? Number.MAX_SAFE_INTEGER;
    if (parsed < min || parsed > max) {
      errors[key] = `Enter a value from ${min.toLocaleString()} to ${max.toLocaleString()}.`;
    }
  }
  if (draft.app_timezone.length > 64) {
    errors.app_timezone = "Timezone names must be 64 characters or fewer.";
  } else if (!isValidTimezone(draft.app_timezone)) {
    errors.app_timezone = "Enter a valid IANA timezone, such as Australia/Sydney.";
  }
  return errors;
}

function InfoTip({ text }: { text: string }) {
  return (
    <span className="noodle-settings-info" title={text} aria-hidden="true">
      <Info size={15} aria-hidden="true" />
    </span>
  );
}

function SettingsCard({
  id,
  title,
  description,
  icon: Icon,
  children,
  footer,
}: {
  id: string;
  title: string;
  description: string;
  icon: typeof IdentificationCard;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <section id={id} className="noodle-settings-card" aria-labelledby={`${id}-title`}>
      <div className="noodle-settings-card-head">
        <span className="noodle-settings-card-icon" aria-hidden="true">
          <Icon size={18} />
        </span>
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      <div className="noodle-settings-card-body">{children}</div>
      {footer && <div className="noodle-settings-card-footer">{footer}</div>}
    </section>
  );
}

function ProfilePanel({ workspaceRole }: { workspaceRole: string | null }) {
  const user = useWorkspaceAccessContext().user;
  const displayName = user?.name || user?.email || "Local user";
  const initials = displayName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "N";

  return (
    <SettingsCard
      id="account"
      title="Account profile"
      description="Your identity in this Noodle installation. Profile editing is not available in this version."
      icon={IdentificationCard}
    >
      <div className="noodle-settings-profile">
        <span className="noodle-settings-avatar" aria-hidden="true">{initials}</span>
        <div className="noodle-settings-profile-copy">
          <strong>{displayName}</strong>
          {user?.email && <span>{user.email}</span>}
          {user?.company && <span>{user.company}</span>}
        </div>
        {(user?.role || workspaceRole) && (
          <div className="noodle-settings-role">
            {user?.role && (
              <><span>Instance role</span><strong className={`role-pill role-${user.role}`}>{user.role}</strong></>
            )}
            {workspaceRole && (
              <><span>Current workspace role</span><strong className={`role-pill role-${workspaceRole}`}>{workspaceRole}</strong></>
            )}
          </div>
        )}
      </div>
    </SettingsCard>
  );
}

const THEME_OPTIONS: {
  value: ThemePreference;
  label: string;
  description: string;
  icon: typeof Monitor;
}[] = [
  { value: "dark", label: "Dark", description: "Low-light graphite", icon: Moon },
  { value: "system", label: "System", description: "Match your device", icon: Monitor },
  { value: "light", label: "Light", description: "Bright neutral", icon: Sun },
];

const FONT_OPTIONS: { value: FontPreference; label: string; sample: string }[] = [
  { value: "brand", label: "Brand Grotesk", sample: "Noodle workflows" },
  { value: "inter", label: "Inter", sample: "Noodle workflows" },
  { value: "technical", label: "Technical", sample: "Noodle workflows" },
  { value: "system", label: "System UI", sample: "Noodle workflows" },
];

function AppearancePanel() {
  const [theme, setTheme] = useState<ThemePreference>(() => getThemePreference());
  const [font, setFont] = useState<FontPreference>(() => getFontPreference());
  const [saveState, setSaveState] = useState<"idle" | "saved" | "session">("idle");

  function markSaved(persisted: boolean) {
    setSaveState(persisted ? "saved" : "session");
    window.setTimeout(() => setSaveState("idle"), 2400);
  }

  function updateTheme(value: ThemePreference): void {
    setTheme(value);
    markSaved(setThemePreference(value));
  }

  function updateFont(value: FontPreference): void {
    setFont(value);
    markSaved(setFontPreference(value));
  }

  return (
    <SettingsCard
      id="appearance"
      title="Appearance"
      description="Personal preferences stored in this browser. Changes apply immediately."
      icon={Palette}
      footer={
        <span className="noodle-settings-save-status" role="status" aria-live="polite">
          {saveState === "saved" && <><Check size={14} aria-hidden="true" />Saved to this browser</>}
          {saveState === "session" && <><WarningCircle size={14} aria-hidden="true" />Applied for this session; browser storage is unavailable</>}
        </span>
      }
    >
      <fieldset className="noodle-settings-fieldset">
        <legend>Theme</legend>
        <div className="noodle-theme-grid">
          {THEME_OPTIONS.map((option) => {
            const Icon = option.icon;
            return (
              <label
                key={option.value}
                className={`noodle-theme-option${theme === option.value ? " is-selected" : ""}`}
              >
                <input
                  type="radio"
                  name="theme"
                  value={option.value}
                  checked={theme === option.value}
                  onChange={() => updateTheme(option.value)}
                />
                <span className={`noodle-theme-preview theme-${option.value}`}>
                  <Icon size={21} aria-hidden="true" />
                  <span className="noodle-theme-preview-layout" aria-hidden="true">
                    <i /><i /><i />
                  </span>
                </span>
                <strong>{option.label}</strong>
                <small>{option.description}</small>
                {theme === option.value && <Check className="noodle-theme-check" size={15} aria-hidden="true" />}
              </label>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="noodle-settings-fieldset">
        <legend>Typeface</legend>
        <div className="noodle-font-grid">
          {FONT_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={`noodle-font-option font-${option.value}${font === option.value ? " is-selected" : ""}`}
            >
              <input
                type="radio"
                name="font"
                value={option.value}
                checked={font === option.value}
                onChange={() => updateFont(option.value)}
              />
              <span>{option.sample}</span>
              <small>{option.label}</small>
            </label>
          ))}
        </div>
      </fieldset>
    </SettingsCard>
  );
}

function WorkspaceSettingsPanel() {
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [draft, setDraft] = useState<DraftSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const discardRevision = useInstanceSettingsDiscardRevision();
  const lastDiscardRevisionRef = useRef(discardRevision);

  const load = () => {
    setError("");
    api
      .getSystemSettings()
      .then((value) => {
        setSettings(value);
        setDraft(toDraft(value));
      })
      .catch((err) => setError(errorMessage(err)));
  };

  useEffect(load, []);

  const isDirty = useMemo(() => {
    if (!settings || !draft) return false;
    const baseline = toDraft(settings);
    return (Object.keys(FIELD_DOCS) as SettingsKey[]).some(
      (key) => baseline[key] !== draft[key],
    );
  }, [draft, settings]);

  const validationErrors = useMemo(
    () => (draft ? validateDraft(draft) : {}),
    [draft],
  );
  const isValid = Object.keys(validationErrors).length === 0;
  useEffect(() => {
    if (lastDiscardRevisionRef.current === discardRevision) return;
    lastDiscardRevisionRef.current = discardRevision;
    if (settings) {
      setDraft(toDraft(settings));
      setError("");
      setSavedAt(null);
    }
  }, [discardRevision, settings]);

  useEffect(() => {
    setInstanceSettingsDirty(isDirty, "instance");
    return () => {
      setInstanceSettingsDirty(false, "instance");
    };
  }, [isDirty]);

  function update(key: SettingsKey, value: string): void {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
    setSavedAt(null);
  }

  function reset(): void {
    if (!settings) return;
    setDraft(toDraft(settings));
    setError("");
  }

  async function save(): Promise<void> {
    if (busy || !settings || !draft || !isDirty || !isValid) return;
    setBusy(true);
    setError("");
    try {
      const payload = Object.fromEntries(
        (Object.keys(FIELD_DOCS) as SettingsKey[]).map((key) => [
          key,
          key === "app_timezone" ? draft[key].trim() : Number(draft[key]),
        ]),
      ) as unknown as Partial<SystemSettings>;
      const next = await api.updateSystemSettings(payload);
      setSettings(next);
      setDraft(toDraft(next));
      setSavedAt(Date.now());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!settings || !draft) {
    return (
      <SettingsCard
        id="instance"
        title="Instance settings"
        description="Runtime, retention, and storage limits for this Noodle installation."
        icon={GearSix}
      >
        {error ? (
          <div className="noodle-settings-inline-error" role="alert">
            <WarningCircle size={18} aria-hidden="true" />
            <span>{error}</span>
            <button className="btn btn-sm" type="button" onClick={load}>Retry</button>
          </div>
        ) : (
          <div className="noodle-settings-skeleton" aria-label="Loading instance settings">
            <span /><span /><span />
          </div>
        )}
      </SettingsCard>
    );
  }

  return (
    <SettingsCard
      id="instance"
      title="Instance settings"
      description="Runtime, retention, and storage limits for this Noodle installation. Admin changes affect every workspace."
      icon={GearSix}
      footer={
        <>
          <div className="noodle-settings-footer-status" aria-live="polite">
            {error ? (
              <span className="error-text"><WarningCircle size={15} aria-hidden="true" />{error}</span>
            ) : savedAt ? (
              <span className="noodle-settings-saved"><Check size={15} aria-hidden="true" />Saved successfully</span>
            ) : isDirty ? (
              <span>Unsaved changes</span>
            ) : (
              <span>No pending changes</span>
            )}
          </div>
          <div className="settings-actions">
            <button className="btn" type="button" onClick={reset} disabled={busy || !isDirty}>
              <ArrowCounterClockwise size={15} aria-hidden="true" />Reset
            </button>
            <button
              className="btn btn-primary"
              type="button"
              onClick={() => void save()}
              disabled={busy || !isDirty || !isValid}
            >
              {busy ? "Saving…" : "Save instance settings"}
            </button>
          </div>
        </>
      }
    >
      <fieldset className="noodle-settings-instance-fields" disabled={busy}>
      <div className="noodle-settings-restart-note">
        <Info size={17} aria-hidden="true" />
        <span>Fields marked <strong>Restart required</strong> apply after the next API restart. Other changes apply on the next runtime tick.</span>
      </div>
      <div className="noodle-settings-form-grid">
        {NUMERIC_KEYS.map((key) => {
          const meta = FIELD_DOCS[key];
          const fieldError = validationErrors[key];
          return (
            <label key={key} className="noodle-settings-field">
              <span className="noodle-settings-label">
                {meta.label}
                <InfoTip text={meta.help} />
                {meta.restartRequired && <span className="settings-tag">Restart required</span>}
              </span>
              <span className="noodle-settings-input-wrap">
                <input
                  className={`field-input${fieldError ? " field-invalid" : ""}`}
                  type="number"
                  inputMode="numeric"
                  min={meta.min ?? 0}
                  max={meta.max}
                  step={1}
                  value={draft[key]}
                  aria-invalid={Boolean(fieldError)}
                  aria-describedby={fieldError ? `${key}-error` : `${key}-help`}
                  onChange={(event) => update(key, event.target.value)}
                />
                {meta.unit && <span className="noodle-settings-unit">{meta.unit}</span>}
              </span>
              {fieldError ? (
                <small id={`${key}-error`} className="noodle-settings-field-error">{fieldError}</small>
              ) : (
                <small id={`${key}-help`}>{meta.help}</small>
              )}
            </label>
          );
        })}
        <label className="noodle-settings-field noodle-settings-field-wide">
          <span className="noodle-settings-label">
            {FIELD_DOCS.app_timezone.label}
            <InfoTip text={FIELD_DOCS.app_timezone.help} />
            <span className="settings-tag">Restart required</span>
          </span>
          <input
            className={`field-input${validationErrors.app_timezone ? " field-invalid" : ""}`}
            type="text"
            autoComplete="off"
            placeholder="Australia/Sydney"
            value={draft.app_timezone}
            aria-invalid={Boolean(validationErrors.app_timezone)}
            aria-describedby={validationErrors.app_timezone ? "app_timezone-error" : "app_timezone-help"}
            onChange={(event) => update("app_timezone", event.target.value)}
          />
          {validationErrors.app_timezone ? (
            <small id="app_timezone-error" className="noodle-settings-field-error">{validationErrors.app_timezone}</small>
          ) : (
            <small id="app_timezone-help">Use an IANA timezone. Leave blank to use the server timezone.</small>
          )}
        </label>
      </div>
      </fieldset>
    </SettingsCard>
  );
}

function LicensePanel() {
  const [info, setInfo] = useState<LicenseInfo | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revealKey, setRevealKey] = useState(false);
  const discardRevision = useInstanceSettingsDiscardRevision();
  const lastDiscardRevisionRef = useRef(discardRevision);
  const confirm = useConfirm();

  const load = () => {
    setError("");
    api.getLicense().then(setInfo).catch((err) => setError(errorMessage(err)));
  };

  useEffect(load, []);

  useEffect(() => {
    setInstanceSettingsDirty(Boolean(keyDraft.trim()), "license");
    return () => setInstanceSettingsDirty(false, "license");
  }, [keyDraft]);

  useEffect(() => {
    if (lastDiscardRevisionRef.current === discardRevision) return;
    lastDiscardRevisionRef.current = discardRevision;
    setKeyDraft("");
    setRevealKey(false);
    setError("");
  }, [discardRevision]);

  async function apply(): Promise<void> {
    if (busy || !keyDraft.trim()) return;
    setBusy(true);
    setError("");
    try {
      const next = await api.applyLicense(keyDraft.trim());
      setInfo(next);
      setKeyDraft("");
      window.location.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    if (busy) return;
    const ok = await confirm({
      title: "Remove this license?",
      body: "This installation will return to the Community edition and its resource caps.",
      confirmLabel: "Remove license",
    });
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      const next = await api.removeLicense();
      setInfo(next);
      window.location.reload();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const formatLimit = (value: number | undefined) =>
    value === undefined ? "—" : value === 0 ? "Unlimited" : value.toLocaleString();

  return (
    <SettingsCard
      id="license"
      title="Plan & license"
      description="Edition, installation limits, and self-hosted license management."
      icon={CreditCard}
    >
      {!info ? (
        error ? (
          <div className="noodle-settings-inline-error" role="alert">
            <WarningCircle size={18} aria-hidden="true" />
            <span>{error}</span>
            <button className="btn btn-sm" type="button" onClick={load}>Retry</button>
          </div>
        ) : (
          <div className="noodle-settings-skeleton" aria-label="Loading plan and license">
            <span /><span /><span />
          </div>
        )
      ) : (
        <>
          <div className="noodle-license-summary">
            <div className="noodle-license-edition">
              <span>Current edition</span>
              <strong>{info.edition}</strong>
              {info.customer && <small>{info.customer}</small>}
            </div>
            <dl>
              <div><dt>Environments</dt><dd>{formatLimit(info.limits.environments)}</dd></div>
              <div><dt>Runners</dt><dd>{formatLimit(info.limits.runners)}</dd></div>
              <div><dt>Deployments</dt><dd>{formatLimit(info.limits.deployments)}</dd></div>
              <div><dt>Seats</dt><dd>{formatLimit(info.limits.seats)}</dd></div>
            </dl>
          </div>
          {info.expires_at && (
            <p className="noodle-license-expiry">Expires {new Date(info.expires_at * 1000).toLocaleDateString()}</p>
          )}
          {info.notice && <div className="noodle-settings-inline-warning"><WarningCircle size={17} aria-hidden="true" />{info.notice}</div>}
          <div className="noodle-license-apply">
            <label className="noodle-settings-field noodle-settings-field-wide">
              <span className="noodle-settings-label">Apply a Pro or Enterprise license key</span>
              <span className="noodle-license-key-control">
                <input
                  className="field-input"
                  type={revealKey ? "text" : "password"}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="Paste license key"
                  value={keyDraft}
                  onChange={(event) => setKeyDraft(event.target.value)}
                />
                <button
                  type="button"
                  aria-label={revealKey ? "Hide license key" : "Show license key"}
                  aria-pressed={revealKey}
                  onClick={() => setRevealKey((value) => !value)}
                >
                  {revealKey ? <EyeSlash size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
                </button>
              </span>
              <small>The key is sent securely to this installation and is never stored in your browser.</small>
            </label>
            {error && <p className="error-text" role="alert">{error}</p>}
            <div className="settings-actions">
              <button className="btn btn-primary" type="button" onClick={() => void apply()} disabled={busy || !keyDraft.trim()}>
                {busy ? "Applying…" : "Apply license"}
              </button>
              {info.edition !== "community" && (
                <button className="btn btn-danger" type="button" onClick={() => void remove()} disabled={busy}>
                  Remove license
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </SettingsCard>
  );
}

function ManagementPanel({
  canAdmin,
  canWorkspaceManage,
  canAudit,
}: {
  canAdmin: boolean;
  canWorkspaceManage: boolean;
  canAudit: boolean;
}) {
  const canManageAnything = canAdmin || canWorkspaceManage || canAudit;
  return (
    <SettingsCard
      id="management"
      title="Management"
      description={canManageAnything ? "Manage the scopes available to your current roles." : "Your role does not include workspace or instance management permissions."}
      icon={UsersThree}
    >
      {canManageAnything ? (
        <div className="noodle-management-links">
          {canWorkspaceManage && <Link to="/organization"><Buildings size={18} aria-hidden="true" /><span><strong>Workspace</strong><small>Members, roles, quotas, and usage</small></span></Link>}
          {canAdmin && <Link to="/security"><ShieldCheck size={18} aria-hidden="true" /><span><strong>Instance access</strong><small>Users and installation-wide roles</small></span></Link>}
          {canAudit && <Link to="/activity"><GearSix size={18} aria-hidden="true" /><span><strong>Activity log</strong><small>Review workspace administrative changes</small></span></Link>}
        </div>
      ) : (
        <div className="noodle-settings-inline-warning"><Info size={17} aria-hidden="true" />Ask an instance admin to change access or runtime settings.</div>
      )}
    </SettingsCard>
  );
}

export function SettingsPage() {
  const workspace = useWorkspaceAccessContext();
  const user = workspace.user;
  const workspaceRole = workspace.role;
  const canAdmin = !workspace.authRequired || user?.role === "admin" || user?.role === "owner";
  const canWorkspaceManage = workspace.multiTenancyEnabled && (workspaceRole === "admin" || workspaceRole === "owner");
  const canAudit = workspace.multiTenancyEnabled ? canWorkspaceManage : canAdmin;
  const visibleNavigation = SETTINGS_NAV.filter(
    (item) => canAdmin || !["instance", "license"].includes(item.id),
  );
  const [activeSection, setActiveSection] = useState(() => {
    const hash = window.location.hash.slice(1);
    return visibleNavigation.some((item) => item.id === hash)
      ? hash
      : visibleNavigation[0]?.id ?? "profile";
  });
  const settingsDirty = useInstanceSettingsDirty();
  const confirm = useConfirm();
  const resolvingBlockRef = useRef(false);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      settingsDirty && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasDirtyInstanceSettings()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  useEffect(() => {
    if (blocker.state !== "blocked" || resolvingBlockRef.current) return;
    resolvingBlockRef.current = true;
    void confirm({
      title: "Discard unsaved settings?",
      body: "Your instance or license changes have not been saved.",
      confirmLabel: "Discard changes",
    })
      .then((discard) => {
        if (discard) {
          discardDirtyInstanceSettings();
          blocker.proceed();
        } else {
          blocker.reset();
        }
      })
      .finally(() => {
        resolvingBlockRef.current = false;
      });
  }, [blocker, confirm]);

  useEffect(() => {
    if (!("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible?.target.id) setActiveSection(visible.target.id);
      },
      { rootMargin: "-18% 0px -68% 0px", threshold: [0, 0.1, 0.5] },
    );
    visibleNavigation.forEach((item) => {
      const section = document.getElementById(item.id);
      if (section) observer.observe(section);
    });
    return () => observer.disconnect();
  }, [canAdmin]);

  return (
    <div className="home noodle-settings-page">
      <main className="home-main">
        <div className="home-bar noodle-settings-heading">
          <div>
            <p className="noodle-settings-eyebrow">Personal and installation preferences</p>
            <h1>Settings</h1>
            <p className="muted">Account, appearance, instance controls, and plan information.</p>
          </div>
        </div>

        <div className="noodle-settings-layout">
          <nav className="noodle-settings-nav" aria-label="Settings sections">
            {visibleNavigation.map((item) => {
              const Icon = item.icon;
              return (
                <a
                  key={item.id}
                  href={`#${item.id}`}
                  className={activeSection === item.id ? "is-active" : ""}
                  aria-current={activeSection === item.id ? "location" : undefined}
                  onClick={() => setActiveSection(item.id)}
                >
                  <Icon size={17} aria-hidden="true" />
                  {item.label}
                </a>
              );
            })}
          </nav>

          <div className="noodle-settings-content">
            <ProfilePanel workspaceRole={workspaceRole} />
            <AppearancePanel />
            {canAdmin && <WorkspaceSettingsPanel />}
            {canAdmin && <LicensePanel />}
            <ManagementPanel canAdmin={canAdmin} canWorkspaceManage={canWorkspaceManage} canAudit={canAudit} />
          </div>
        </div>
      </main>
    </div>
  );
}
