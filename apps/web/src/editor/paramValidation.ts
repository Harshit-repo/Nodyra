import type { ParamSpec } from "../types";

export interface FieldValidation {
  /** Whether the current value is valid */
  valid: boolean;
  /** User-facing error message, or null if valid */
  message: string | null;
}

/**
 * Validate a parameter value against its spec.
 *
 * Checks required fields, type constraints, and basic format rules.
 * Returns `{ valid: true, message: null }` for passing values.
 */
export function validateParam(
  spec: ParamSpec,
  value: unknown,
): FieldValidation {
  const str = typeof value === "string" ? value.trim() : "";

  // ── required check ──────────────────────────────────────────────────
  if (spec.required) {
    if (value === null || value === undefined) {
      return { valid: false, message: `${formatParamLabelInline(spec.name)} is required.` };
    }
    if (typeof value === "string" && str.length === 0) {
      return { valid: false, message: `${formatParamLabelInline(spec.name)} is required.` };
    }
    if (typeof value === "number" && Number.isNaN(value)) {
      return { valid: false, message: `${formatParamLabelInline(spec.name)} must be a valid number.` };
    }
  }

  // ── type-specific checks ────────────────────────────────────────────
  if (spec.type === "integer" && value !== null && value !== undefined) {
    if (typeof value === "number" && !Number.isInteger(value)) {
      return { valid: false, message: `${formatParamLabelInline(spec.name)} must be a whole number.` };
    }
  }

  if (spec.type === "number" && value !== null && value !== undefined) {
    if (typeof value === "number" && Number.isNaN(value)) {
      return { valid: false, message: `${formatParamLabelInline(spec.name)} must be a valid number.` };
    }
  }

  // ── choices check (for select-type fields) ──────────────────────────
  if (spec.choices && spec.choices.length > 0 && str.length > 0) {
    const validValues = spec.choices.map((c) => String(c));
    if (!validValues.includes(str) && !spec.load_options) {
      // Don't flag free-text in load_options fields (they can have custom values)
      return { valid: false, message: `"${str}" is not a valid choice for ${formatParamLabelInline(spec.name)}.` };
    }
  }

  // ── expression fields: skip validation (expressions are validated at runtime) ──
  if (typeof value === "string" && value.startsWith("{{") && value.endsWith("}}")) {
    return { valid: true, message: null };
  }

  return { valid: true, message: null };
}

/** Format a param name into a readable label (inline, no JSX). */
function formatParamLabelInline(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Hook-friendly: returns whether a field should show its error state.
 * A field shows errors only after it has been "touched" (blurred) and
 * the value is invalid.
 */
export function shouldShowError(
  touched: boolean,
  validation: FieldValidation,
): boolean {
  return touched && !validation.valid;
}
