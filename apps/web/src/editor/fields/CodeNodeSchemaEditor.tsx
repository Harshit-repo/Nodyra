import { useCallback, useEffect, useRef, useState } from "react";

type SchemaType = "string" | "number" | "integer" | "boolean" | "array" | "object" | "enum";

interface SchemaProperty {
  name: string;
  type: SchemaType;
  required: boolean;
}

export interface SchemaData {
  type: "object";
  properties: Record<string, { type: string; enum?: string[] }>;
  required: string[];
}

/** Accept any object-shaped value that may be a schema (e.g. from params). */
export type SchemaValue = SchemaData | Record<string, unknown> | null;

function defaultProperties(): SchemaProperty[] {
  return [{ name: "", type: "string", required: false }];
}

/** Convert a JSON Schema dict to the editor's internal row format. */
function schemaToRows(schema: SchemaData | null): SchemaProperty[] {
  if (!schema?.properties || typeof schema.properties !== "object") {
    return defaultProperties();
  }
  const requiredSet = new Set(schema.required || []);
  const rows: SchemaProperty[] = [];
  for (const [name, prop] of Object.entries(schema.properties)) {
    if (prop && typeof prop === "object") {
      const type = (prop.type as SchemaType) || "string";
      rows.push({
        name,
        type: "enum" in prop && Array.isArray(prop.enum) ? "enum" : type,
        required: requiredSet.has(name),
      });
    }
  }
  return rows.length > 0 ? rows : defaultProperties();
}

/** Convert editor rows back into a JSON Schema dict. */
function rowsToSchema(rows: SchemaProperty[]): SchemaData | null {
  const nonEmpty = rows.filter((r) => r.name.trim().length > 0);
  if (nonEmpty.length === 0) return null;

  const properties: SchemaData["properties"] = {};
  const required: string[] = [];

  for (const row of nonEmpty) {
    const prop: Record<string, unknown> = {};
    if (row.type === "enum") {
      prop.type = "string";
      prop.enum = [];
    } else {
      prop.type = row.type;
    }
    properties[row.name.trim()] = prop as { type: string; enum?: string[] };
    if (row.required) {
      required.push(row.name.trim());
    }
  }

  return {
    type: "object",
    properties,
    required: required.length > 0 ? required : [],
  };
}

/** Pretty-print a preview of the inferred port types. */
function schemaPreview(schema: SchemaData | null): string {
  if (!schema?.properties) return "Any";
  const props = Object.entries(schema.properties);
  if (props.length === 0) return "Any";
  return props
    .map(([name, prop]) => {
      const type = "enum" in prop && Array.isArray(prop.enum) ? "enum" : prop.type || "any";
      return `${name}: ${type}`;
    })
    .join(", ");
}

interface CodeNodeSchemaEditorProps {
  value: SchemaValue;
  onChange: (value: SchemaData | null) => void;
}

function coerceSchema(raw: SchemaValue): SchemaData | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const obj = raw as Record<string, unknown>;
  if (obj.type !== "object") return null;
  if (!obj.properties || typeof obj.properties !== "object") return null;
  return raw as unknown as SchemaData;
}

