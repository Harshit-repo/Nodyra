/**
 * Token usage display and cost estimation helpers.
 *
 * Cost data below is approximate and reflects common hosted LLM pricing
 * as of June 2026. A fallback rate is applied when the model is unknown.
 */

import type { TokenUsage } from "./store";

/** Per-model pricing: [input $/1M tokens, output $/1M tokens] */
const MODEL_PRICING: Record<string, [number, number]> = {
  // OpenAI
  "gpt-4o": [2.50, 10.00],
  "gpt-4o-2024-08-06": [2.50, 10.00],
  "gpt-4o-2024-05-13": [5.00, 15.00],
  "gpt-4o-mini": [0.15, 0.60],
  "gpt-4o-mini-2024-07-18": [0.15, 0.60],
  "gpt-4-turbo": [10.00, 30.00],
  "gpt-4": [30.00, 60.00],
  "gpt-3.5-turbo": [0.50, 1.50],
  // Anthropic
  "claude-3-5-sonnet": [3.00, 15.00],
  "claude-3-5-sonnet-20241022": [3.00, 15.00],
  "claude-3-opus": [15.00, 75.00],
  "claude-3-haiku": [0.25, 1.25],
  "claude-3-5-haiku": [0.80, 4.00],
  // Google
  "gemini-1.5-pro": [1.25, 5.00],
  "gemini-1.5-flash": [0.075, 0.30],
  "gemini-2.0-flash": [0.10, 0.40],
  // Groq / Llama hosted
  "llama-3.1-70b": [0.59, 0.79],
  "llama-3.1-8b": [0.05, 0.08],
  "llama-3.3-70b": [0.59, 0.79],
  "mixtral-8x7b": [0.24, 0.24],
  // Together / open-source hosted
  "llama-3.1-405b": [2.50, 2.50],
  "qwen2.5-72b": [0.90, 0.90],
};

/** Fallback pricing when model is unrecognised. */
const FALLBACK_INPUT_RATE = 1.00;
const FALLBACK_OUTPUT_RATE = 2.00;

/**
 * Estimate the cost of a single LLM call in USD.
 * Returns 0 when no tokens were consumed.
 */
export function estimateCost(usage: TokenUsage): number {
  const input = usage.prompt_tokens || 0;
  const output = usage.completion_tokens || 0;
  if (input === 0 && output === 0) return 0;

  const model = (usage.model ?? "").toLowerCase().trim();
  const pricing = MODEL_PRICING[model];
  const [inputRate, outputRate] = pricing ?? [FALLBACK_INPUT_RATE, FALLBACK_OUTPUT_RATE];

  const cost = (input / 1_000_000) * inputRate + (output / 1_000_000) * outputRate;
  return Number(cost.toFixed(6));
}

/**
 * Format a USD cost value to a concise string.
 *
 *   "$0.003"   — less than 1 cent
 *   "$0.12"    — under $1
 *   "$1.45"    — over $1
 */
export function formatCost(amountUsd: number): string {
  if (amountUsd <= 0) return "$0.00";
  if (amountUsd < 0.01) return `~$${amountUsd.toFixed(4)}`;
  if (amountUsd < 0.50) return `~$${amountUsd.toFixed(3)}`;
  return `~$${amountUsd.toFixed(2)}`;
}

/**
 * Short model label extracted from a full model name.
 *   "gpt-4o-2024-08-06" -> "gpt-4o"
 *   "claude-3-5-sonnet-20241022" -> "claude-3-5-sonnet"
 */
export function shortModelLabel(model?: string): string | undefined {
  if (!model) return undefined;
  // Use known key as-is if it exists as a pricing key (cheapest exact match).
  const lower = model.toLowerCase().trim();
  // Sort by descending key length so more-specific keys (e.g. "gpt-4o-mini")
  // match before shorter prefixes ("gpt-4o") that would otherwise shadow them.
  const known = Object.keys(MODEL_PRICING)
    .sort((a, b) => b.length - a.length)
    .find((k) => lower.startsWith(k));
  return known ?? lower.split("-").slice(0, 2).join("-");
}
