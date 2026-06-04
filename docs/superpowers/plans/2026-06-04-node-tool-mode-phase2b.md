# Node Tool Mode — Phase 2b (Editor UX) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tool mode usable from the editor — a "Use as tool" toggle in the node inspector, a per-parameter Fixed/From-AI control that writes `$fromAI(...)` expressions, and an `ai_tool` output handle on a tool-mode node card so it can be wired into the AI Agent.

**Architecture:** Pure helpers for the `$fromAI` expression (build/detect/type-map) live in a small testable module. `NodeDetails` gains a tool-mode section (toggle + name/description via the existing `updateNodeSettings`) and a per-param Fixed/From-AI control. `NodeCard` renders a single `ai_tool` `tool` output when `data.toolMode`.

**Tech Stack:** React + TypeScript + Vite + vitest. Frontend only.

**Spec:** `docs/superpowers/specs/2026-06-04-node-tool-mode-design.md` (Phase 2). Builds on Phase 2a (`045c1b4`, `027ac25`).

**Scope:** toggle + per-param control + node output handle. The drag-from-Agent-tool-port picker is Phase 2c (deferred). View edits (NodeCard/NodeDetails) are verified by typecheck + full vitest + build; the extractable logic is unit-tested.

**Concurrency note:** other efforts edit this branch. Commit only the specific files per task — never `git add -A`.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `apps/web/src/editor/toolParam.ts` | `$fromAI` expr build/detect + param→arg type map | Create |
| `apps/web/src/editor/toolParam.test.ts` | Unit tests for the helpers | Create |
| `apps/web/src/editor/NodeCard.tsx` | `ai_tool` `tool` output when `data.toolMode` | Modify |
| `apps/web/src/editor/NodeDetails.tsx` | "Use as tool" section + per-param Fixed/From-AI control | Modify |
| `apps/web/src/editor.css` | Tool-mode UI styles | Modify |

Commands run from repo root; frontend via `npm --prefix apps/web …`.

---

## Task 1: `$fromAI` helper module (TDD)

**Files:**
- Create: `apps/web/src/editor/toolParam.ts`
- Test: `apps/web/src/editor/toolParam.test.ts`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/editor/toolParam.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { fromAiExpr, isFromAiExpr, paramArgType } from "./toolParam";