export function CodeNodeSchemaEditor({ value, onChange }: CodeNodeSchemaEditorProps) {
  const coerced = coerceSchema(value);
  const [mode, setMode] = useState<"builder" | "json">("builder");
  const [rows, setRows] = useState<SchemaProperty[]>(() => schemaToRows(coerced));
  const [jsonText, setJsonText] = useState(() =>
    coerced ? JSON.stringify(coerced, null, 2) : JSON.stringify({ type: "object", properties: {}, required: [] }, null, 2),
  );
  const [invalid, setInvalid] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Sync rows when value changes externally (undo/redo).
  useEffect(() => {
    if (wrapRef.current?.contains(document.activeElement)) return;
    const c = coerceSchema(value);
    setRows(schemaToRows(c));
    setJsonText(c ? JSON.stringify(c, null, 2) : JSON.stringify({ type: "object", properties: {}, required: [] }, null, 2));
    setInvalid(false);
  }, [value]);

  const commitRows = useCallback(
    (next: SchemaProperty[]) => {
      setRows(next);
      onChange(rowsToSchema(next));
    },
    [onChange],
  );

  const switchToJson = useCallback(() => {
    setJsonText(JSON.stringify(rowsToSchema(rows) || { type: "object", properties: {}, required: [] }, null, 2));
    setInvalid(false);
    setMode("json");
  }, [rows]);

  const switchToBuilder = useCallback(() => {
    if (!jsonText.trim()) {
      setRows(defaultProperties());
      onChange(null);
      setMode("builder");
      return;
    }
    try {
      const parsed = JSON.parse(jsonText) as Record<string, unknown>;
      if (parsed && typeof parsed === "object") {
        const schema = coerceSchema(parsed);
        setRows(schemaToRows(schema));
        onChange(schema);
        setInvalid(false);
        setMode("builder");
      }
    } catch {
      // Keep JSON mode if not parseable
    }
  }, [jsonText, onChange]);

  // Add a new empty property row.
  const addProperty = useCallback(() => {
    const next = [...rows, { name: "", type: "string" as SchemaType, required: false }];
    commitRows(next);
  }, [rows, commitRows]);

  // Remove a property row by index.
  const removeProperty = useCallback(
    (idx: number) => {
      const next = rows.filter((_, i) => i !== idx);
      commitRows(next.length > 0 ? next : defaultProperties());
    },
    [rows, commitRows],
  );

  // Update a single property field.
  const updateProperty = useCallback(
    (idx: number, field: Partial<SchemaProperty>) => {
      const next = rows.map((r, i) => (i === idx ? { ...r, ...field } : r));
      commitRows(next);
    },
    [rows, commitRows],
  );

  const schema = rowsToSchema(rows);

  return (
    <div className="inspector-section">
      <div className="kv-field" ref={wrapRef}>
        <div className="kv-toolbar">
          <button
            type="button"
            className={`kv-mode-btn${mode === "builder" ? " is-active" : ""}`}
            onClick={() => {
              if (mode !== "builder") switchToBuilder();
            }}
          >
            Schema Builder
          </button>
          <button
            type="button"
            className={`kv-mode-btn${mode === "json" ? " is-active" : ""}`}
            onClick={() => {
              if (mode !== "json") switchToJson();
            }}
          >
            Raw JSON
          </button>
        </div>

        {mode === "builder" ? (
          <div className="ncs-editor">
            <div className="ncs-rows">
              {rows.map((prop, idx) => (
                <div className="ncs-row" key={idx}>
                  <input
                    className="ncs-name-input"
                    type="text"
                    value={prop.name}
                    placeholder="property name"
                    spellCheck={false}
                    onChange={(e) => updateProperty(idx, { name: e.target.value })}
                  />
                  <select
                    className="ncs-type-select"
                    value={prop.type}
                    onChange={(e) => updateProperty(idx, { type: e.target.value as SchemaType })}
                  >
                    <option value="string">string</option>
                    <option value="number">number</option>
                    <option value="integer">integer</option>
                    <option value="boolean">boolean</option>
                    <option value="array">array</option>
                    <option value="object">object</option>
                    <option value="enum">enum</option>
                  </select>
                  <label className="ncs-required-label">
                    <input
                      type="checkbox"
                      checked={prop.required}
                      onChange={(e) => updateProperty(idx, { required: e.target.checked })}
                    />
                    Required
                  </label>
                  <button
                    type="button"
                    className="ncs-remove"
                    title="Remove property"
                    onClick={() => removeProperty(idx)}
                    disabled={rows.length <= 1 && !rows[0].name}
                  >
                    X
                  </button>
                </div>
              ))}
            </div>
            <button type="button" className="ncs-add" onClick={addProperty}>
              + Add Property
            </button>

            {schema && schema.properties && Object.keys(schema.properties).length > 0 && (
              <div className="ncs-preview">
                <strong>Port types:</strong> {schemaPreview(schema)}
              </div>
            )}
          </div>
        ) : (
          <textarea
            className={`field-input field-json${invalid ? " field-invalid" : ""}`}
            rows={8}
            spellCheck={false}
            placeholder='{ "type": "object", "properties": {}, "required": [] }'
            value={jsonText}
            onChange={(e) => {
              const next = e.target.value;
              setJsonText(next);
              if (next.trim() === "") {
                setInvalid(false);
                onChange(null);
                return;
              }
              try {
                onChange(JSON.parse(next) as SchemaData);
                setInvalid(false);
              } catch {
                setInvalid(true);
              }
            }}
          />
        )}
      </div>
    </div>
  );
}
