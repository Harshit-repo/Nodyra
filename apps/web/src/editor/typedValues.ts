export interface TypedEnvelope {
  __nodyra_typed__: true;
  version: number;
  type: string;
  value: unknown;
  restorable?: boolean;
  truncated?: boolean;
  python_type?: string;
  module?: string;
  repr?: string;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function asTypedEnvelope(value: unknown): TypedEnvelope | null {
  if (!isRecord(value)) return null;
  if (value.__nodyra_typed__ !== true || value.version !== 1) return null;
  if (typeof value.type !== "string") return null;
  return value as unknown as TypedEnvelope;
}

export function typedLabel(envelope: TypedEnvelope): string {
  if (envelope.type === "decimal") return "Decimal";
  if (envelope.type === "dataframe") return "DataFrame";
  if (envelope.type === "bytearray") return "bytearray";
  return envelope.type;
}

export function typedValueBody(
  envelope: TypedEnvelope,
): Record<string, unknown> {
  return isRecord(envelope.value) ? envelope.value : {};
}

export function typedRecords(
  envelope: TypedEnvelope,
): Record<string, unknown>[] | null {
  if (envelope.type !== "dataframe") return null;
  const records = typedValueBody(envelope).records;
  if (
    Array.isArray(records) &&
    records.every((row) => isRecord(row))
  ) {
    return records as Record<string, unknown>[];
  }
  return null;
}

export function typedDisplayValue(value: unknown): unknown {
  const envelope = asTypedEnvelope(value);
  if (!envelope) return value;
  const body = typedValueBody(envelope);
  switch (envelope.type) {
    case "dataframe":
      return body.records ?? [];
    case "tuple":
    case "set":
    case "frozenset":
      return body.items ?? [];
    case "bytes":
    case "bytearray":
      return (
        body.utf8_preview ??
        `${typeof body.byte_length === "number" ? body.byte_length : 0} bytes`
      );
    case "object":
      return envelope.repr ?? `${envelope.python_type ?? "object"}`;
    default:
      return envelope.value;
  }
}

export function typedSummary(envelope: TypedEnvelope): string {
  const body = typedValueBody(envelope);
  if (envelope.type === "dataframe") {
    const rows = typeof body.row_count === "number" ? body.row_count : 0;
    const cols = typeof body.column_count === "number" ? body.column_count : 0;
    const suffix =
      envelope.restorable === false || body.truncated || envelope.truncated
        ? " preview"
        : "";
    return `${rows} rows x ${cols} columns${suffix}`;
  }
  if (envelope.type === "bytes" || envelope.type === "bytearray") {
    const byteLength =
      typeof body.byte_length === "number" ? body.byte_length : 0;
    return `${byteLength} B`;
  }
  if (
    envelope.type === "tuple" ||
    envelope.type === "set" ||
    envelope.type === "frozenset"
  ) {
    const length = typeof body.length === "number" ? body.length : 0;
    return `${length} item${length === 1 ? "" : "s"}`;
  }
  if (envelope.type === "object") {
    return envelope.restorable === false ? "not restorable" : "object";
  }
  const display = typedDisplayValue(envelope);
  return display === null || display === undefined ? "" : String(display);
}

export function formatTypedCell(value: unknown): string | null {
  const envelope = asTypedEnvelope(value);
  if (!envelope) return null;
  const label = typedLabel(envelope);
  const summary = typedSummary(envelope);
  return summary ? `${label}: ${summary}` : label;
}
