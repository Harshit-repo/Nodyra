import { Plus, Trash } from "@phosphor-icons/react";

export type ConditionType =
  | "string"
  | "number"
  | "boolean"
  | "array"
  | "object"
  | "date"
  | "any";

export type ConditionLogic = "AND" | "OR";

export interface Condition {
  id: string;
  field: string;
  type: ConditionType;
  operator: string;
  value: string;
}

export interface ConditionsValue {
  logic: ConditionLogic;
  conditions: Condition[];
}

const OPERATORS_BY_TYPE: Record<ConditionType, string[]> = {
  string: [
    "equals",
    "not equals",
    "contains",
    "does not contain",
    "starts with",
    "ends with",
    "is empty",
    "is not empty",
    "matches regex",
  ],
  number: [
    "equals",
    "not equals",
    "greater than",
    "greater than or equal",
    "less than",
    "less than or equal",
  ],
  boolean: ["is true", "is false"],
  array: [
    "is empty",
    "is not empty",
    "contains",
    "does not contain",
    "length equals",
    "length not equals",
    "length greater than",
    "length less than",
  ],
  object: ["has key", "does not have key", "is empty", "is not empty"],
  date: ["before", "after", "equals"],
  any: ["exists", "does not exist", "is empty", "is not empty"],
};

const TYPE_LABELS: Record<ConditionType, string> = {
  string: "T  String",
  number: "#  Number",
  boolean: "⊤  Boolean",
  array: "[]  Array",
  object: "{}  Object",
  date: "📅  Date",
  any: "∗  Any",
};

const NO_VALUE_OPERATORS = new Set([
  "is empty",
  "is not empty",
  "is true",
  "is false",
  "exists",
  "does not exist",
]);

const CONDITION_TYPES: ConditionType[] = [
  "string",
  "number",
  "boolean",
  "array",
  "object",
  "date",
  "any",
];

function defaultOperator(type: ConditionType): string {
  return OPERATORS_BY_TYPE[type][0];
}

function parseConditions(value: unknown): ConditionsValue {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const v = value as Record<string, unknown>;
    return {
      logic: v.logic === "OR" ? "OR" : "AND",
      conditions: Array.isArray(v.conditions)
        ? (v.conditions as unknown[]).filter(
            (c): c is Condition =>
              c !== null && typeof c === "object" && !Array.isArray(c),
          )
        : [],
    };
  }
  return { logic: "AND", conditions: [] };
}

function newCondition(): Condition {
  return {
    id: crypto.randomUUID(),
    field: "",
    type: "string",
    operator: "equals",
    value: "",
  };
}

function inferTypeFromValue(v: string): ConditionType {
  if (v === "true" || v === "false") return "boolean";
  if (v !== "" && !isNaN(Number(v))) return "number";
  return "string";
}

function mapLegacyOperator(op: string, type: ConditionType): string {
  const legacyMap: Record<string, string> = {
    equals: "equals",
    "not equals": "not equals",
    contains: "contains",
    "greater than": "greater than",
    "less than": "less than",
    "is empty": "is empty",
    "is not empty": "is not empty",
    "is true": "is true",
  };
  const candidate = legacyMap[op] ?? "";
  return OPERATORS_BY_TYPE[type].includes(candidate)
    ? candidate
    : defaultOperator(type);
}