describe("toolParam helpers", () => {
  it("builds a positional $fromAI expression", () => {
    expect(fromAiExpr("text", "What to say", "string")).toBe(
      "{{ $fromAI('text', 'What to say', 'string') }}",
    );
  });

  it("escapes single quotes in name/description", () => {
    expect(fromAiExpr("it's", "a 'quote'", "string")).toBe(
      "{{ $fromAI('it\\'s', 'a \\'quote\\'', 'string') }}",
    );
  });

  it("detects a $fromAI expression value", () => {
    expect(isFromAiExpr("{{ $fromAI('x', '', 'string') }}")).toBe(true);
    expect(isFromAiExpr("{{ $json.x }}")).toBe(false);
    expect(isFromAiExpr("plain")).toBe(false);
    expect(isFromAiExpr(42)).toBe(false);
  });

  it("maps param spec types to JSON-schema arg types", () => {
    expect(paramArgType("number")).toBe("number");
    expect(paramArgType("integer")).toBe("number");
    expect(paramArgType("boolean")).toBe("boolean");
    expect(paramArgType("string")).toBe("string");
    expect(paramArgType("json")).toBe("string");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix apps/web test -- toolParam`
Expected: FAIL — cannot resolve `./toolParam`.

- [ ] **Step 3: Implement**

Create `apps/web/src/editor/toolParam.ts`:

```typescript
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix apps/web test -- toolParam`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/toolParam.ts apps/web/src/editor/toolParam.test.ts
git commit -m "feat(web): \$fromAI expression helpers for tool-mode params"
```

---

## Task 2: NodeCard `ai_tool` output handle in tool mode

**Files:**
- Modify: `apps/web/src/editor/NodeCard.tsx` (`outputNames` ~line 166; output `Handle` map ~line 779; port-tag map ~line 797)

- [ ] **Step 1: Override `outputNames` for tool mode**

At `apps/web/src/editor/NodeCard.tsx` line 166, change:

```typescript
  const outputNames = outputsOverride ?? manifest.outputs.map((o) => o.name);
```

to:

```typescript
  const outputNames = data.toolMode
    ? ["tool"]
    : outputsOverride ?? manifest.outputs.map((o) => o.name);
```

- [ ] **Step 2: Color the tool handle as `ai_tool`**

In the output `Handle` map (~line 779), replace the single `<Handle … />` return with one that special-cases the tool port:

```tsx
        {outputNames.map((name, i) => {
          const spec = manifest.outputs.find((o) => o.name === name);
          const isToolPort = Boolean(data.toolMode) && name === "tool";
          return (
            <Handle
              key={`out-${name}`}
              type="source"
              position={Position.Right}
              id={name}
              title={isToolPort ? "tool: AI tool" : `${name}: ${portKindLabel(spec?.data_kind)}`}
              className={
                isToolPort
                  ? "handle-ai-tools"
                  : portHandleClass(manifest.id, name, spec?.data_kind)
              }
              style={{
                top: portTop(i, outputNames.length),
                background: isToolPort
                  ? PORT_KIND_COLOR.ai_tool
                  : semanticPortColor(manifest.id, name, spec?.data_kind, color),
              }}
            />
          );
        })}
```

- [ ] **Step 3: Label the tool port**

In the port-tag map (~line 797), change the `shouldShow` line so the tool port shows a tag:

```tsx
          const isToolPort = Boolean(data.toolMode) && name === "tool";
          const shouldShow =
            outputNames.length > 1 || spec?.data_kind === "dataset" || isToolPort;
          if (!shouldShow) return null;
          return (
            <span
              key={`tag-${name}`}
              className={`port-tag${spec?.data_kind === "dataset" ? " port-tag-dataset" : ""}`}
              style={{ top: portTop(i, outputNames.length) }}
            >
              {isToolPort ? "tool" : spec?.data_kind === "dataset" ? `${name} · DatasetRef` : name}
            </span>
          );
```

(`PORT_KIND_COLOR` is already defined at the top of the file; `data` is the `NodeProps` arg.)

- [ ] **Step 4: Typecheck**

Run: `npm --prefix apps/web run typecheck`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/NodeCard.tsx
git commit -m "feat(web): tool-mode node card shows an ai_tool output handle"
```

---

## Task 3: NodeDetails — "Use as tool" toggle + per-param From-AI control

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (imports; `NodeDetails` component — add `updateNodeSettings`; tool section before the Parameters section ~line 2675; per-param control inside the param `.map` ~line 2709)

- [ ] **Step 1: Add imports + store hook**

At the top of `apps/web/src/editor/NodeDetails.tsx`, add to the editor imports:

```typescript
import { fromAiExpr, isFromAiExpr, paramArgType } from "./toolParam";
```

In the `NodeDetails` component (after `const updateParams = useEditor((s) => s.updateParams);` ~line 2556), add:

```typescript
  const updateNodeSettings = useEditor((s) => s.updateNodeSettings);
```

- [ ] **Step 2: Add the "Use as tool" section**

Immediately before the `<div className="inspector-section">` that holds Parameters (~line 2675, inside the `mode !== "python"` branch), insert:

```tsx
      {manifest.usable_as_tool && (
        <div className="inspector-section tool-mode-section">
          <label className="tool-mode-toggle">
            <input
              type="checkbox"
              checked={Boolean(node.data.toolMode)}
              onChange={(e) =>
                updateNodeSettings(node.id, { toolMode: e.target.checked })
              }
            />
            <span>Use as tool</span>
          </label>
          <p className="field-desc">
            Expose this node as a tool an AI Agent can call. Connect its{" "}
            <strong>tool</strong> output to the Agent's tool port.
          </p>
          {node.data.toolMode && (
            <>
              <div className="field">
                <div className="field-label">
                  <span className="field-name">Tool name</span>
                </div>
                <input
                  className="field-input"
                  value={node.data.toolName ?? ""}
                  placeholder={manifest.id}
                  onChange={(e) =>
                    updateNodeSettings(node.id, { toolName: e.target.value })
                  }
                />
              </div>
              <div className="field">
                <div className="field-label">
                  <span className="field-name">Tool description</span>
                </div>
                <textarea
                  className="field-input"
                  rows={2}
                  value={node.data.toolDescription ?? ""}
                  placeholder={manifest.description || manifest.name}
                  onChange={(e) =>
                    updateNodeSettings(node.id, { toolDescription: e.target.value })
                  }
                />
              </div>
            </>
          )}
        </div>
      )}
```

- [ ] **Step 3: Add the per-param Fixed/From-AI control**

Inside the param `.map`, in the returned `<div className="field" …>` (~line 2710), add the control right after the `<div className="field-label">…</div>` block and before the `{spec.description && …}` line. Only render it when the node is in tool mode and the param is not a credential:

```tsx
                {node.data.toolMode && renderSpec.type !== "credential" && (
                  <div className="from-ai-toggle" role="group" aria-label="Parameter source">
                    <button
                      type="button"
                      className={isFromAiExpr(value) ? "" : "active"}
                      onClick={() => setParam(spec.name, spec.default ?? "")}
                    >
                      Fixed
                    </button>
                    <button
                      type="button"
                      className={isFromAiExpr(value) ? "active" : ""}
                      onClick={() =>
                        setParam(
                          spec.name,
                          fromAiExpr(
                            spec.name,
                            spec.description || "",
                            paramArgType(spec.type),
                          ),
                        )
                      }
                    >
                      From AI
                    </button>
                  </div>
                )}
```

Then make the value editor hide when the param is AI-filled — wrap the existing `isWebhookAuthType ? (...) : (<ParamField …/>)` block so a From-AI param shows a chip instead:

```tsx
                {isFromAiExpr(value) ? (
                  <p className="from-ai-note">↯ The model supplies this argument.</p>
                ) : isWebhookAuthType ? (
                  <select
                    /* …existing select unchanged… */
                  >
                    {/* …existing options… */}
                  </select>
                ) : (
                  <ParamField
                    spec={renderSpec}
                    value={value}
                    onChange={(v) => setParam(spec.name, v)}
                    credentialContext={params}
                  />
                )}
```

(Keep the existing `<select>`/`<ParamField>` bodies exactly as they were — only the surrounding `isFromAiExpr(value) ? … :` branch is new.)

- [ ] **Step 4: Typecheck**

Run: `npm --prefix apps/web run typecheck`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat(web): Use-as-tool toggle + per-param From-AI control in the NDV"
```

---

## Task 4: Styles + full verification

**Files:**
- Modify: `apps/web/src/editor.css` (append)

- [ ] **Step 1: Append styles**

Append to `apps/web/src/editor.css`:

```css
.tool-mode-section .tool-mode-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  cursor: pointer;
}
.from-ai-toggle {
  display: inline-flex;
  margin: 4px 0 6px;
  border: 1px solid var(--line);
  border-radius: 7px;
  overflow: hidden;
}
.from-ai-toggle button {
  padding: 3px 10px;
  font-size: 12px;
  background: var(--surface-2);
  color: var(--ink-2);
  border: none;
  cursor: pointer;
}
.from-ai-toggle button.active {
  background: var(--accent);
  color: #fff;
}
.from-ai-note {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--accent-2);
  font-style: italic;
}
```

- [ ] **Step 2: Typecheck + full vitest (no regressions)**

Run: `npm --prefix apps/web run typecheck && npm --prefix apps/web test`
Expected: typecheck exit 0; all tests PASS (existing + the new `toolParam` tests).

- [ ] **Step 3: Build**

Run: `npm --prefix apps/web run build`
Expected: build succeeds (existing chunk-size warnings are fine).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/editor.css
git commit -m "style(web): tool-mode NDV controls"
```

- [ ] **Step 5: Manual smoke (after rebuilding the web container)**

Rebuild web (`docker compose -f deploy/docker-compose.yml --project-directory deploy up -d --build web`), hard-refresh, add an HTTP Request node, open it, toggle **Use as tool**, set a param to **From AI**, and confirm its `tool` output wires into an AI Agent's tool port.

---

## Notes for the implementer

- **`updateNodeSettings` already exists** and spreads the patch onto `node.data` (Phase 2a added `toolMode`/`toolName`/`toolDescription` to `NodeSettingsPatch`).
- **`usable_as_tool` is on the manifest** (Phase 2a added it to the TS type; the backend already sends it).
- View edits aren't unit-tested in this repo; the testable logic is isolated in `toolParam.ts` (Task 1). NodeCard/NodeDetails correctness is covered by typecheck + build + the manual smoke.
- Commit only the listed files per task; never `git add -A`.
