export const SUPPORTED_LOCALES = ["en", "en-XA"] as const;

export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

const LOCALE_STORAGE_KEY = "nodyra.locale";

const EN_MESSAGES = {
  "mobile.mode": "Phone monitoring mode",
  "mobile.title": "Inspect this workflow without squeezing the canvas",
  "mobile.description":
    "The full visual editor is designed for desktop and tablet widths. On a phone, use executions and approvals for safe operational work.",
  "mobile.executions": "Open executions",
  "mobile.workflows": "Back to workflows",
  "mobile.editorAnyway": "Open full editor anyway",
  "artifact.lineage": "Lineage",
  "artifact.download": "Download",
} as const;

export type MessageKey = keyof typeof EN_MESSAGES;

const PSEUDO_MAP: Record<string, string> = {
  a: "à", b: "ƀ", c: "ç", d: "ð", e: "ë", f: "ƒ", g: "ğ", h: "ħ",
  i: "ï", j: "ĵ", k: "ķ", l: "ľ", m: "ɱ", n: "ñ", o: "ô", p: "þ",
  q: "ɋ", r: "ŕ", s: "š", t: "ţ", u: "ü", v: "ṽ", w: "ŵ", x: "ẋ",
  y: "ý", z: "ž",
};

function normalizeLocale(value: string | null | undefined): SupportedLocale | null {
  if (!value) return null;
  if (value.toLowerCase() === "en-xa") return "en-XA";
  if (value.toLowerCase().startsWith("en")) return "en";
  return null;
}

function browserLocale(): SupportedLocale {
  if (typeof window === "undefined") return "en";
  const requested = normalizeLocale(new URLSearchParams(window.location.search).get("locale"));
  if (requested) return requested;
  try {
    const stored = normalizeLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY));
    if (stored) return stored;
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
  for (const locale of window.navigator.languages ?? [window.navigator.language]) {
    const supported = normalizeLocale(locale);
    if (supported) return supported;
  }
  return "en";
}

let activeLocale: SupportedLocale = browserLocale();

function pseudoLocalize(value: string): string {
  const transformed = Array.from(value, (character) => {
    const replacement = PSEUDO_MAP[character.toLowerCase()];
    if (!replacement) return character;
    return character === character.toUpperCase() ? replacement.toUpperCase() : replacement;
  }).join("");
  const expansion = "·".repeat(Math.max(3, Math.ceil(value.length * 0.35)));
  return `[${transformed} ${expansion}]`;
}

export function getLocale(): SupportedLocale {
  return activeLocale;
}

export function setLocalePreference(locale: SupportedLocale): void {
  activeLocale = locale;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // The in-memory preference still applies for the active page.
  }
  applyLocalePreference();
}

export function applyLocalePreference(): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = activeLocale === "en-XA" ? "en" : activeLocale;
  document.documentElement.dataset.locale = activeLocale;
}

export function t(key: MessageKey): string {
  const message = EN_MESSAGES[key];
  return activeLocale === "en-XA" ? pseudoLocalize(message) : message;
}

function intlLocale(): string {
  return activeLocale === "en-XA" ? "en" : activeLocale;
}

export function formatDateTime(
  value: string | number | Date,
  options: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" },
): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return new Intl.DateTimeFormat(intlLocale(), options).format(date);
}

export function formatNumber(
  value: number,
  options?: Intl.NumberFormatOptions,
): string {
  return new Intl.NumberFormat(intlLocale(), options).format(value);
}

export function formatPlural(
  count: number,
  forms: { one: string; other: string },
): string {
  const form = new Intl.PluralRules(intlLocale()).select(count) === "one" ? forms.one : forms.other;
  const value = `${formatNumber(count)} ${form}`;
  return activeLocale === "en-XA" ? pseudoLocalize(value) : value;
}

export function formatTimeZoneName(timeZone?: string): string {
  const resolved = timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  try {
    const parts = new Intl.DateTimeFormat(intlLocale(), {
      timeZone: resolved,
      timeZoneName: "long",
    }).formatToParts(new Date());
    return parts.find((part) => part.type === "timeZoneName")?.value || resolved;
  } catch {
    return resolved;
  }
}
