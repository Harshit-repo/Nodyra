import { safeGetItem, safeSetItem } from "./safeStorage";

export type ThemePreference = "system" | "dark" | "light";
export type FontPreference = "brand" | "inter" | "technical" | "system";

const KEY = "noodle_theme";
const FONT_KEY = "noodle_font";
const THEMES: ThemePreference[] = ["system", "dark", "light"];
const FONTS: FontPreference[] = ["brand", "inter", "technical", "system"];

function isTheme(value: string | null): value is ThemePreference {
  return THEMES.includes(value as ThemePreference);
}

function isFont(value: string | null): value is FontPreference {
  return FONTS.includes(value as FontPreference);
}

function systemTheme(): "dark" | "light" {
  if (window.matchMedia?.("(prefers-color-scheme: light)").matches) {
    return "light";
  }
  return "dark";
}

export function getThemePreference(): ThemePreference {
  const stored = safeGetItem(KEY);
  return isTheme(stored) ? stored : "system";
}

export function applyThemePreference(
  preference: ThemePreference = getThemePreference(),
): "dark" | "light" {
  const resolved = preference === "system" ? systemTheme() : preference;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = preference;
  return resolved;
}

export function setThemePreference(preference: ThemePreference): boolean {
  const persisted = safeSetItem(KEY, preference);
  applyThemePreference(preference);
  return persisted;
}

export function getFontPreference(): FontPreference {
  const stored = safeGetItem(FONT_KEY);
  return isFont(stored) ? stored : "brand";
}

export function applyFontPreference(
  preference: FontPreference = getFontPreference(),
): FontPreference {
  document.documentElement.dataset.font = preference;
  return preference;
}

export function setFontPreference(preference: FontPreference): boolean {
  const persisted = safeSetItem(FONT_KEY, preference);
  applyFontPreference(preference);
  return persisted;
}

export function listenForSystemThemeChanges(): () => void {
  const media = window.matchMedia?.("(prefers-color-scheme: light)");
  if (!media) return () => undefined;
  const onChange = () => {
    if (getThemePreference() === "system") {
      applyThemePreference("system");
    }
  };
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}
