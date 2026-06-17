export interface ExprContext {
  json?: unknown;
  inputs?: Record<string, unknown>;
  nodes?: Record<string, unknown>;
}

export const EXPR_RE = /\{\{[\s\S]+?\}\}/;
export const EXPR_RE_GLOBAL = /\{\{[\s\S]+?\}\}/g;

export type PreviewPart =
  | { kind: "text"; value: string }
  | { kind: "expr"; raw: string; value: unknown }
  | { kind: "error"; raw: string; error: string };

export type ResultView = "text" | "html";

export function formatResultText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function getTokenBeforeCursor(value: string, cursorPos: number): string {
  const before = value.slice(0, cursorPos);
  const match = before.match(/\$[\w.\["\]]*$/);
  return match ? match[0] : "";
}

export function computeSuggestions(
  value: string,
  cursorPos: number,
  ctx?: ExprContext,
): string[] {
  const token = getTokenBeforeCursor(value, cursorPos);
  if (!token) return [];

  const results: string[] = [];

  if (token.startsWith("$json.")) {
    const prefix = token.slice("$json.".length);
    const keys =
      ctx?.json && typeof ctx.json === "object" && ctx.json !== null
        ? Object.keys(ctx.json as Record<string, unknown>)
        : [];
    for (const key of keys) {
      if (key.startsWith(prefix)) results.push(`$json.${key}`);
    }
  } else if (token.startsWith("$json")) {
    results.push("$json.");
    const keys =
      ctx?.json && typeof ctx.json === "object" && ctx.json !== null
        ? Object.keys(ctx.json as Record<string, unknown>)
        : [];
    for (const key of keys) results.push(`$json.${key}`);
  } else if (token.startsWith("$node")) {
    const nodeIds = Object.keys(ctx?.nodes ?? {});
    for (const id of nodeIds) results.push(`$node["${id}"].`);
  } else if (token.startsWith("$env")) {
    results.push("$env.KEY");
  } else if (token.startsWith("$run")) {
    results.push("$run.id", "$run.status", "$run.startedAt");
  } else if (token.startsWith("$")) {
    results.push('$json.', '$node["', "$env.", "$run.");
  }

  return results.slice(0, 10);
}
