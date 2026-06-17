import type { NodeManifest } from "../../types";

/** Query params for a dynamic-options fetch: the selected credential's id, the
 * standard provider/base_url/workflow context, plus any field-specific
 * `depends_on` params that carry a string value.
 */
export function buildLoadOptionsParams(
  credential: { id: string } | null,
  params: Record<string, unknown>,
  dependsOn: string[] = [],
): Record<string, string> {
  const out: Record<string, string> = {};
  if (credential?.id) out.credential_id = credential.id;
  for (const key of ["provider", "base_url", "workflow_id", ...dependsOn]) {
    const value = params[key];
    if (typeof value === "string" && value.trim()) out[key] = value.trim();
  }
  return out;
}

/** Evaluate a param's `display_when` against current param values.
 * Supports simple equality, AND groups, and OR-of-AND groups.
 */
export function matchesDisplayWhen(
  displayWhen: Record<string, unknown> | null | undefined,
  params: Record<string, unknown>,
): boolean {
  if (!displayWhen || typeof displayWhen !== "object") return true;
  if (Array.isArray(displayWhen.any)) {
    return (displayWhen.any as unknown[]).some((group) =>
      matchesDisplayWhen(group as Record<string, unknown>, params),
    );
  }
  if (Array.isArray(displayWhen.conditions)) {
    return (displayWhen.conditions as unknown[]).every((cond) =>
      matchesDisplayWhen(cond as Record<string, unknown>, params),
    );
  }
  const paramName = String(displayWhen.param ?? "");
  if (!paramName) return true;
  const currentVal = String(params[paramName] ?? "");
  if (Array.isArray(displayWhen.values)) {
    return displayWhen.values.map(String).includes(currentVal);
  }
  return currentVal === String(displayWhen.value ?? "");
}

/** Recompute params after a resource/operation switch on a consolidated node. */
export function applyResourceOperation(
  manifest: NodeManifest,
  params: Record<string, unknown>,
  resource: string,
  operation: string,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...params, resource, operation };
  for (const spec of manifest.params) {
    if (spec.display_when && !matchesDisplayWhen(spec.display_when, next)) {
      delete next[spec.name];
    }
  }
  return next;
}
