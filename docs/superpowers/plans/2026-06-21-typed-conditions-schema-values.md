# Typed Conditions & Schema Values Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add n8n-style data-type-aware multi-condition builders to the If and Filter nodes; augment the Schema tab in the DataPanel to show type icons and actual field values inline; show data-type badges on canvas edges on hover; add an NDV output coerce-preview panel; and add a "Set Field Type" node for single-field in-workflow type conversion.

**Architecture:** Five independent subsystems. (1) Schema view — pure frontend `DataPanel.tsx` change; `SchemaRow` gains a type icon and inline value preview. (2) Typed conditions engine — backend `_matches_typed`/`_eval_conditions` in `builtin.py`, frontend `ConditionsField.tsx` widget wired via `spec.widget === "conditions_builder"`. (3) Edge type badges — `NoodleEdge.tsx` reads `runOutputs` from the store, derives the JS type of the data flowing through the edge, and renders a hover-only badge in the mid-point label area. (4) NDV coerce preview — `DataPanel.tsx` output panel gets a "coerce view" selector that rerenders the current data after a client-side type conversion (no workflow change). (5) Set Field Type node — a new `set_field_type` backend node in `builtin.py` that converts a single named field of an object to the chosen type, passing the rest through unchanged.

**Tech Stack:** TypeScript/React (frontend), Python 3.12 (backend), Vitest + jsdom (frontend tests), pytest + uv (backend tests), Phosphor Icons (`@phosphor-icons/react`).

## Global Constraints

- Python ≥ 3.12 — use `datetime.fromisoformat()` for date parsing (handles ISO 8601 natively).
- No new Python dependencies — `re` and `datetime` are stdlib.
- No new npm packages — all UI built from existing design system tokens and Phosphor Icons.
- All CSS uses existing `var(--...)` tokens defined in `editor.css` — no hardcoded hex colors except where syntax highlighting demands it (precedent: `json-tree-*` classes).
- Backward compatibility: existing saved workflows with `field`/`operator`/`value` on If/Filter nodes MUST continue to execute correctly after this change.
- The `conditions_builder` widget is registered in `ParamField` via `spec.widget === "conditions_builder"` — same pattern as `routes_table`.
- Frontend test runner: `cd apps/web && npm run test -- <file>` (vitest run).
- Backend test runner: `uv run pytest <path> -v` from repo root.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `apps/web/src/editor/DataPanel.tsx` | Modify | `schemaTypeIcon()`, `schemaValuePreview()`, updated `SchemaRow`, coerce-preview selector |
| `apps/web/src/editor/DataPanel.test.tsx` | Modify | Schema view tests + coerce-preview tests |
| `apps/web/src/editor/ConditionsField.tsx` | Create | Typed multi-condition builder React component |
| `apps/web/src/editor/ConditionsField.test.tsx` | Create | Unit tests for ConditionsField logic |
| `apps/web/src/editor/NodeDetails.tsx` | Modify | Register `conditions_builder` widget dispatch in `ParamField` |
| `apps/web/src/editor/NoodleEdge.tsx` | Modify | Read `runOutputs`, derive edge data type, render hover badge |
| `apps/web/src/editor/NoodleEdge.test.ts` | Modify | Tests for `deriveEdgeType()` helper |
| `apps/web/src/editor.css` | Modify | CSS for schema icons/values, conditions builder, edge type badge, coerce selector |
| `packages/nodes/noodle_nodes/builtin.py` | Modify | `_matches_typed()`, `_eval_conditions()`, updated `if_node`/`filter_node`, new `set_field_type_node` |
| `packages/nodes/tests/test_if_conditions.py` | Create | Pytest tests for typed conditions engine |
| `packages/nodes/tests/test_set_field_type.py` | Create | Pytest tests for Set Field Type node |

---

### Task 1: Schema Type Icons and Value Previews

**Files:**
- Modify: `apps/web/src/editor/DataPanel.tsx` (functions `schemaType`, `SchemaRow`)
- Modify: `apps/web/src/editor/DataPanel.test.tsx`
- Modify: `apps/web/src/editor.css` (after line containing `.schema-children {`)

**Interfaces:**
- Produces: `schemaTypeIcon(value: unknown): string` — single-char/short string badge
- Produces: `schemaValuePreview(value: unknown): string | null` — truncated inline value or null for objects/arrays
- Produces: `schemaRawType(value: unknown): string` — data-type attribute string for CSS color targeting

- [ ] **Step 1: Write the failing tests**

Add to `apps/web/src/editor/DataPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataPanel } from "./DataPanel";

// ... existing tests ...

describe("DataPanel schema view", () => {
  it("shows type icon T for string fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ name: "Alice", age: 30 }}
        dragPrefix="$json"
      />,
    );
    // Switch to schema view — it's the default when dragPrefix is set and data is an object
    expect(screen.getByText("T")).toBeTruthy();
  });

  it("shows type icon # for number fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ count: 42 }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("#")).toBeTruthy();
  });

  it("shows type icon ⊤ for boolean fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ active: true }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("⊤")).toBeTruthy();
  });

  it("shows inline value for primitive string fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ city: "London" }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("London")).toBeTruthy();
  });

  it("shows inline value for number fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ score: 99 }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("99")).toBeTruthy();
  });

  it("truncates long string values to 40 chars with ellipsis", () => {
    const longStr = "a".repeat(60);
    render(
      <DataPanel
        title="Input"
        data={{ note: longStr }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("a".repeat(40) + "…")).toBeTruthy();
  });

  it("does not show inline value for object fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ meta: { x: 1 } }}
        dragPrefix="$json"
      />,
    );
    // The key "meta" appears but no inline value preview for objects
    expect(screen.getByText("meta")).toBeTruthy();
    expect(screen.queryByText('{"x":1}')).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify tests fail**

```
cd apps/web && npm run test -- DataPanel.test.tsx
```

Expected: FAILs on the schema view tests — `T`, `#`, `⊤`, `London` etc. not found.

- [ ] **Step 3: Add helper functions to DataPanel.tsx**

Locate the existing `schemaType` function (around line 454) and replace it plus the `SchemaRow` component:

```tsx
// Replace existing schemaType() at ~line 454
function schemaRawType(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value; // "string" | "number" | "boolean" | "object"
}

function schemaTypeIcon(value: unknown): string {
  const t = schemaRawType(value);
  if (t === "string") return "T";
  if (t === "number") return "#";
  if (t === "boolean") return "⊤";
  if (t === "array") return "[]";
  if (t === "object") return "{}";
  return "∅"; // null / undefined
}

function schemaValuePreview(value: unknown): string | null {
  if (value === null) return "null";
  if (value === undefined) return null;
  if (Array.isArray(value)) return null;
  if (typeof value === "object") return null;
  if (typeof value === "string") {
    return value.length > 40 ? value.slice(0, 40) + "…" : value;
  }
  return String(value);
}

// Keep schemaType for legacy display_name (still used in aria-label)
function schemaType(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (Array.isArray(value)) return `array[${value.length}]`;
  if (typeof value === "object") return "object";
  return typeof value;
}
```

- [ ] **Step 4: Update SchemaRow to show icon and value**

Replace the existing `SchemaRow` function (around line 467) with:

