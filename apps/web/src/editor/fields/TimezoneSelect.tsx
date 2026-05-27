import { useEffect, useMemo, useState } from "react";

import { api } from "../../api";

type IntlWithTimezones = typeof Intl & {
  supportedValuesOf?: (key: string) => string[];
};

function loadIanaNames(): string[] {
  const intl = Intl as IntlWithTimezones;
  if (typeof intl.supportedValuesOf === "function") {
    try {
      return intl.supportedValuesOf("timeZone");
    } catch {
      // Fall through to the static fallback.
    }
  }
  // Older browsers: a small curated list covering the common regions. Users
  // can still type any name into the value; the server validates it.
  return [
    "UTC",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Asia/Hong_Kong",
    "Asia/Dubai",
    "Australia/Sydney",
    "Australia/Perth",
    "Africa/Johannesburg",
    "Pacific/Auckland",
  ];
}

// IANA names are "Region/City". Group them by region for browsability.
// "UTC", "GMT", "Etc/*" land in "Other".
function groupByRegion(names: string[]): { region: string; zones: string[] }[] {
  const groups = new Map<string, string[]>();
  for (const name of names) {
    const slash = name.indexOf("/");
    const region = slash > 0 ? name.slice(0, slash) : "Other";
    const bucket = groups.get(region) ?? [];
    bucket.push(name);
    groups.set(region, bucket);
  }
  // Sort region keys alphabetically, but pin "UTC" / "Other" to the bottom.
  const regions = Array.from(groups.keys()).sort((a, b) => {
    const aOther = a === "Other" || a === "UTC" || a === "Etc";
    const bOther = b === "Other" || b === "UTC" || b === "Etc";
    if (aOther && !bOther) return 1;
    if (!aOther && bOther) return -1;
    return a.localeCompare(b);
  });
  return regions.map((region) => ({
    region,
    zones: (groups.get(region) ?? []).slice().sort(),
  }));
}

export function TimezoneSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
}) {
  const [workspaceDefault, setWorkspaceDefault] = useState<string>("");

  useEffect(() => {
    api
      .getSystemSettings()
      .then((settings) => setWorkspaceDefault(settings.app_timezone || ""))
      .catch(() => {
        // Workspace defaults are best-effort; missing is fine.
      });
  }, []);

  const ianaNames = useMemo(() => loadIanaNames(), []);
  const groups = useMemo(() => groupByRegion(ianaNames), [ianaNames]);

  const defaultLabel = workspaceDefault
    ? `(workspace default — ${workspaceDefault})`
    : "(workspace default — UTC)";

  // If the saved value isn't in the browser's known list, expose it as a
  // separate option at the top so it stays selected and visible.
  const unknownSaved = value && !ianaNames.includes(value) ? value : "";

  return (
    <div className="timezone-select">
      <select
        className="field-input"
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{defaultLabel}</option>
        {unknownSaved && (
          <option value={unknownSaved}>
            {unknownSaved} (unknown to browser)
          </option>
        )}
        {groups.map(({ region, zones }) => (
          <optgroup key={region} label={region}>
            {zones.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <p className="muted timezone-hint">
        Type to jump within the list. Leave on “workspace default” to follow
        the global setting.
      </p>
    </div>
  );
}
