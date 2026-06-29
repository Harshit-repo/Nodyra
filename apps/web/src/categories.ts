export const CATEGORY_COLORS: Record<string, string> = {
  Triggers: "#4c9eff",
  API: "#2f9bff",
  Logic: "#b48bff",
  AI: "#24c78e",
  Data: "#37c8a8",
  Transform: "#3fd0e0",
  Integrations: "#ff6f91",
  Utility: "#8a93a8",
  General: "#8a93a8",
  MCP: "#ff8c42",
};

export function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? "#a59a86";
}

export const CATEGORY_ORDER = [
  "Triggers",
  "API",
  "Logic",
  "AI",
  "Data",
  "Transform",
  "Integrations",
  "MCP",
  "Utility",
  "General",
];
