export const EDGE_PREVIEW_MAX_CHARS = 420;

export interface EdgePreviewValue {
  hasValue: boolean;
  value: unknown;
}

export function edgePreviewValue(
  outputs: unknown,
  sourceHandle?: string | null,
): EdgePreviewValue {
  if (!outputs || typeof outputs !== "object" || Array.isArray(outputs)) {
    return { hasValue: false, value: undefined };
  }
  const port = sourceHandle ?? "main";
  const record = outputs as Record<string, unknown>;
  if (!Object.prototype.hasOwnProperty.call(record, port)) {
    return { hasValue: false, value: undefined };
  }
  return { hasValue: true, value: record[port] };
}

export function formatEdgePreviewValue(
  value: unknown,
  maxChars = EDGE_PREVIEW_MAX_CHARS,
): string {
  let text: string;
  if (value === undefined) {
    text = "undefined";
  } else if (typeof value === "string") {
    text = value;
  } else {
    try {
      text = JSON.stringify(value, null, 2) ?? String(value);
    } catch {
      text = String(value);
    }
  }
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars).trimEnd()}\n... truncated (${text.length.toLocaleString()} chars)`;
}

export function EdgePreview({
  sourceLabel,
  typeLabel,
  value,
  onOpen,
}: {
  sourceLabel: string;
  typeLabel: string;
  value: unknown;
  onOpen: () => void;
}) {
  return (
    <div className="edge-preview-popover" role="tooltip">
      <div className="edge-preview-head">
        <span>{sourceLabel}</span>
        {typeLabel && <span>{typeLabel}</span>}
      </div>
      <pre className="edge-preview-body">{formatEdgePreviewValue(value)}</pre>
      <button
        type="button"
        className="edge-preview-open"
        onClick={(event) => {
          event.stopPropagation();
          onOpen();
        }}
      >
        Open output
      </button>
    </div>
  );
}