```tsx
function SchemaRow({
  label,
  value,
  path,
  dragPrefix,
}: {
  label: string;
  value: unknown;
  path: (string | number)[];
  dragPrefix?: string;
}): JSX.Element {
  const expandable =
    isPlainObject(value) || (Array.isArray(value) && value.length > 0);
  const [open, setOpen] = useState(path.length <= 1);
  const expr = dragPrefix ? buildExpression(dragPrefix, path) : undefined;
  const rawType = schemaRawType(value);
  const icon = schemaTypeIcon(value);
  const preview = schemaValuePreview(value);

  return (
    <div className="schema-node">
      <div
        className="schema-row"
        draggable={Boolean(expr)}
        onDragStart={expr ? (e) => startExpressionDrag(e, expr) : undefined}
        title={expr ? `Drag to insert ${expr} (${schemaType(value)})` : schemaType(value)}
      >
        {expandable ? (
          <button
            type="button"
            className="schema-twist"
            onClick={() => setOpen((o) => !o)}
            aria-label={open ? "Collapse" : "Expand"}
          >
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="schema-twist-spacer" aria-hidden />
        )}
        {expr && (
          <span className="schema-grip" aria-hidden>
            ⠿
          </span>
        )}
        <span
          className="schema-type-icon"
          data-type={rawType}
          aria-label={schemaType(value)}
        >
          {icon}
        </span>
        <span className="schema-key">{label}</span>
        {preview !== null && (
          <span className="schema-value" data-type={rawType}>
            {preview}
          </span>
        )}
        {expandable && (
          <span className="schema-type">{schemaType(value)}</span>
        )}
      </div>
      {expandable && open && (
        <div className="schema-children">
          {isPlainObject(value)
            ? Object.entries(value).map(([k, v]) => (
                <SchemaRow
                  key={k}
                  label={k}
                  value={v}
                  path={[...path, k]}
                  dragPrefix={dragPrefix}
                />
              ))
            : Array.isArray(value) && value.length > 0
              ? (
                <SchemaRow
                  label="0"
                  value={value[0]}
                  path={[...path, 0]}
                  dragPrefix={dragPrefix}
                />
              )
              : null}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Add CSS for schema type icons and values**

In `apps/web/src/editor.css`, find the `.schema-children {` block (around line 7670) and add after the closing brace `}`:

```css
/* Schema type icon badge */
.schema-type-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
  flex-shrink: 0;
  background: var(--surface-2);
  color: var(--ink-3);
  padding: 0 2px;
}
.schema-type-icon[data-type="string"]  { color: #4ec9b0; }
.schema-type-icon[data-type="number"]  { color: #9cdcfe; }
.schema-type-icon[data-type="boolean"] { color: #ce9178; }
.schema-type-icon[data-type="object"]  { color: #dcdcaa; }
.schema-type-icon[data-type="array"]   { color: #dcdcaa; }

/* Inline value preview shown after the field key */
.schema-value {
  color: var(--ink-3);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
  flex: 1;
}
.schema-value[data-type="string"]  { color: #ce9178; }
.schema-value[data-type="number"]  { color: #9cdcfe; }
.schema-value[data-type="boolean"] { color: #569cd6; }
.schema-value[data-type="null"]    { color: var(--ink-3); font-style: italic; }
```

- [ ] **Step 6: Run tests to verify they pass**

```
cd apps/web && npm run test -- DataPanel.test.tsx
```

Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/editor/DataPanel.tsx apps/web/src/editor/DataPanel.test.tsx apps/web/src/editor.css
git commit -m "feat(ui): add type icons and value previews to schema view"
```

---

### Task 2: Typed Conditions Engine (Backend)

**Files:**
- Modify: `packages/nodes/noodle_nodes/builtin.py`
- Create: `packages/nodes/tests/test_if_conditions.py`

**Interfaces:**
- Produces: `_matches_typed(actual: Any, dtype: str, operator: str, value: str = "") -> bool`
- Produces: `_eval_conditions(input_data: Any, conditions_param: Any) -> bool`
- Consumes: `_field(value, field)` — already exists at line 34

- [ ] **Step 1: Create test file with failing tests**

Create `packages/nodes/tests/test_if_conditions.py`:

```python
"""Tests for typed multi-condition evaluation (_matches_typed, _eval_conditions)."""
import pytest
from noodle_nodes.builtin import _matches_typed, _eval_conditions


# ---------------------------------------------------------------------------
# _matches_typed — string operators
# ---------------------------------------------------------------------------

def test_string_equals():
    assert _matches_typed("open", "string", "equals", "open") is True
    assert _matches_typed("open", "string", "equals", "closed") is False

def test_string_not_equals():
    assert _matches_typed("open", "string", "not equals", "closed") is True

def test_string_contains():
    assert _matches_typed("hello world", "string", "contains", "world") is True
    assert _matches_typed("hello", "string", "contains", "xyz") is False

def test_string_does_not_contain():
    assert _matches_typed("hello", "string", "does not contain", "xyz") is True

def test_string_starts_with():
    assert _matches_typed("foobar", "string", "starts with", "foo") is True
    assert _matches_typed("foobar", "string", "starts with", "bar") is False

def test_string_ends_with():
    assert _matches_typed("foobar", "string", "ends with", "bar") is True

def test_string_is_empty():
    assert _matches_typed("", "string", "is empty") is True
    assert _matches_typed("x", "string", "is empty") is False

def test_string_is_not_empty():
    assert _matches_typed("x", "string", "is not empty") is True
    assert _matches_typed("", "string", "is not empty") is False

def test_string_matches_regex():
    assert _matches_typed("abc123", "string", "matches regex", r"\d+") is True
    assert _matches_typed("abcdef", "string", "matches regex", r"\d+") is False

def test_string_matches_regex_invalid_pattern_returns_false():
    assert _matches_typed("abc", "string", "matches regex", "[invalid") is False


# ---------------------------------------------------------------------------
# _matches_typed — number operators
# ---------------------------------------------------------------------------

def test_number_equals():
    assert _matches_typed(42, "number", "equals", "42") is True
    assert _matches_typed(42, "number", "equals", "43") is False

def test_number_not_equals():
    assert _matches_typed(1, "number", "not equals", "2") is True

def test_number_greater_than():
    assert _matches_typed(10, "number", "greater than", "5") is True
    assert _matches_typed(3, "number", "greater than", "5") is False

def test_number_greater_than_or_equal():
    assert _matches_typed(5, "number", "greater than or equal", "5") is True
    assert _matches_typed(4, "number", "greater than or equal", "5") is False

def test_number_less_than():
    assert _matches_typed(3, "number", "less than", "5") is True

def test_number_less_than_or_equal():
    assert _matches_typed(5, "number", "less than or equal", "5") is True

def test_number_non_numeric_actual_returns_false():
    assert _matches_typed("hello", "number", "equals", "5") is False

def test_number_non_numeric_value_returns_false():
    assert _matches_typed(5, "number", "equals", "not-a-number") is False


# ---------------------------------------------------------------------------
# _matches_typed — boolean operators
# ---------------------------------------------------------------------------

def test_boolean_is_true():
    assert _matches_typed(True, "boolean", "is true") is True
    assert _matches_typed(False, "boolean", "is true") is False

def test_boolean_is_false():
    assert _matches_typed(False, "boolean", "is false") is True
    assert _matches_typed(True, "boolean", "is false") is False

def test_boolean_string_false():
    assert _matches_typed("false", "boolean", "is true") is False

def test_boolean_string_true():
    assert _matches_typed("true", "boolean", "is true") is True


# ---------------------------------------------------------------------------
# _matches_typed — array operators
# ---------------------------------------------------------------------------

def test_array_is_empty():
    assert _matches_typed([], "array", "is empty") is True
    assert _matches_typed([1], "array", "is empty") is False

def test_array_is_not_empty():
    assert _matches_typed([1, 2], "array", "is not empty") is True

def test_array_contains():
    assert _matches_typed(["a", "b"], "array", "contains", "a") is True
    assert _matches_typed(["a", "b"], "array", "contains", "c") is False

def test_array_does_not_contain():
    assert _matches_typed(["a"], "array", "does not contain", "b") is True

def test_array_length_equals():
    assert _matches_typed([1, 2, 3], "array", "length equals", "3") is True
    assert _matches_typed([1, 2], "array", "length equals", "3") is False

def test_array_length_not_equals():
    assert _matches_typed([1, 2], "array", "length not equals", "3") is True

def test_array_length_greater_than():
    assert _matches_typed([1, 2, 3], "array", "length greater than", "2") is True

def test_array_length_less_than():
    assert _matches_typed([1], "array", "length less than", "5") is True

def test_array_non_list_treated_as_empty():
    assert _matches_typed("not-a-list", "array", "is empty") is True


# ---------------------------------------------------------------------------
# _matches_typed — object operators
# ---------------------------------------------------------------------------

def test_object_has_key():
    assert _matches_typed({"name": "Alice"}, "object", "has key", "name") is True
    assert _matches_typed({"name": "Alice"}, "object", "has key", "age") is False

def test_object_does_not_have_key():
    assert _matches_typed({"a": 1}, "object", "does not have key", "b") is True

def test_object_is_empty():
    assert _matches_typed({}, "object", "is empty") is True
    assert _matches_typed({"a": 1}, "object", "is empty") is False

def test_object_is_not_empty():
    assert _matches_typed({"k": "v"}, "object", "is not empty") is True

def test_object_non_dict_treated_as_empty():
    assert _matches_typed("not-a-dict", "object", "is empty") is True


# ---------------------------------------------------------------------------
# _matches_typed — date operators
# ---------------------------------------------------------------------------

def test_date_before():
    assert _matches_typed("2024-01-01", "date", "before", "2025-01-01") is True
    assert _matches_typed("2026-01-01", "date", "before", "2025-01-01") is False

def test_date_after():
    assert _matches_typed("2026-01-01", "date", "after", "2025-01-01") is True

def test_date_equals_same_day():
    assert _matches_typed("2025-06-15T10:00:00", "date", "equals", "2025-06-15T18:00:00") is True
    assert _matches_typed("2025-06-15", "date", "equals", "2025-06-16") is False

def test_date_invalid_format_returns_false():
    assert _matches_typed("not-a-date", "date", "before", "2025-01-01") is False


# ---------------------------------------------------------------------------
# _matches_typed — any operators
# ---------------------------------------------------------------------------

def test_any_exists():
    assert _matches_typed("value", "any", "exists") is True
    assert _matches_typed(None, "any", "exists") is False

def test_any_does_not_exist():
    assert _matches_typed(None, "any", "does not exist") is True
    assert _matches_typed(0, "any", "does not exist") is False

def test_any_is_empty():
    assert _matches_typed(None, "any", "is empty") is True
    assert _matches_typed("", "any", "is empty") is True
    assert _matches_typed([], "any", "is empty") is True
    assert _matches_typed("x", "any", "is empty") is False

def test_any_is_not_empty():
    assert _matches_typed("x", "any", "is not empty") is True
    assert _matches_typed(None, "any", "is not empty") is False


# ---------------------------------------------------------------------------
# _eval_conditions
# ---------------------------------------------------------------------------

def test_eval_conditions_empty_list_passes():
    assert _eval_conditions({"x": 1}, {"logic": "AND", "conditions": []}) is True

def test_eval_conditions_none_returns_false():
    assert _eval_conditions({"x": 1}, None) is False

def test_eval_conditions_and_all_pass():
    cond = {
        "logic": "AND",
        "conditions": [
            {"field": "status", "type": "string", "operator": "equals", "value": "open"},
            {"field": "count", "type": "number", "operator": "greater than", "value": "0"},
        ],
    }
    assert _eval_conditions({"status": "open", "count": 5}, cond) is True

def test_eval_conditions_and_one_fails():
    cond = {
        "logic": "AND",
        "conditions": [
            {"field": "status", "type": "string", "operator": "equals", "value": "open"},
            {"field": "count", "type": "number", "operator": "greater than", "value": "10"},
        ],
    }
    assert _eval_conditions({"status": "open", "count": 5}, cond) is False

def test_eval_conditions_or_one_passes():
    cond = {
        "logic": "OR",
        "conditions": [
            {"field": "status", "type": "string", "operator": "equals", "value": "closed"},
            {"field": "count", "type": "number", "operator": "greater than", "value": "0"},
        ],
    }
    assert _eval_conditions({"status": "open", "count": 5}, cond) is True

def test_eval_conditions_or_all_fail():
    cond = {
        "logic": "OR",
        "conditions": [
            {"field": "status", "type": "string", "operator": "equals", "value": "closed"},
            {"field": "count", "type": "number", "operator": "greater than", "value": "100"},
        ],
    }
    assert _eval_conditions({"status": "open", "count": 5}, cond) is False

def test_eval_conditions_blank_field_tests_whole_input():
    cond = {
        "logic": "AND",
        "conditions": [
            {"field": "", "type": "any", "operator": "exists"},
        ],
    }
    assert _eval_conditions("hello", cond) is True
    assert _eval_conditions(None, cond) is False

def test_eval_conditions_skips_non_dict_entries():
    cond = {
        "logic": "AND",
        "conditions": ["not-a-dict", None, 42],
    }
    # All entries skipped → vacuously true
    assert _eval_conditions({"x": 1}, cond) is True
```

- [ ] **Step 2: Run to verify tests fail**

```
uv run pytest packages/nodes/tests/test_if_conditions.py -v 2>&1 | head -30
```

Expected: `ImportError` — `_matches_typed` and `_eval_conditions` do not exist yet.

- [ ] **Step 3: Implement `_matches_typed` and `_eval_conditions` in `builtin.py`**

Add `import re` and `from datetime import datetime` at the top of `builtin.py` (after the existing imports), then add the two functions after the existing `_as_list` function (around line 64):

```python
import re
from datetime import datetime
```

Add after `_as_list` (around line 67):

```python
def _matches_typed(actual: Any, dtype: str, operator: str, value: str = "") -> bool:
    """Evaluate a single typed condition against a value.

    dtype must be one of: string, number, boolean, array, object, date, any.
    operator must be valid for the given dtype.  Returns False for unknown
    dtype/operator combinations rather than raising.
    """
    if dtype == "string":
        s = str(actual) if actual is not None else ""
        v = str(value)
        if operator == "equals":           return s == v
        if operator == "not equals":       return s != v
        if operator == "contains":         return v.lower() in s.lower()
        if operator == "does not contain": return v.lower() not in s.lower()
        if operator == "starts with":      return s.lower().startswith(v.lower())
        if operator == "ends with":        return s.lower().endswith(v.lower())
        if operator == "is empty":         return s == ""
        if operator == "is not empty":     return s != ""
        if operator == "matches regex":
            try:
                return bool(re.search(v, s))
            except re.error:
                return False

    elif dtype == "number":
        try:
            n = float(actual)
            v_num = float(value)
        except (TypeError, ValueError):
            return False
        if operator == "equals":                 return n == v_num
        if operator == "not equals":             return n != v_num
        if operator == "greater than":           return n > v_num
        if operator == "greater than or equal":  return n >= v_num
        if operator == "less than":              return n < v_num
        if operator == "less than or equal":     return n <= v_num

    elif dtype == "boolean":
        # Coerce: the string literals "false"/"0"/"no"/"off"/"" are falsy.
        if isinstance(actual, bool):
            b = actual
        elif isinstance(actual, str):
            b = actual.lower() not in ("false", "0", "no", "off", "")
        else:
            b = bool(actual)
        if operator == "is true":  return b
        if operator == "is false": return not b

    elif dtype == "array":
        arr = actual if isinstance(actual, list) else []
        if operator == "is empty":       return len(arr) == 0
        if operator == "is not empty":   return len(arr) > 0
        if operator == "contains":       return str(value) in [str(x) for x in arr]
        if operator == "does not contain": return str(value) not in [str(x) for x in arr]
        try:
            v_len = int(value)
        except (TypeError, ValueError):
            return False
        if operator == "length equals":       return len(arr) == v_len
        if operator == "length not equals":   return len(arr) != v_len
        if operator == "length greater than": return len(arr) > v_len
        if operator == "length less than":    return len(arr) < v_len

    elif dtype == "object":
        obj = actual if isinstance(actual, dict) else {}
        if operator == "has key":           return str(value) in obj
        if operator == "does not have key": return str(value) not in obj
        if operator == "is empty":          return len(obj) == 0
        if operator == "is not empty":      return len(obj) > 0

    elif dtype == "date":
        try:
            d_actual = datetime.fromisoformat(str(actual))
            d_value = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return False
        if operator == "before": return d_actual < d_value
        if operator == "after":  return d_actual > d_value
        if operator == "equals": return d_actual.date() == d_value.date()

    elif dtype == "any":
        if operator == "exists":         return actual is not None
        if operator == "does not exist": return actual is None
        if operator == "is empty":       return actual in (None, "", [], {})
        if operator == "is not empty":   return actual not in (None, "", [], {})

    return False


def _eval_conditions(input_data: Any, conditions_param: Any) -> bool:
    """Evaluate a conditions_builder param value against input_data.

    conditions_param must be ``{"logic": "AND"|"OR", "conditions": [...]}``.
    Returns False if conditions_param is not a dict.  Returns True for an
    empty conditions list (vacuous truth — no constraint means pass all).
    """
    if not isinstance(conditions_param, dict):
        return False
    logic = str(conditions_param.get("logic", "AND")).upper()
    conditions = conditions_param.get("conditions") or []
    results: list[bool] = []
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        field = str(cond.get("field", ""))
        dtype = str(cond.get("type", "any"))
        operator = str(cond.get("operator", "exists"))
        value = str(cond.get("value", ""))
        actual = _field(input_data, field) if field else input_data
        results.append(_matches_typed(actual, dtype, operator, value))
    if not results:
        return True  # empty conditions list — pass everything
    return all(results) if logic == "AND" else any(results)
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest packages/nodes/tests/test_if_conditions.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/builtin.py packages/nodes/tests/test_if_conditions.py
git commit -m "feat(nodes): add typed multi-condition engine (_matches_typed, _eval_conditions)"
```

---

### Task 3: Update If and Filter Nodes with `conditions` Param

**Files:**
- Modify: `packages/nodes/noodle_nodes/builtin.py` (the `@node` decorators and functions for `if_node` and `filter_node`)
- Modify: `packages/nodes/tests/test_if_conditions.py` (add integration tests using full graph execution)

**Interfaces:**
- Consumes: `_eval_conditions` from Task 2
- Consumes: `_matches`, `_field`, `_as_list` — all already present in builtin.py
- Produces: `if_node` now accepts `conditions: Any = None` param; `filter_node` same
- Produces: node manifests expose `conditions` with `widget="conditions_builder"` and `field`/`operator`/`value` in `group="Legacy condition"`

- [ ] **Step 1: Write integration tests (failing)**

Append to `packages/nodes/tests/test_if_conditions.py`:

```python
# ---------------------------------------------------------------------------
# Integration: full graph execution via if_node / filter_node
# ---------------------------------------------------------------------------
import pytest
import noodle_nodes  # noqa: F401 — registers built-in nodes
from noodle.engine import execute
from noodle.models import Edge, GraphNode, NodeStatus, WorkflowGraph


@pytest.mark.asyncio
async def test_if_node_typed_conditions_true_branch():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"age": 25}}),
            GraphNode(
                id="i",
                type="if",
                params={
                    "conditions": {
                        "logic": "AND",
                        "conditions": [
                            {"id": "c1", "field": "age", "type": "number",
                             "operator": "greater than or equal", "value": "18"},
                        ],
                    }
                },
            ),
            GraphNode(id="yes", type="no_op"),
        ],
        edges=[
            Edge(source="t", target="i"),
            Edge(source="i", source_output="true", target="yes"),
        ],
    )
    result = await execute(graph, registry)
    assert result.nodes["yes"].status == NodeStatus.success
    assert result.nodes["yes"].outputs["main"] == {"age": 25}


@pytest.mark.asyncio
async def test_if_node_typed_conditions_false_branch():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"age": 10}}),
            GraphNode(
                id="i",
                type="if",
                params={
                    "conditions": {
                        "logic": "AND",
                        "conditions": [
                            {"id": "c1", "field": "age", "type": "number",
                             "operator": "greater than or equal", "value": "18"},
                        ],
                    }
                },
            ),
            GraphNode(id="no", type="no_op"),
        ],
        edges=[
            Edge(source="t", target="i"),
            Edge(source="i", source_output="false", target="no"),
        ],
    )
    result = await execute(graph, registry)
    assert result.nodes["no"].status == NodeStatus.success


@pytest.mark.asyncio
async def test_if_node_legacy_params_still_work():
    """Existing saved workflows with field/operator/value must keep working."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"status": "open"}}),
            GraphNode(
                id="i",
                type="if",
                params={"field": "status", "operator": "equals", "value": "open"},
            ),
            GraphNode(id="yes", type="no_op"),
        ],
        edges=[
            Edge(source="t", target="i"),
            Edge(source="i", source_output="true", target="yes"),
        ],
    )
    result = await execute(graph, registry)
    assert result.nodes["yes"].status == NodeStatus.success


@pytest.mark.asyncio
async def test_filter_node_typed_conditions():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": [{"score": 90}, {"score": 40}, {"score": 75}]},
            ),
            GraphNode(
                id="f",
                type="filter",
                params={
                    "conditions": {
                        "logic": "AND",
                        "conditions": [
                            {"id": "c1", "field": "score", "type": "number",
                             "operator": "greater than or equal", "value": "70"},
                        ],
                    }
                },
            ),
        ],
        edges=[Edge(source="t", target="f")],
    )
    result = await execute(graph, registry)
    out = result.nodes["f"].outputs["main"]
    assert isinstance(out, list)
    assert len(out) == 2
    assert all(row["score"] >= 70 for row in out)


@pytest.mark.asyncio
async def test_filter_node_or_logic():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": [
                    {"status": "open", "priority": "low"},
                    {"status": "closed", "priority": "high"},
                    {"status": "closed", "priority": "low"},
                ]},
            ),
            GraphNode(
                id="f",
                type="filter",
                params={
                    "conditions": {
                        "logic": "OR",
                        "conditions": [
                            {"id": "c1", "field": "status", "type": "string",
                             "operator": "equals", "value": "open"},
                            {"id": "c2", "field": "priority", "type": "string",
                             "operator": "equals", "value": "high"},
                        ],
                    }
                },
            ),
        ],
        edges=[Edge(source="t", target="f")],
    )
    result = await execute(graph, registry)
    out = result.nodes["f"].outputs["main"]
    assert len(out) == 2  # "open/low" and "closed/high" pass; "closed/low" fails
```

Note: The import block at the top of the test file needs `from noodle.sdk import registry`. Ensure the top of `test_if_conditions.py` has:
```python
import pytest
import noodle_nodes  # noqa: F401
from noodle.engine import execute
from noodle.models import Edge, GraphNode, NodeStatus, WorkflowGraph
from noodle.sdk import registry
from noodle_nodes.builtin import _matches_typed, _eval_conditions
```

- [ ] **Step 2: Run integration tests to verify they fail**

```
uv run pytest packages/nodes/tests/test_if_conditions.py -k "integration or if_node or filter_node" -v 2>&1 | head -20
```

Expected: `TypeError` — `if_node`/`filter_node` don't accept `conditions` yet.

- [ ] **Step 3: Update `if_node` in builtin.py**

Replace the existing `@node` decorator + `if_node` function (lines ~423–433):

```python
@node(name="If", id="if", category="Logic", icon="branch", outputs=["true", "false"],
      params={
          "conditions": {
              "widget": "conditions_builder",
              "description": (
                  "Typed multi-condition test. Each row specifies a field path, "
                  "data type, operator, and comparison value. Use the logic toggle "
                  "to require ALL (AND) or ANY (OR) conditions to match."
              ),
          },
          "field": {
              "placeholder": "status",
              "description": "Field to test (blank = whole input). Legacy: use Conditions above.",
              "group": "Legacy condition",
          },
          "operator": {
              "choices": OPERATORS,
              "group": "Legacy condition",
          },
          "value": {
              "placeholder": "expected value",
              "group": "Legacy condition",
          },
      })
def if_node(
    input: Any = None,
    field: str = "",
    operator: str = "is true",
    value: str = "",
    conditions: Any = None,
) -> dict:
    """Route the input to the true or false branch based on a condition."""
    if conditions:
        matched = _eval_conditions(input, conditions)
    else:
        matched = _matches(_field(input, field), operator, value)
    return {"true": input} if matched else {"false": input}
```

- [ ] **Step 4: Update `filter_node` in builtin.py**

Replace the existing `@node` decorator + `filter_node` function (lines ~462–479):

```python
@node(name="Filter", id="filter", category="Logic", icon="filter",
      params={
          "conditions": {
              "widget": "conditions_builder",
              "description": (
                  "Typed multi-condition filter. Items passing all (AND) or any "
                  "(OR) conditions flow through; others are dropped."
              ),
          },
          "field": {
              "placeholder": "status",
              "group": "Legacy condition",
          },
          "operator": {
              "choices": OPERATORS,
              "group": "Legacy condition",
          },
          "value": {
              "placeholder": "expected value",
              "group": "Legacy condition",
          },
      })
def filter_node(
    input: Any = None,
    field: str = "",
    operator: str = "is not empty",
    value: str = "",
    conditions: Any = None,
) -> Any:
    """Keep only the input items that satisfy a condition.

    When the upstream input is a single object (not a list), a passing filter
    returns that object directly so the next node receives the same shape it
    would have received without the filter.  When the input is already a list,
    the output is always a list (possibly empty).
    """
    input_was_list = isinstance(input, list)
    if conditions:
        results = [it for it in _as_list(input) if _eval_conditions(it, conditions)]
    else:
        results = [it for it in _as_list(input) if _matches(_field(it, field), operator, value)]
    if not input_was_list and len(results) == 1:
        return results[0]
    return results
```

- [ ] **Step 5: Run all if-conditions tests**

```
uv run pytest packages/nodes/tests/test_if_conditions.py -v
```

Expected: ALL PASS (both unit and integration tests).

- [ ] **Step 6: Run the full node test suite to check for regressions**

```
uv run pytest packages/nodes/tests/test_builtin_nodes.py -v
```

Expected: ALL PASS (especially `test_if_routes_false_branch` and `test_switch_routes_via_dynamic_rules`).

- [ ] **Step 7: Commit**

```bash
git add packages/nodes/noodle_nodes/builtin.py packages/nodes/tests/test_if_conditions.py
git commit -m "feat(nodes): add typed conditions param to If and Filter nodes with legacy fallback"
```

---

### Task 4: ConditionsField Frontend Widget

**Files:**
- Create: `apps/web/src/editor/ConditionsField.tsx`
- Create: `apps/web/src/editor/ConditionsField.test.tsx`
- Modify: `apps/web/src/editor.css` (append after Task 1's schema CSS additions)

**Interfaces:**
- Produces: `export function ConditionsField({ value, onChange, allParams }: ConditionsFieldProps): JSX.Element`
- Produces: `export type { ConditionsValue, Condition, ConditionType }` — consumed by tests
- Consumes: `@phosphor-icons/react` — `Plus`, `Trash` icons (already a dependency)

- [ ] **Step 1: Write the failing component tests**

Create `apps/web/src/editor/ConditionsField.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConditionsField } from "./ConditionsField";

describe("ConditionsField", () => {
  it("renders Add condition button when value is null", () => {
    render(<ConditionsField value={null} onChange={() => {}} />);
    expect(screen.getByText("Add condition")).toBeTruthy();
  });

  it("renders Add condition button when value is empty conditions array", () => {
    render(
      <ConditionsField
        value={{ logic: "AND", conditions: [] }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("Add condition")).toBeTruthy();
  });

  it("calls onChange with a new condition when Add condition is clicked", () => {
    const onChange = vi.fn();
    render(<ConditionsField value={null} onChange={onChange} />);
    fireEvent.click(screen.getByText("Add condition"));
    expect(onChange).toHaveBeenCalledOnce();
    const arg = onChange.mock.calls[0][0];
    expect(arg).toMatchObject({
      logic: "AND",
      conditions: [expect.objectContaining({ type: "string", operator: "equals" })],
    });
  });

  it("renders existing conditions", () => {
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "status", type: "string", operator: "equals", value: "open" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByDisplayValue("status")).toBeTruthy();
    expect(screen.getByDisplayValue("open")).toBeTruthy();
  });

  it("removes a condition when the remove button is clicked", () => {
    const onChange = vi.fn();
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "status", type: "string", operator: "equals", value: "open" },
          ],
        }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByLabelText("Remove condition"));
    expect(onChange).toHaveBeenCalledWith(null); // null because conditions is now empty
  });

  it("shows logic toggle (AND/OR) only when there are 2+ conditions", () => {
    const { rerender } = render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "a", type: "string", operator: "equals", value: "x" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    // With 1 condition, no logic toggle
    expect(screen.queryByText("AND")).toBeNull();

    rerender(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "a", type: "string", operator: "equals", value: "x" },
            { id: "c2", field: "b", type: "string", operator: "equals", value: "y" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    // With 2 conditions, the AND toggle appears
    expect(screen.getByText("AND")).toBeTruthy();
  });

  it("toggles logic from AND to OR when toggle button is clicked", () => {
    const onChange = vi.fn();
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "a", type: "string", operator: "equals", value: "x" },
            { id: "c2", field: "b", type: "string", operator: "equals", value: "y" },
          ],
        }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByText("AND"));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ logic: "OR" }),
    );
  });

  it("resets operator to first valid when type changes", () => {
    const onChange = vi.fn();
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "tags", type: "string", operator: "contains", value: "vip" },
          ],
        }}
        onChange={onChange}
      />,
    );
    // Change type to "boolean" — "contains" is not a valid boolean operator
    const typeSelect = screen.getByTitle("Data type");
    fireEvent.change(typeSelect, { target: { value: "boolean" } });
    const updated = onChange.mock.calls[0][0];
    const cond = updated.conditions[0];
    expect(cond.type).toBe("boolean");
    expect(["is true", "is false"]).toContain(cond.operator);
  });

  it("hides value input for no-value operators like 'is empty'", () => {
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "note", type: "string", operator: "is empty", value: "" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    expect(screen.queryByPlaceholderText("value")).toBeNull();
  });

  it("shows value input for operators that need a value", () => {
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "name", type: "string", operator: "equals", value: "Alice" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByDisplayValue("Alice")).toBeTruthy();
  });

  it("shows legacy migration banner when conditions empty but allParams has field set", () => {
    render(
      <ConditionsField
        value={null}
        onChange={() => {}}
        allParams={{ field: "status", operator: "equals", value: "open" }}
      />,
    );
    expect(screen.getByText(/legacy single-condition/i)).toBeTruthy();
    expect(screen.getByText("Migrate →")).toBeTruthy();
  });

  it("does not show legacy banner when conditions has entries", () => {
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "x", type: "string", operator: "equals", value: "y" },
          ],
        }}
        onChange={() => {}}
        allParams={{ field: "status", operator: "equals", value: "open" }}
      />,
    );
    expect(screen.queryByText(/legacy/i)).toBeNull();
  });

  it("calls onChange with migrated condition when Migrate button is clicked", () => {
    const onChange = vi.fn();
    render(
      <ConditionsField
        value={null}
        onChange={onChange}
        allParams={{ field: "score", operator: "greater than", value: "10" }}
      />,
    );
    fireEvent.click(screen.getByText("Migrate →"));
    const arg = onChange.mock.calls[0][0];
    expect(arg.conditions[0]).toMatchObject({
      field: "score",
      operator: "greater than",
      value: "10",
    });
  });
});
```

- [ ] **Step 2: Run to verify tests fail**

```
cd apps/web && npm run test -- ConditionsField.test.tsx
```

Expected: `Error: Failed to resolve import "./ConditionsField"`.

- [ ] **Step 3: Create `ConditionsField.tsx`**

Create `apps/web/src/editor/ConditionsField.tsx`:

```tsx
import { Plus, Trash } from "@phosphor-icons/react";
import { useState } from "react";

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
  onChange,
  onRemove,
}: {
  condition: Condition;
  index: number;
  logic: ConditionLogic;
  onChange: (patch: Partial<Condition>) => void;
  onRemove: () => void;
}): JSX.Element {
  const operators = OPERATORS_BY_TYPE[condition.type];
  const needsValue = !NO_VALUE_OPERATORS.has(condition.operator);

  return (
    <div className="condition-row">
      {index > 0 && (
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
```

- [ ] **Step 4: Add conditions builder CSS to editor.css**

Append to `apps/web/src/editor.css` (after the schema CSS added in Task 1):

```css
/* ---- Conditions builder ---- */
.conditions-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.conditions-legacy-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: var(--surface-2);
  border-radius: 5px;
  font-size: 11px;
  color: var(--ink-3);
  flex-wrap: wrap;
}

.conditions-logic-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--ink-3);
}

.conditions-logic-btn {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--line-2);
  background: var(--surface-2);
  color: var(--accent);
  cursor: pointer;
}

.conditions-logic-btn:hover {
  background: var(--surface-3);
}

.conditions-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.condition-row {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.condition-logic-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--accent);
  width: 26px;
  flex-shrink: 0;
  text-align: center;
}

.condition-field-input {
  flex: 1;
  min-width: 80px;
}

.condition-type-select {
  width: 110px;
  flex-shrink: 0;
}

.condition-op-select {
  flex: 1.2;
  min-width: 120px;
}

.condition-value-input {
  flex: 1;
  min-width: 80px;
}

.condition-remove-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  border-radius: 4px;
  flex-shrink: 0;
  padding: 0;
}

.condition-remove-btn:hover {
  color: #f08c8c;
  background: var(--surface-2);
}

.conditions-add-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  font-size: 11px;
  color: var(--ink-3);
  background: none;
  border: 1px dashed var(--line-2);
  border-radius: 5px;
  padding: 5px 8px;
  cursor: pointer;
  width: 100%;
}

.conditions-add-btn:hover {
  color: var(--ink);
  border-color: var(--line-1);
  background: var(--surface-2);
}
```

- [ ] **Step 5: Run component tests to verify they pass**

```
cd apps/web && npm run test -- ConditionsField.test.tsx
```

Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/ConditionsField.tsx apps/web/src/editor/ConditionsField.test.tsx apps/web/src/editor.css
git commit -m "feat(ui): add ConditionsField typed multi-condition builder widget"
```

---

### Task 5: Wire ConditionsField into NodeDetails

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`

**Interfaces:**
- Consumes: `ConditionsField` from `./ConditionsField`
- The dispatch goes in `ParamField` immediately after the existing `spec.widget === "routes_table"` block (line ~3128)
- `credentialContext` (which is `params`) is passed as `allParams` so the widget can read legacy field values

- [ ] **Step 1: Write a smoke test**

Add to `apps/web/src/editor/DataPanel.test.tsx` or create a new file if preferred. For simplicity, add to the existing test file a minimal proof that `ConditionsField` renders through `ParamField` — but `ParamField` requires a complex setup. Instead, verify via typecheck that the import compiles correctly. The real test is Task 4's `ConditionsField.test.tsx`. This step is just a type-safety check:

```
cd apps/web && npm run typecheck
```

Expected: PASS (after the edit in Step 2 below, the import must compile).

- [ ] **Step 2: Add ConditionsField import to NodeDetails.tsx**

At the top of `apps/web/src/editor/NodeDetails.tsx`, add this import alongside the other local imports (around line 32–70):

```tsx
import { ConditionsField } from "./ConditionsField";
```

- [ ] **Step 3: Register `conditions_builder` widget in `ParamField`**

In `apps/web/src/editor/NodeDetails.tsx`, find the block:

```tsx
  if (spec.widget === "routes_table") {
    return <RoutesField value={value} onChange={onChange} />;
  }
```

(around line 3128). Add the `conditions_builder` dispatch immediately after it:

```tsx
  if (spec.widget === "conditions_builder") {
    return (
      <ConditionsField
        value={value}
        onChange={onChange}
        allParams={credentialContext}
      />
    );
  }
```

- [ ] **Step 4: Run typecheck**

```
cd apps/web && npm run typecheck
```

Expected: No errors.

- [ ] **Step 5: Run full frontend test suite**

```
cd apps/web && npm run test
```

Expected: ALL PASS — no regressions.

- [ ] **Step 6: Run backend test suite**

```
uv run pytest packages/nodes/tests/test_builtin_nodes.py packages/nodes/tests/test_if_conditions.py -v
```

Expected: ALL PASS.

- [ ] **Step 7: Final commit**

```bash
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(ui): wire conditions_builder widget into ParamField for If/Filter nodes"
```

---

---

### Task 6: Edge Data-Type Badge on Hover (Feature C)

**Files:**
- Modify: `apps/web/src/editor/NoodleEdge.tsx`
- Modify: `apps/web/src/editor/NoodleEdge.test.ts`
- Modify: `apps/web/src/editor.css`

**Interfaces:**
- Produces: `export function deriveEdgeType(value: unknown): { icon: string; label: string }` — exported for tests
- Consumes: `useEditor((s) => s.runOutputs)` — already imported in this file

The badge lives inside the existing `.noodle-edge-mid` div. It is invisible at rest and fades in on hover alongside the add/delete action buttons, using the same `opacity` transition. The type is derived from the actual runtime output value for the source node's output port (`runOutputs[source]?.[sourceHandle ?? "main"]`).

- [ ] **Step 1: Write failing tests for `deriveEdgeType`**

Add to `apps/web/src/editor/NoodleEdge.test.ts`:

```ts
import { deriveEdgeType } from "./NoodleEdge";

describe("deriveEdgeType", () => {
  it("returns string icon for a string value", () => {
    expect(deriveEdgeType("hello")).toEqual({ icon: "T", label: "string" });
  });

  it("returns number icon for a number value", () => {
    expect(deriveEdgeType(42)).toEqual({ icon: "#", label: "number" });
  });

  it("returns boolean icon for a boolean value", () => {
    expect(deriveEdgeType(true)).toEqual({ icon: "⊤", label: "boolean" });
  });

  it("returns array icon with count for arrays", () => {
    expect(deriveEdgeType([1, 2, 3])).toEqual({ icon: "[]", label: "array · 3" });
  });

  it("returns object icon for plain objects", () => {
    expect(deriveEdgeType({ a: 1 })).toEqual({ icon: "{}", label: "object" });
  });

  it("returns null icon for null", () => {
    expect(deriveEdgeType(null)).toEqual({ icon: "∅", label: "null" });
  });

  it("returns empty string when value is undefined (no run yet)", () => {
    expect(deriveEdgeType(undefined)).toEqual({ icon: "", label: "" });
  });
});
```

- [ ] **Step 2: Run to verify tests fail**

```
cd apps/web && npm run test -- NoodleEdge.test.ts
```

Expected: `deriveEdgeType is not a function` (not exported yet).

- [ ] **Step 3: Implement `deriveEdgeType` and update `NoodleEdge.tsx`**

Add the exported helper at the top of `NoodleEdge.tsx` (after the imports):

```tsx
export function deriveEdgeType(value: unknown): { icon: string; label: string } {
  if (value === undefined) return { icon: "", label: "" };
  if (value === null) return { icon: "∅", label: "null" };
  if (Array.isArray(value)) return { icon: "[]", label: `array · ${value.length}` };
  if (typeof value === "object") return { icon: "{}", label: "object" };
  if (typeof value === "string") return { icon: "T", label: "string" };
  if (typeof value === "number") return { icon: "#", label: "number" };
  if (typeof value === "boolean") return { icon: "⊤", label: "boolean" };
  return { icon: "?", label: typeof value };
}
```

Then in the `NoodleEdge` component, add a `runOutputs` selector after the existing `onEdgesChange` line:

```tsx
  const onEdgesChange = useEditor((s) => s.onEdgesChange);
  const edgeType = useEditor((s) => {
    if (!source) return { icon: "", label: "" };
    const outputs = s.runOutputs[source];
    if (!outputs || typeof outputs !== "object") return { icon: "", label: "" };
    const portValue = (outputs as Record<string, unknown>)[sourceHandle ?? "main"];
    return deriveEdgeType(portValue);
  });
  const agentFlowClass = useEditor((s) => /* ... existing ... */);
```

And inside the `EdgeLabelRenderer` div, add the type badge before the actions div:

```tsx
        <div
          className={`noodle-edge-mid nodrag nopan${selected ? " is-selected" : ""}`}
          style={{
            position: "absolute",
            transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: "all",
          }}
        >
          {label && <span className="noodle-edge-label">{String(label)}</span>}
          {edgeType.icon && (
            <div className="noodle-edge-type-badge" title={edgeType.label}>
              <span className="noodle-edge-type-icon">{edgeType.icon}</span>
              <span className="noodle-edge-type-label">{edgeType.label}</span>
            </div>
          )}
          <div className="noodle-edge-actions">
            {/* ... existing add + delete buttons unchanged ... */}
          </div>
        </div>
```

Note: `sourceHandle` is already a prop on `NoodleEdge` (it comes from `EdgeProps`). Confirm this is available; if not, it can be read from `useEditor((s) => s.edges.find(e => e.id === id)?.sourceHandle)`.

- [ ] **Step 4: Add CSS for the edge type badge**

In `apps/web/src/editor.css`, append after the `.noodle-edge-btn-delete:hover` block (around line 2820):

```css
/* Edge data-type badge — visible on hover alongside the action buttons */
.noodle-edge-type-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 4px;
  opacity: 0;
  transition: opacity 140ms ease;
  pointer-events: none;
  white-space: nowrap;
}
.noodle-edge-mid:hover .noodle-edge-type-badge {
  opacity: 1;
}
.noodle-edge-type-icon {
  font-size: 9px;
  font-weight: 700;
  font-family: var(--font-mono);
  color: var(--ink-3);
  background: var(--surface-2);
  border-radius: 3px;
  padding: 0 3px;
  line-height: 14px;
}
.noodle-edge-type-label {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd apps/web && npm run test -- NoodleEdge.test.ts
```

Expected: ALL PASS.

- [ ] **Step 6: Typecheck**

```
cd apps/web && npm run typecheck
```

Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/editor/NoodleEdge.tsx apps/web/src/editor/NoodleEdge.test.ts apps/web/src/editor.css
git commit -m "feat(ui): show data-type badge on canvas edge hover"
```

---

### Task 7: NDV Output Coerce Preview (Feature A)

**Files:**
- Modify: `apps/web/src/editor/DataPanel.tsx`
- Modify: `apps/web/src/editor/DataPanel.test.tsx`
- Modify: `apps/web/src/editor.css`

**Interfaces:**
- Produces: `coercePreview(value: unknown, to: CoerceTarget): unknown` — pure client-side conversion, no server call
- The selector is a `<select>` in the DataPanel Output header that only appears when `dragPrefix` is falsy (i.e. this is the Output panel, not the Input panel — the Input panel passes `dragPrefix`). When a coerce target is chosen, the panel body re-renders the data through `coercePreview()`.
- `CoerceTarget = "none" | "string" | "number" | "boolean" | "json"` — no "list" (already is list) or "object" (already is object), to keep the selector short.

The coerce selector does NOT modify the workflow or the stored run output. It is a read-only view transformation, reset to `"none"` whenever `data` changes (i.e. the node reruns).

- [ ] **Step 1: Write failing tests**

Add to `apps/web/src/editor/DataPanel.test.tsx`:

```tsx
describe("DataPanel coerce preview (output panel)", () => {
  it("shows coerce selector when title starts with Output and data is present", () => {
    render(<DataPanel title="Output" data={{ score: "42" }} />);
    expect(screen.getByRole("combobox", { name: /coerce/i })).toBeTruthy();
  });

  it("does not show coerce selector when dragPrefix is set (input panel)", () => {
    render(
      <DataPanel title="Output" data={{ score: "42" }} dragPrefix="$json" />,
    );
    expect(screen.queryByRole("combobox", { name: /coerce/i })).toBeNull();
  });

  it("does not show coerce selector when data is empty", () => {
    render(<DataPanel title="Output" data={undefined} />);
    expect(screen.queryByRole("combobox", { name: /coerce/i })).toBeNull();
  });

  it("shows numeric coerced value when 'number' coerce is selected", async () => {
    render(<DataPanel title="Output" data="99.5" />);
    const sel = screen.getByRole("combobox", { name: /coerce/i });
    fireEvent.change(sel, { target: { value: "number" } });
    // The coerced value (99.5) should now appear in the view
    expect(screen.getByText("99.5")).toBeTruthy();
  });
});
```

Note: add `import { fireEvent } from "@testing-library/react";` if not already imported.

- [ ] **Step 2: Run to verify tests fail**

```
cd apps/web && npm run test -- DataPanel.test.tsx
```

Expected: FAIL — coerce selector not found.

- [ ] **Step 3: Add `coercePreview` and the selector to `DataPanel.tsx`**

Add the helper function after the existing `pretty()` function in `DataPanel.tsx`:

```tsx
type CoerceTarget = "none" | "string" | "number" | "boolean" | "json";

function coercePreview(value: unknown, to: CoerceTarget): unknown {
  if (to === "none") return value;
  // Flatten single-output wrapper first (mirrors unwrapSingleOutput)
  const v = value !== null && typeof value === "object" && !Array.isArray(value)
    ? (() => {
        const keys = Object.keys(value as object);
        return keys.length === 1 ? (value as Record<string, unknown>)[keys[0]] : value;
      })()
    : value;
  const s = v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  if (to === "string") return s;
  if (to === "number") { const n = Number(s); return isNaN(n) ? `(cannot convert "${s}" to number)` : n; }
  if (to === "boolean") {
    if (s === "" || s === "0" || s.toLowerCase() === "false" || s.toLowerCase() === "no") return false;
    return true;
  }
  if (to === "json") {
    try { return JSON.parse(s); }
    catch { return `(invalid JSON: ${s.slice(0, 60)})`; }
  }
  return v;
}
```

In the `DataPanel` component function, add a coerce state that resets when data changes:

```tsx
  const [coerce, setCoerce] = useState<CoerceTarget>("none");
  useEffect(() => { setCoerce("none"); }, [data]);
```

Derive the data shown in the body by applying `coercePreview`:

```tsx
  // In the existing body: replace the two `display` / `effectiveView` derivations with:
  const rawDisplay = unwrapSingleOutput(data);
  const display = coerce !== "none" ? coercePreview(data, coerce) : rawDisplay;
  // (keep the rest of the existing effectiveView logic unchanged, using `display`)
```

Show the coerce selector in the panel header. The output panel is identified by `!dragPrefix && !empty`. Add after the `<h3>` block inside the `<header>`:

```tsx
          {!dragPrefix && !empty && (
            <select
              className="data-coerce-select"
              aria-label="Coerce output type"
              value={coerce}
              onChange={(e) => setCoerce(e.target.value as CoerceTarget)}
              title="Preview data coerced to a different type"
            >
              <option value="none">as-is</option>
              <option value="string">→ string</option>
              <option value="number">→ number</option>
              <option value="boolean">→ boolean</option>
              <option value="json">→ JSON parse</option>
            </select>
          )}
```

- [ ] **Step 4: Add CSS for the coerce selector**

In `apps/web/src/editor.css`, append after the conditions builder CSS added in Task 4:

```css
/* NDV output coerce-preview selector */
.data-coerce-select {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--ink-3);
  background: var(--surface-2);
  border: 1px solid var(--line-2);
  border-radius: 4px;
  padding: 2px 4px;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
}
.data-coerce-select:focus {
  outline: none;
  border-color: var(--accent);
  color: var(--ink);
}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd apps/web && npm run test -- DataPanel.test.tsx
```

Expected: ALL PASS.

- [ ] **Step 6: Typecheck**

```
cd apps/web && npm run typecheck
```

Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/editor/DataPanel.tsx apps/web/src/editor/DataPanel.test.tsx apps/web/src/editor.css
git commit -m "feat(ui): add coerce-preview selector to NDV output panel"
```

---

### Task 8: Set Field Type Node (Feature B)

**Files:**
- Modify: `packages/nodes/noodle_nodes/builtin.py`
- Create: `packages/nodes/tests/test_set_field_type.py`

**Interfaces:**
- Produces: `set_field_type_node(input, field, to)` — registered as node id `"set_field_type"`, category `"Data Types"`
- Consumes: `_convert_value(value, target)` — already defined at line 1811 in `builtin.py`
- `field` supports a single level of dot-notation: `"price"` converts the top-level key; `"meta.count"` converts `input["meta"]["count"]`.

The node passes the entire input object through unchanged except for the one named field, which is coerced to `to`. It raises `ValueError` if the conversion fails (same behaviour as the existing To Integer / To Float nodes).

- [ ] **Step 1: Write failing tests**

Create `packages/nodes/tests/test_set_field_type.py`:

```python
"""Tests for the Set Field Type node."""
import pytest
import noodle_nodes  # noqa: F401
from noodle.engine import execute
from noodle.models import Edge, GraphNode, NodeStatus, WorkflowGraph
from noodle.sdk import registry


def test_set_field_type_registered():
    ids = {m.id for m in registry.manifests()}
    assert "set_field_type" in ids


@pytest.mark.asyncio
async def test_set_field_type_converts_string_to_int():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": {"price": "42", "name": "widget"}},
            ),
            GraphNode(
                id="c",
                type="set_field_type",
                params={"field": "price", "to": "int"},
            ),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    out = result.nodes["c"].outputs["main"]
    assert out["price"] == 42
    assert isinstance(out["price"], int)
    assert out["name"] == "widget"  # untouched


@pytest.mark.asyncio
async def test_set_field_type_converts_string_to_float():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"score": "9.5"}}),
            GraphNode(id="c", type="set_field_type", params={"field": "score", "to": "float"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"]["score"] == 9.5


@pytest.mark.asyncio
async def test_set_field_type_converts_to_boolean():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"active": "true"}}),
            GraphNode(id="c", type="set_field_type", params={"field": "active", "to": "boolean"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"]["active"] is True


@pytest.mark.asyncio
async def test_set_field_type_converts_to_string():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"count": 7}}),
            GraphNode(id="c", type="set_field_type", params={"field": "count", "to": "string"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"]["count"] == "7"


@pytest.mark.asyncio
async def test_set_field_type_nested_dot_path():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": {"meta": {"count": "5"}, "name": "x"}},
            ),
            GraphNode(
                id="c",
                type="set_field_type",
                params={"field": "meta.count", "to": "int"},
            ),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    out = result.nodes["c"].outputs["main"]
    assert out["meta"]["count"] == 5
    assert out["name"] == "x"  # untouched


@pytest.mark.asyncio
async def test_set_field_type_nonexistent_field_passes_through():
    """A field path that doesn't exist is silently ignored — not an error."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"a": 1}}),
            GraphNode(id="c", type="set_field_type", params={"field": "missing", "to": "int"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"] == {"a": 1}


@pytest.mark.asyncio
async def test_set_field_type_non_dict_input_passes_through():
    """Non-dict inputs (e.g. a list) are returned unchanged."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": [1, 2, 3]}),
            GraphNode(id="c", type="set_field_type", params={"field": "x", "to": "string"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_set_field_type_empty_field_passes_through():
    """Blank field name means no conversion — pass through unchanged."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"x": "5"}}),
            GraphNode(id="c", type="set_field_type", params={"field": "", "to": "int"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"] == {"x": "5"}
```

- [ ] **Step 2: Run to verify tests fail**

```
uv run pytest packages/nodes/tests/test_set_field_type.py -v 2>&1 | head -20
```

Expected: `AssertionError: assert "set_field_type" in ids` — node not registered yet.

- [ ] **Step 3: Implement `set_field_type_node` in `builtin.py`**

Add the following immediately after the existing `convert_fields_node` function (around line 1990):

```python
@node(
    name="Set Field Type",
    id="set_field_type",
    category="Data Types",
    icon="braces",
    params={
        "field": {
            "placeholder": "price",
            "description": (
                "Dot-path of the field to convert (e.g. 'price' or 'meta.count'). "
                "One level of nesting is supported. Leave blank to pass through unchanged."
            ),
        },
        "to": {
            "choices": CONVERSION_TARGETS,
            "description": "Target type for the field.",
        },
    },
)
def set_field_type_node(input: Any = None, field: str = "", to: str = "string") -> Any:
    """Convert a single named field of the input object to the specified type.

    The rest of the input passes through unchanged.  Supports one level of
    dot-notation for nested fields (e.g. ``meta.count``).  If the field is
    absent or the input is not a dict, the input is returned as-is.
    """
    if not field or not isinstance(input, dict):
        return input
    result = dict(input)
    parts = field.split(".", 1)
    if len(parts) == 1:
        if field in result:
            result[field] = _convert_value(result[field], to)
    else:
        parent, child = parts
        if parent in result and isinstance(result[parent], dict):
            result[parent] = {
                **result[parent],
                child: _convert_value(result[parent].get(child), to),
            }
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest packages/nodes/tests/test_set_field_type.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Run the full node registration test to verify new node appears**

```
uv run pytest packages/nodes/tests/test_builtin_nodes.py::test_expected_builtins_are_registered -v
```

Expected: PASS (the registration test only checks for a listed set of IDs, so adding a new one doesn't break it).

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/builtin.py packages/nodes/tests/test_set_field_type.py
git commit -m "feat(nodes): add Set Field Type node for single-field in-workflow type conversion"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Schema view: type icon per field — Task 1, `schemaTypeIcon()`
- [x] Schema view: actual value inline — Task 1, `schemaValuePreview()`
- [x] Typed conditions: String operators (equals, contains, regex…) — Task 2
- [x] Typed conditions: Number operators (GT/LT/GTE/LTE/eq) — Task 2
- [x] Typed conditions: Boolean (is true / is false) — Task 2
- [x] Typed conditions: Array (length ops, contains) — Task 2
- [x] Typed conditions: Object (has key / is empty) — Task 2
- [x] Typed conditions: Date (before / after / equals by day) — Task 2
- [x] Typed conditions: Any (exists / is empty) — Task 2
- [x] Multi-condition AND logic — Task 2, `_eval_conditions`
- [x] Multi-condition OR logic — Task 2, `_eval_conditions`
- [x] If node uses conditions param — Task 3
- [x] Filter node uses conditions param — Task 3
- [x] Backward compat: legacy `field/operator/value` still work — Task 3 + test
- [x] ConditionsField UI renders conditions — Task 4
- [x] ConditionsField logic/AND/OR toggle — Task 4
- [x] ConditionsField type-specific operator reset — Task 4
- [x] ConditionsField no-value operators hide input — Task 4
- [x] ConditionsField legacy migration banner — Task 4
- [x] ConditionsField wired into inspector — Task 5
- [x] Edge type badge: derives type from `runOutputs[source][handle]` — Task 6, `deriveEdgeType()`
- [x] Edge type badge: visible on hover, hidden at rest — Task 6, CSS opacity transition
- [x] Edge type badge: shows array count (e.g. `array · 3`) — Task 6
- [x] Edge type badge: empty when node hasn't run yet — Task 6, `undefined` case
- [x] NDV coerce preview: selector appears only on Output panel with data — Task 7
- [x] NDV coerce preview: selector hidden when `dragPrefix` is set (input panel) — Task 7
- [x] NDV coerce preview: resets to "as-is" when data changes (node reruns) — Task 7
- [x] NDV coerce preview: client-side only, no workflow modification — Task 7
- [x] Set Field Type node registered — Task 8
- [x] Set Field Type: converts top-level field — Task 8
- [x] Set Field Type: converts nested `"parent.child"` dot-path — Task 8
- [x] Set Field Type: non-dict / missing field passes through unchanged — Task 8
- [x] Set Field Type: all `CONVERSION_TARGETS` types supported via `_convert_value` — Task 8

**Type consistency:**
- `ConditionType`, `Condition`, `ConditionsValue` defined once in `ConditionsField.tsx` and referenced by tests — consistent.
- `_matches_typed` / `_eval_conditions` defined in `builtin.py`, imported directly in test file — consistent.
- `conditions_builder` string is the same in `builtin.py` `widget=` and in `NodeDetails.tsx` `spec.widget ===` check — consistent.
- `deriveEdgeType` exported from `NoodleEdge.tsx` and imported directly by `NoodleEdge.test.ts` — consistent.
- `CoerceTarget` union type defined once in `DataPanel.tsx` — consistent.
- `set_field_type` id string matches `test_expected_builtins_are_registered` test expectations (it only checks a fixed set, so new node doesn't break it) — consistent.
