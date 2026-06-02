export const CATEGORY_COLORS: Record<string, string> = {
  Triggers: "#4c9eff",
  Logic: "#b48bff",
  AI: "#24c78e",
  Data: "#37c8a8",
  Transform: "#3fd0e0",
  Integrations: "#ff6f91",
  Utility: "#8a93a8",
  General: "#8a93a8",
};

export function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? "#a59a86";
}

export const CATEGORY_ORDER = [
  "Triggers",
  "Logic",
  "AI",
  "Data",
  "Transform",
  "Integrations",
  "Utility",
  "General",
];
