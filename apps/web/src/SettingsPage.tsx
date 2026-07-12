import {
  ArrowCounterClockwise,
  Buildings,
  Check,
  Copy,
  CreditCard,
  Eye,
  EyeSlash,
  GearSix,
  IdentificationCard,
  Info,
  Key,
  LockKey,
  Monitor,
  Moon,
  Palette,
  Plug,
  ShieldCheck,
  Sun,
  Trash,
  UsersThree,
  WarningCircle,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker } from "react-router-dom";

import { api, errorMessage, type SandboxStatus } from "./api";
import { useConfirm } from "./ConfirmProvider";
import { ReadinessPanel } from "./ReadinessPanel";
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
import type {
  ApiTokenCreated,
  ApiTokenInfo,
  ApiTokenScopeInfo,
  LicenseInfo,
  SystemSettings,
} from "./types";

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
  { id: "mcp-access", label: "MCP access", icon: Key },
  { id: "readiness", label: "Readiness", icon: WarningCircle },
  { id: "instance", label: "Instance", icon: GearSix },
  { id: "sandbox", label: "Sandbox", icon: LockKey },
  { id: "license", label: "Plan & license", icon: CreditCard },
  { id: "management", label: "Management", icon: ShieldCheck },
];

const MCP_ACCESS_SCOPE_ORDER = [
  "workflow:read",
  "workflow:write",
  "workflow:run",
  "workflow:publish",
  "credential:read",
  "deployment:write",
  "environment:write",
  "mcp_connection:manage",
];

const RECOMMENDED_MCP_SCOPES = ["workflow:read", "workflow:write", "workflow:run"];

