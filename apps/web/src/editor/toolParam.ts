/** Helpers for the per-parameter "From AI" control in tool mode. A From-AI
 *  param stores a positional `$fromAI(name, description, type)` expression that
 *  the engine resolves at tool-call time (see packages/core/noodle/expr.py). */

function esc(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

export function fromAiExpr(name: string, description: string, argType: string): string {
  return `{{ $fromAI('${esc(name)}', '${esc(description)}', '${esc(argType)}') }}`;
}

export function isFromAiExpr(value: unknown): boolean {
  return typeof value === "string" && /\{\{\s*\$fromAI\(/.test(value);
}

export function paramArgType(specType: string | undefined): "string" | "number" | "boolean" {
  if (specType === "number" || specType === "integer" || specType === "float") return "number";
  if (specType === "boolean") return "boolean";
  return "string";
}