export function ConditionsField({
  value,
  onChange,
  allParams,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  allParams?: Record<string, unknown>;
}): JSX.Element {
  const parsed = parseConditions(value);
  const isEmpty = parsed.conditions.length === 0;

  const legacyField =
    typeof allParams?.field === "string" ? allParams.field : "";
  const legacyOperator =
    typeof allParams?.operator === "string" ? allParams.operator : "";
  const legacyValue =
    typeof allParams?.value === "string" ? allParams.value : "";
  const hasLegacy = isEmpty && legacyField !== "";

  function update(next: ConditionsValue): void {
    onChange(next.conditions.length === 0 ? null : next);
  }

  function addCondition(): void {
    update({ ...parsed, conditions: [...parsed.conditions, newCondition()] });
  }

  function removeCondition(id: string): void {
    update({
      ...parsed,
      conditions: parsed.conditions.filter((c) => c.id !== id),
    });
  }

  function updateCondition(id: string, patch: Partial<Condition>): void {
    update({
      ...parsed,
      conditions: parsed.conditions.map((c) => {
        if (c.id !== id) return c;
        const next = { ...c, ...patch };
        if (
          patch.type !== undefined &&
          !OPERATORS_BY_TYPE[patch.type].includes(next.operator)
        ) {
          next.operator = defaultOperator(patch.type);
        }
        return next;
      }),
    });
  }

  function toggleLogic(): void {
    update({ ...parsed, logic: parsed.logic === "AND" ? "OR" : "AND" });
  }

  function migrateFromLegacy(): void {
    const type = inferTypeFromValue(legacyValue);
    const operator = mapLegacyOperator(legacyOperator, type);
    update({
      logic: "AND",
      conditions: [
        {
          id: crypto.randomUUID(),
          field: legacyField,
          type,
          operator,
          value: legacyValue,
        },
      ],
    });
  }

  return (
    <div className="conditions-field">
      {hasLegacy && (
        <div className="conditions-legacy-notice">
          <span>
            Using legacy single-condition (field=&quot;{legacyField}&quot;,
            op=&quot;{legacyOperator}&quot;)
          </span>
          <button
            type="button"
            className="btn btn-xs"
            onClick={migrateFromLegacy}
          >
            Migrate →
          </button>
        </div>
      )}

      {parsed.conditions.length > 1 && (
        <div className="conditions-logic-toggle">
          <span>Match</span>
          <button
            type="button"
            className="conditions-logic-btn"
            onClick={toggleLogic}
          >
            {parsed.logic}
          </button>
          <span>of the following</span>
        </div>
      )}

      {parsed.conditions.length > 0 && (
        <div className="conditions-list">
          {parsed.conditions.map((cond, i) => (
            <ConditionRow
              key={cond.id}
              condition={cond}
              index={i}
              logic={parsed.logic}
              showInlineLogic={parsed.conditions.length < 2}
              onChange={(patch) => updateCondition(cond.id, patch)}
              onRemove={() => removeCondition(cond.id)}
            />
          ))}
        </div>
      )}

      <button
        type="button"
        className="conditions-add-btn"
        onClick={addCondition}
      >
        <Plus size={12} weight="bold" />
        Add condition
      </button>
    </div>
  );
}

function ConditionRow({
  condition,
  index,
  logic,
  showInlineLogic,
  onChange,
  onRemove,
}: {
  condition: Condition;
  index: number;
  logic: ConditionLogic;
  showInlineLogic: boolean;
  onChange: (patch: Partial<Condition>) => void;
  onRemove: () => void;
}): JSX.Element {
  const operators = OPERATORS_BY_TYPE[condition.type];
  const needsValue = !NO_VALUE_OPERATORS.has(condition.operator);

  return (
    <div className="condition-row">
      {index > 0 && showInlineLogic && (
        <span className="condition-logic-label">{logic}</span>
      )}
      <input
        className="field-input condition-field-input"
        placeholder="field path"
        value={condition.field}
        onChange={(e) => onChange({ field: e.target.value })}
      />
      <select
        className="field-input condition-type-select"
        value={condition.type}
        onChange={(e) =>
          onChange({ type: e.target.value as ConditionType })
        }
        title="Data type"
      >
        {CONDITION_TYPES.map((t) => (
          <option key={t} value={t}>
            {TYPE_LABELS[t]}
          </option>
        ))}
      </select>
      <select
        className="field-input condition-op-select"
        value={condition.operator}
        onChange={(e) => onChange({ operator: e.target.value })}
      >
        {operators.map((op) => (
          <option key={op} value={op}>
            {op}
          </option>
        ))}
      </select>
      {needsValue && (
        <input
          className="field-input condition-value-input"
          placeholder="value"
          value={condition.value}
          onChange={(e) => onChange({ value: e.target.value })}
        />
      )}
      <button
        type="button"
        className="condition-remove-btn"
        onClick={onRemove}
        aria-label="Remove condition"
      >
        <Trash size={12} />
      </button>
    </div>
  );
}