const TOKEN_SCOPE_COPY: Record<string, { label: string; description: string }> = {
  "workflow:read": {
    label: "Read workflows",
    description: "List workflows and inspect graph structure.",
  },
  "workflow:write": {
    label: "Edit workflows",
    description: "Create workflows and change nodes, edges, and settings.",
  },
  "workflow:run": {
    label: "Run workflows",
    description: "Start workflow runs and invoke published workflow tools.",
  },
  "workflow:publish": {
    label: "Publish workflows",
    description: "Publish draft workflow versions for deployments and tools.",
  },
  "deployment:write": {
    label: "Manage deployments",
    description: "Create or update schedules and deployment settings.",
  },
  "credential:read": {
    label: "Read credential metadata",
    description: "List credential names and IDs without exposing secret values.",
  },
  "credential:read_values": {
    label: "Read credential secrets",
    description: "Decrypt stored credential values. Grant only to trusted automation.",
  },
  "credential:write": {
    label: "Manage credentials",
    description: "Create, update, or delete stored credentials.",
  },
  "environment:write": {
    label: "Manage environments",
    description: "Change execution environments and packages.",
  },
  "runner_pool:write": {
    label: "Manage runner pools",
    description: "Change remote execution pool settings.",
  },
  "mcp_connection:manage": {
    label: "Manage MCP connections",
    description: "Create and change outbound MCP server connections.",
  },
  "audit:read": {
    label: "Read audit log",
    description: "Inspect administrative and security audit events.",
  },
};

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
    <span className="nodyra-settings-info" title={text} aria-hidden="true">
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
    <section id={id} className="nodyra-settings-card" aria-labelledby={`${id}-title`}>
      <div className="nodyra-settings-card-head">
        <span className="nodyra-settings-card-icon" aria-hidden="true">
          <Icon size={18} />
        </span>
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      <div className="nodyra-settings-card-body">{children}</div>
      {footer && <div className="nodyra-settings-card-footer">{footer}</div>}
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
      description="Your identity in this Nodyra installation. Profile editing is not available in this version."
      icon={IdentificationCard}
    >
      <div className="nodyra-settings-profile">
        <span className="nodyra-settings-avatar" aria-hidden="true">{initials}</span>
        <div className="nodyra-settings-profile-copy">
          <strong>{displayName}</strong>
          {user?.email && <span>{user.email}</span>}
          {user?.company && <span>{user.company}</span>}
        </div>
        {(user?.role || workspaceRole) && (
          <div className="nodyra-settings-role">
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
  { value: "brand", label: "Brand Grotesk", sample: "Nodyra workflows" },
  { value: "inter", label: "Inter", sample: "Nodyra workflows" },
  { value: "technical", label: "Technical", sample: "Nodyra workflows" },
  { value: "system", label: "System UI", sample: "Nodyra workflows" },
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
        <span className="nodyra-settings-save-status" role="status" aria-live="polite">
          {saveState === "saved" && <><Check size={14} aria-hidden="true" />Saved to this browser</>}
          {saveState === "session" && <><WarningCircle size={14} aria-hidden="true" />Applied for this session; browser storage is unavailable</>}
        </span>
      }
    >
      <fieldset className="nodyra-settings-fieldset">
        <legend>Theme</legend>
        <div className="nodyra-theme-grid">
          {THEME_OPTIONS.map((option) => {
            const Icon = option.icon;
            return (
              <label
                key={option.value}
                className={`nodyra-theme-option${theme === option.value ? " is-selected" : ""}`}
              >
                <input
                  type="radio"
                  name="theme"
                  value={option.value}
                  checked={theme === option.value}
                  onChange={() => updateTheme(option.value)}
                />
                <span className={`nodyra-theme-preview theme-${option.value}`}>
                  <Icon size={21} aria-hidden="true" />
                  <span className="nodyra-theme-preview-layout" aria-hidden="true">
                    <i /><i /><i />
                  </span>
                </span>
                <strong>{option.label}</strong>
                <small>{option.description}</small>
                {theme === option.value && <Check className="nodyra-theme-check" size={15} aria-hidden="true" />}
              </label>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="nodyra-settings-fieldset">
        <legend>Typeface</legend>
        <div className="nodyra-font-grid">
          {FONT_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={`nodyra-font-option font-${option.value}${font === option.value ? " is-selected" : ""}`}
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
        description="Runtime, retention, and storage limits for this Nodyra installation."
        icon={GearSix}
      >
        {error ? (
          <div className="nodyra-settings-inline-error" role="alert">
            <WarningCircle size={18} aria-hidden="true" />
            <span>{error}</span>
            <button className="btn btn-sm" type="button" onClick={load}>Retry</button>
          </div>
        ) : (
          <div className="nodyra-settings-skeleton" aria-label="Loading instance settings">
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
      description="Runtime, retention, and storage limits for this Nodyra installation. Admin changes affect every workspace."
      icon={GearSix}
      footer={
        <>
          <div className="nodyra-settings-footer-status" aria-live="polite">
            {error ? (
              <span className="error-text"><WarningCircle size={15} aria-hidden="true" />{error}</span>
            ) : savedAt ? (
              <span className="nodyra-settings-saved"><Check size={15} aria-hidden="true" />Saved successfully</span>
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
      <fieldset className="nodyra-settings-instance-fields" disabled={busy}>
      <div className="nodyra-settings-restart-note">
        <Info size={17} aria-hidden="true" />
        <span>Fields marked <strong>Restart required</strong> apply after the next API restart. Other changes apply on the next runtime tick.</span>
      </div>
      <div className="nodyra-settings-form-grid">
        {NUMERIC_KEYS.map((key) => {
          const meta = FIELD_DOCS[key];
          const fieldError = validationErrors[key];
          return (
            <label key={key} className="nodyra-settings-field">
              <span className="nodyra-settings-label">
                {meta.label}
                <InfoTip text={meta.help} />
                {meta.restartRequired && <span className="settings-tag">Restart required</span>}
              </span>
              <span className="nodyra-settings-input-wrap">
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
                {meta.unit && <span className="nodyra-settings-unit">{meta.unit}</span>}
              </span>
              {fieldError ? (
                <small id={`${key}-error`} className="nodyra-settings-field-error">{fieldError}</small>
              ) : (
                <small id={`${key}-help`}>{meta.help}</small>
              )}
            </label>
          );
        })}
        <label className="nodyra-settings-field nodyra-settings-field-wide">
          <span className="nodyra-settings-label">
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
            <small id="app_timezone-error" className="nodyra-settings-field-error">{validationErrors.app_timezone}</small>
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
          <div className="nodyra-settings-inline-error" role="alert">
            <WarningCircle size={18} aria-hidden="true" />
            <span>{error}</span>
            <button className="btn btn-sm" type="button" onClick={load}>Retry</button>
          </div>
        ) : (
          <div className="nodyra-settings-skeleton" aria-label="Loading plan and license">
            <span /><span /><span />
          </div>
        )
      ) : (
        <>
          <div className="nodyra-license-summary">
            <div className="nodyra-license-edition">
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
            <p className="nodyra-license-expiry">Expires {new Date(info.expires_at * 1000).toLocaleDateString()}</p>
          )}
          {info.notice && <div className="nodyra-settings-inline-warning"><WarningCircle size={17} aria-hidden="true" />{info.notice}</div>}
          <div className="nodyra-license-apply">
            <label className="nodyra-settings-field nodyra-settings-field-wide">
              <span className="nodyra-settings-label">Apply a Pro or Enterprise license key</span>
              <span className="nodyra-license-key-control">
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

function formatTokenDate(value: string | null): string {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function tokenStatus(token: ApiTokenInfo): { label: string; className: string } {
  if (token.revoked_at) return { label: "Revoked", className: "is-revoked" };
  if (token.expires_at && new Date(token.expires_at).getTime() <= Date.now()) {
    return { label: "Expired", className: "is-expired" };
  }
  return { label: "Active", className: "is-active" };
}

function scopeCopy(scope: string): { label: string; description: string } {
  return TOKEN_SCOPE_COPY[scope] ?? {
    label: scope.replace(/[_:]/g, " "),
    description: "Allows this permission when the caller's workspace role can grant it.",
  };
}

function McpAccessPanel() {
  const workspace = useWorkspaceAccessContext();
  const confirm = useConfirm();
  const [tokens, setTokens] = useState<ApiTokenInfo[]>([]);
  const [scopes, setScopes] = useState<ApiTokenScopeInfo[]>([]);
  const [selectedScopes, setSelectedScopes] = useState<string[]>(RECOMMENDED_MCP_SCOPES);
  const [name, setName] = useState("LLM workflow builder");
  const [expiresInDays, setExpiresInDays] = useState("90");
  const [createdToken, setCreatedToken] = useState<ApiTokenCreated | null>(null);
  const [revealCreatedToken, setRevealCreatedToken] = useState(false);
  const [copied, setCopied] = useState<"endpoint" | "token" | "headers" | "config" | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const mcpEndpoint = `${window.location.origin}/mcp`;
  const mcpHeaderSnippet = `Authorization: Bearer <paste-token-here>`;
  const mcpJsonConfig = JSON.stringify(
    {
      mcpServers: {
        nodyra: {
          url: mcpEndpoint,
          headers: {
            Authorization: "Bearer <paste-token-here>",
          },
        },
      },
    },
    null,
    2,
  );
  const workspaceLabel = workspace.multiTenancyEnabled
    ? workspace.current?.name ?? "No workspace selected"
    : "Single-tenant installation";
  const canLoad = Boolean(workspace.user) && workspace.hasActiveWorkspace;

  const orderedScopes = useMemo(() => {
    const rank = (scope: string) => {
      const accessIndex = MCP_ACCESS_SCOPE_ORDER.indexOf(scope);
      return accessIndex >= 0 ? accessIndex : MCP_ACCESS_SCOPE_ORDER.length;
    };
    return scopes
      .filter((scope) => MCP_ACCESS_SCOPE_ORDER.includes(scope.scope))
      .sort((a, b) => rank(a.scope) - rank(b.scope) || a.scope.localeCompare(b.scope));
  }, [scopes]);

  async function load(): Promise<void> {
    if (!canLoad) return;
    setLoading(true);
    setError("");
    try {
      const [tokenRows, scopeRows] = await Promise.all([
        api.listApiTokens(),
        api.listApiTokenScopes(),
      ]);
      setTokens(tokenRows);
      setScopes(scopeRows);
      const grantable = new Set(
        scopeRows
          .filter((scope) => scope.grantable && MCP_ACCESS_SCOPE_ORDER.includes(scope.scope))
          .map((scope) => scope.scope),
      );
      setSelectedScopes((current) => {
        const filtered = current.filter((scope) => grantable.has(scope));
        if (filtered.length) return filtered;
        return RECOMMENDED_MCP_SCOPES.filter((scope) => grantable.has(scope));
      });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setCreatedToken(null);
    void load();
  }, [workspace.user?.id, workspace.current?.id, workspace.hasActiveWorkspace]);

  function toggleScope(scope: string): void {
    setSelectedScopes((current) =>
      current.includes(scope)
        ? current.filter((item) => item !== scope)
        : [...current, scope],
    );
    setCreatedToken(null);
  }

  async function copyValue(
    kind: "endpoint" | "token" | "headers" | "config",
    value: string,
  ): Promise<void> {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1800);
    } catch {
      setError("Clipboard access is unavailable in this browser.");
    }
  }

  async function createToken(): Promise<void> {
    if (busy) return;
    const cleanedName = name.trim();
    const days = Number(expiresInDays);
    if (!cleanedName) {
      setError("Enter a token name.");
      return;
    }
    if (!Number.isInteger(days) || days < 1 || days > 365) {
      setError("Token expiry must be a whole number from 1 to 365 days.");
      return;
    }
    if (!orderedScopes.length) {
      setError("Token scopes are still loading. Refresh and try again.");
      return;
    }
    if (!selectedScopes.length) {
      setError("Select at least one scope.");
      return;
    }
    const grantable = new Set(
      orderedScopes.filter((scope) => scope.grantable).map((scope) => scope.scope),
    );
    const notGrantable = selectedScopes.filter((scope) => !grantable.has(scope));
    if (notGrantable.length) {
      setError(`Your current workspace role cannot grant: ${notGrantable.join(", ")}`);
      return;
    }

    setBusy(true);
    setError("");
    try {
      const token = await api.createApiToken({
        name: cleanedName,
        scopes: selectedScopes,
        expires_in_days: days,
      });
      setCreatedToken(token);
      setRevealCreatedToken(false);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function revokeToken(token: ApiTokenInfo): Promise<void> {
    if (busy || token.revoked_at) return;
    const ok = await confirm({
      title: "Revoke this MCP token?",
      body: `LLM clients using "${token.name}" will lose MCP access immediately.`,
      confirmLabel: "Revoke token",
    });
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      await api.revokeApiToken(token.id);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!workspace.user) {
    return (
      <SettingsCard
        id="mcp-access"
        title="MCP access"
        description="Mint organization-scoped tokens for LLM clients that connect to Nodyra's MCP server."
        icon={Key}
      >
        <div className="nodyra-settings-inline-warning">
          <Info size={17} aria-hidden="true" />
          Sign in with a user account to create MCP automation tokens.
        </div>
      </SettingsCard>
    );
  }

  if (workspace.multiTenancyEnabled && !workspace.hasActiveWorkspace) {
    return (
      <SettingsCard
        id="mcp-access"
        title="MCP access"
        description="Mint organization-scoped tokens for LLM clients that connect to Nodyra's MCP server."
        icon={Key}
      >
        <div className="nodyra-settings-inline-warning">
          <Info size={17} aria-hidden="true" />
          Select a workspace before creating MCP automation tokens.
        </div>
      </SettingsCard>
    );
  }

  return (
    <SettingsCard
      id="mcp-access"
      title="MCP access"
      description="Create scoped bearer tokens for LLM clients. Tokens are bound to the current workspace and are shown only once."
      icon={Key}
    >
      <div className="nodyra-mcp-access">
        <div className="nodyra-mcp-endpoint">
          <label className="nodyra-settings-field nodyra-settings-field-wide">
            <span className="nodyra-settings-label">MCP endpoint</span>
            <span className="nodyra-mcp-copy-row">
              <input className="field-input" value={mcpEndpoint} readOnly spellCheck={false} />
              <button
                className="btn btn-sm"
                type="button"
                onClick={() => void copyValue("endpoint", mcpEndpoint)}
              >
                <Copy size={15} aria-hidden="true" />
                {copied === "endpoint" ? "Copied" : "Copy"}
              </button>
            </span>
            <small>Use this URL with an Authorization header: Bearer &lt;token&gt;.</small>
          </label>
          <div className="nodyra-mcp-workspace">
            <span>Current workspace</span>
            <strong>{workspaceLabel}</strong>
            {workspace.current?.id && <code>{workspace.current.id}</code>}
          </div>
        </div>

        <div className="nodyra-mcp-client-snippets">
          <div className="nodyra-mcp-snippet">
            <div className="nodyra-mcp-snippet-head">
              <strong>Headers</strong>
              <button
                className="btn btn-sm"
                type="button"
                onClick={() => void copyValue("headers", mcpHeaderSnippet)}
              >
                <Copy size={15} aria-hidden="true" />
                {copied === "headers" ? "Copied" : "Copy"}
              </button>
            </div>
            <pre>{mcpHeaderSnippet}</pre>
          </div>
          <div className="nodyra-mcp-snippet">
            <div className="nodyra-mcp-snippet-head">
              <strong>Generic MCP JSON</strong>
              <button
                className="btn btn-sm"
                type="button"
                onClick={() => void copyValue("config", mcpJsonConfig)}
              >
                <Copy size={15} aria-hidden="true" />
                {copied === "config" ? "Copied" : "Copy"}
              </button>
            </div>
            <pre>{mcpJsonConfig}</pre>
          </div>
        </div>

        {error && (
          <div className="nodyra-settings-inline-error" role="alert">
            <WarningCircle size={18} aria-hidden="true" />
            <span>{error}</span>
            <button className="btn btn-sm" type="button" onClick={() => void load()}>
              Retry
            </button>
          </div>
        )}

        {createdToken && (
          <div className="nodyra-mcp-created-token" role="status">
            <div>
              <strong>Copy this token now</strong>
              <span>Nodyra stores only a hash, so this secret cannot be shown again.</span>
            </div>
            <span className="nodyra-mcp-secret-row">
              <input
                className="field-input"
                type={revealCreatedToken ? "text" : "password"}
                value={createdToken.token}
                readOnly
                spellCheck={false}
              />
              <button
                type="button"
                aria-label={revealCreatedToken ? "Hide token" : "Show token"}
                aria-pressed={revealCreatedToken}
                onClick={() => setRevealCreatedToken((value) => !value)}
              >
                {revealCreatedToken ? <EyeSlash size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
              </button>
              <button
                className="btn btn-sm"
                type="button"
                onClick={() => void copyValue("token", createdToken.token)}
              >
                <Copy size={15} aria-hidden="true" />
                {copied === "token" ? "Copied" : "Copy"}
              </button>
            </span>
          </div>
        )}

        {loading && !scopes.length ? (
          <div className="nodyra-settings-skeleton" aria-label="Loading MCP access settings">
            <span /><span /><span />
          </div>
        ) : (
          <>
            <form
              className="nodyra-mcp-token-form"
              onSubmit={(event) => {
                event.preventDefault();
                void createToken();
              }}
            >
              <div className="nodyra-settings-form-grid">
                <label className="nodyra-settings-field">
                  <span className="nodyra-settings-label">Token name</span>
                  <input
                    className="field-input"
                    value={name}
                    maxLength={120}
                    onChange={(event) => setName(event.target.value)}
                  />
                  <small>Use a name that identifies the LLM client or automation owner.</small>
                </label>
                <label className="nodyra-settings-field">
                  <span className="nodyra-settings-label">Expires in</span>
                  <span className="nodyra-settings-input-wrap">
                    <input
                      className="field-input"
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={365}
                      step={1}
                      value={expiresInDays}
                      onChange={(event) => setExpiresInDays(event.target.value)}
                    />
                    <span className="nodyra-settings-unit">days</span>
                  </span>
                  <small>Shorter expiries reduce exposure if a client is compromised.</small>
                </label>
              </div>

              <fieldset className="nodyra-mcp-scopes" disabled={busy || loading}>
                <legend>Scopes</legend>
                <div className="nodyra-mcp-scope-grid">
                  {orderedScopes.map((scope) => {
                    const copy = scopeCopy(scope.scope);
                    const selected = selectedScopes.includes(scope.scope);
                    return (
                      <label
                        key={scope.scope}
                        className={`nodyra-mcp-scope${selected ? " is-selected" : ""}${!scope.grantable ? " is-disabled" : ""}`}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={!scope.grantable}
                          onChange={() => toggleScope(scope.scope)}
                        />
                        <span>
                          <strong>{copy.label}</strong>
                          <small>{copy.description}</small>
                          <code>{scope.scope}</code>
                        </span>
                        <em>{scope.minimum_role}</em>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              <div className="settings-actions">
                <button
                  className="btn btn-primary"
                  type="submit"
                  disabled={busy || loading || !selectedScopes.length || !orderedScopes.length}
                >
                  {busy ? "Creating…" : "Create MCP token"}
                </button>
              </div>
            </form>

            <div className="nodyra-mcp-token-list">
              <div className="nodyra-mcp-token-list-head">
                <strong>Existing tokens</strong>
                <button className="btn btn-sm" type="button" onClick={() => void load()} disabled={busy || loading}>
                  Refresh
                </button>
              </div>
              {tokens.length ? (
                <div className="nodyra-mcp-token-table" role="table" aria-label="MCP tokens">
                  <div className="nodyra-mcp-token-row is-head" role="row">
                    <span>Name</span>
                    <span>Prefix</span>
                    <span>Scopes</span>
                    <span>Last used</span>
                    <span>Status</span>
                    <span aria-label="Actions" />
                  </div>
                  {tokens.map((token) => {
                    const status = tokenStatus(token);
                    return (
                      <div className="nodyra-mcp-token-row" role="row" key={token.id}>
                        <span><strong>{token.name}</strong><small>Expires {formatTokenDate(token.expires_at)}</small></span>
                        <code>{token.token_prefix}</code>
                        <span>{token.scopes.join(", ")}</span>
                        <span>{formatTokenDate(token.last_used_at)}</span>
                        <span className={`nodyra-mcp-token-status ${status.className}`}>{status.label}</span>
                        <button
                          className="btn btn-sm"
                          type="button"
                          aria-label={`Revoke ${token.name}`}
                          disabled={busy || Boolean(token.revoked_at)}
                          onClick={() => void revokeToken(token)}
                        >
                          <Trash size={15} aria-hidden="true" />
                          Revoke
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="muted">No MCP tokens have been created for this workspace yet.</p>
              )}
            </div>
          </>
        )}
      </div>
    </SettingsCard>
  );
}

function McpServerPanel() {
  const [copied, setCopied] = useState(false);
  const endpoint = `${window.location.origin}/mcp`;

  async function copyEndpoint(): Promise<void> {
    try {
      await navigator.clipboard.writeText(endpoint);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <SettingsCard
      id="mcp-server"
      title="MCP server"
      description="Connect LLM clients to this Nodyra installation and manage outbound MCP server connections."
      icon={Plug}
    >
      <div className="nodyra-mcp-server-card">
        <label className="nodyra-settings-field nodyra-settings-field-wide">
          <span className="nodyra-settings-label">Server URL</span>
          <span className="nodyra-mcp-copy-row">
            <input className="field-input" value={endpoint} readOnly spellCheck={false} />
            <button className="btn btn-sm" type="button" onClick={() => void copyEndpoint()}>
              <Copy size={15} aria-hidden="true" />
              {copied ? "Copied" : "Copy"}
            </button>
          </span>
        </label>
        <div className="nodyra-mcp-server-actions">
          <Link className="btn" to="/settings/mcp-connections">
            <Plug size={15} aria-hidden="true" />
            Manage MCP connections
          </Link>
          <a
            className="btn btn-ghost"
            href="https://github.com/Harshit-repo/nodyra/blob/main/docs/mcp-quickstart.md"
            target="_blank"
            rel="noreferrer"
          >
            Open quickstart
          </a>
        </div>
      </div>
    </SettingsCard>
  );
}

function McpAgentQuickstart() {
  const [copied, setCopied] = useState(false);
  const snippet = `{
  "mcpServers": {
    "nodyra": {
      "type": "http",
      "url": "${window.location.origin}/mcp",
      "headers": { "Authorization": "Bearer <paste-token-here>" }
    }
  }
}`;

  async function copyConfig(): Promise<void> {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <SettingsCard
      id="mcp-agent-quickstart"
      title="Connect an AI agent"
      description="Use a scoped MCP access token with Claude, Cursor, or any streamable HTTP MCP client."
      icon={Plug}
    >
      <div className="nodyra-mcp-snippet">
        <div className="nodyra-mcp-snippet-head">
          <strong>Generic MCP JSON</strong>
          <button className="btn btn-sm" type="button" onClick={() => void copyConfig()}>
            <Copy size={15} aria-hidden="true" />
            {copied ? "Copied" : "Copy config"}
          </button>
        </div>
        <pre>{snippet}</pre>
      </div>
    </SettingsCard>
  );
}

function sandboxStatusCopy(status: SandboxStatus | undefined): {
  label: string;
  tone: "active" | "warn" | "off";
} {
  if (!status) return { label: "Checking", tone: "warn" };
  if (status.mode === "off") return { label: "Sandboxed execution is off", tone: "off" };
  if (!status.active) return { label: "Sandbox pool is not active", tone: "warn" };
  return { label: "Sandboxed execution is active", tone: "active" };
}

function SandboxStatusPanel() {
  const sandboxQuery = useQuery({
    queryKey: ["ops", "sandbox"],
    queryFn: api.sandboxStatus,
    retry: false,
    refetchInterval: 15000,
  });

  if (sandboxQuery.isError) return null;

  const status = sandboxQuery.data;
  const copy = sandboxStatusCopy(status);

  return (
    <SettingsCard
      id="sandbox"
      title="Sandbox"
      description="Execution isolation status for code, command, and sandbox-required workflows."
      icon={LockKey}
    >
      {!status ? (
        <div className="nodyra-settings-skeleton" aria-label="Loading sandbox status">
          <span /><span />
        </div>
      ) : (
        <div className={`nodyra-sandbox-status is-${copy.tone}`}>
          <div>
            <span>Status</span>
            <strong>{copy.label}</strong>
          </div>
          <dl>
            <div>
              <dt>Mode</dt>
              <dd>{status.mode}</dd>
            </div>
            <div>
              <dt>Runtime</dt>
              <dd>{status.runtime ?? "-"}</dd>
            </div>
            <div>
              <dt>Network</dt>
              <dd>{status.network ?? "-"}</dd>
            </div>
            <div>
              <dt>Idle</dt>
              <dd>{status.idle}</dd>
            </div>
            <div>
              <dt>Active runs</dt>
              <dd>{status.active_runs}</dd>
            </div>
          </dl>
        </div>
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
        <div className="nodyra-management-links">
          {canWorkspaceManage && <Link to="/organization"><Buildings size={18} aria-hidden="true" /><span><strong>Workspace</strong><small>Members, roles, quotas, and usage</small></span></Link>}
          {canAdmin && <Link to="/security"><ShieldCheck size={18} aria-hidden="true" /><span><strong>Instance access</strong><small>Users and installation-wide roles</small></span></Link>}
          {canAdmin && <Link to="/settings/sso"><IdentificationCard size={18} aria-hidden="true" /><span><strong>SSO</strong><small>Single sign-on via OIDC or SAML</small></span></Link>}
          {canAudit && <Link to="/activity"><GearSix size={18} aria-hidden="true" /><span><strong>Activity log</strong><small>Review workspace administrative changes</small></span></Link>}
        </div>
      ) : (
        <div className="nodyra-settings-inline-warning"><Info size={17} aria-hidden="true" />Ask an instance admin to change access or runtime settings.</div>
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
    (item) => canAdmin || !["readiness", "instance", "sandbox", "license"].includes(item.id),
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
    <div className="home nodyra-settings-page">
      <main className="home-main">
        <div className="home-bar nodyra-settings-heading">
          <div>
            <p className="nodyra-settings-eyebrow">Personal and installation preferences</p>
            <h1>Settings</h1>
            <p className="muted">Account, appearance, instance controls, and plan information.</p>
          </div>
        </div>

        <div className="nodyra-settings-layout">
          <nav className="nodyra-settings-nav" aria-label="Settings sections">
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

          <div className="nodyra-settings-content">
            <ProfilePanel workspaceRole={workspaceRole} />
            <AppearancePanel />
            <McpAgentQuickstart />
            <McpAccessPanel />
            <McpServerPanel />
            {canAdmin && <ReadinessPanel />}
            {canAdmin && <WorkspaceSettingsPanel />}
            {canAdmin && <SandboxStatusPanel />}
            {canAdmin && <LicensePanel />}
            <ManagementPanel canAdmin={canAdmin} canWorkspaceManage={canWorkspaceManage} canAudit={canAudit} />
          </div>
        </div>
      </main>
    </div>
  );
}
