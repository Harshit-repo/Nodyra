# Replace Native Dialogs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three `window.prompt` / `window.alert` / `window.confirm` calls with themed, accessible in-app dialogs.

**Architecture:** A new `PromptDialog` component (mirrors existing `ConfirmDialog`) is added; `ConfirmProvider` gains a second `PromptContext` and `usePrompt()` hook using the same resolver-ref pattern; `OrgSwitcher` in `HomeHeader.tsx` switches to `usePrompt` + `useToast`; `EditorPage.toggleActive` switches to `useConfirm`.

**Tech Stack:** React 18, React Testing Library, Vitest, existing `useModalA11y` focus-trap hook, existing `.modal` / `.btn` CSS classes.

## Global Constraints

- All new components use named exports (not default).
- `PromptDialog` accepts `onConfirm(value: string)` — empty strings are valid; callers validate.
- `usePrompt` fallback (no provider): `Promise.resolve(window.prompt(options.label))` — keeps isolated tests working.
- Do NOT remove the existing `import { ConfirmDialog }` from `EditorPage.tsx` — it is used elsewhere in that file.
- `PromptOptions.confirmLabel` defaults to `"Confirm"` inside `PromptDialog`.
- `ConfirmOptions` and `ConfirmFn` are exported from `ConfirmProvider.tsx` — do NOT change their shapes.
- Tasks 1 and 2 are independently committable. Task 3 must update both `HomeHeader.tsx` AND `EditorPage.tsx` in one commit (both are small; split would leave a partial migration).

---

### Task 1: PromptDialog component + CSS

**Files:**
- Create: `apps/web/src/PromptDialog.tsx`
- Create: `apps/web/src/PromptDialog.test.tsx`
- Modify: `apps/web/src/index.css` (add `.prompt-field` block after `.confirm-modal` rule at line 1135)

**Interfaces:**
- Produces:
  ```ts
  export function PromptDialog(props: {
    title: string;
    body?: string;
    label: string;
    placeholder?: string;
    confirmLabel?: string;
    onCancel: () => void;
    onConfirm: (value: string) => void;
  }): ReactNode
  ```

- [ ] **Step 1: Write the failing tests**

Create `apps/web/src/PromptDialog.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PromptDialog } from "./PromptDialog";

const base = {
  title: "New organization",
  label: "Name",
  placeholder: "Acme Inc.",
  onCancel: vi.fn(),
  onConfirm: vi.fn(),
};

describe("PromptDialog", () => {
  it("renders title, label, input, Cancel and Confirm buttons", () => {
    render(<PromptDialog {...base} />);
    expect(screen.getByText("New organization")).toBeTruthy();
    expect(screen.getByLabelText("Name")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
  });

  it("has role=dialog and aria-modal=true", () => {
    render(<PromptDialog {...base} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
  });

  it("calls onConfirm with typed value when Confirm is clicked", () => {
    const onConfirm = vi.fn();
    render(<PromptDialog {...base} onConfirm={onConfirm} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledWith("Acme");
  });

  it("calls onConfirm with typed value when Enter is pressed in input", () => {
    const onConfirm = vi.fn();
    render(<PromptDialog {...base} onConfirm={onConfirm} />);
    const input = screen.getByLabelText("Name");
    fireEvent.change(input, { target: { value: "Acme" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith("Acme");
  });

  it("calls onCancel when Cancel is clicked", () => {
    const onCancel = vi.fn();
    render(<PromptDialog {...base} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("renders optional body text when provided", () => {
    render(<PromptDialog {...base} body="Enter a unique name." />);
    expect(screen.getByText("Enter a unique name.")).toBeTruthy();
  });

  it("uses custom confirmLabel when provided", () => {
    render(<PromptDialog {...base} confirmLabel="Create" />);
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/web && npm test -- src/PromptDialog.test.tsx
```

Expected: all 7 tests FAIL with `Cannot find module './PromptDialog'`.

- [ ] **Step 3: Implement PromptDialog.tsx**

Create `apps/web/src/PromptDialog.tsx`:

```tsx
import { useRef, useState } from "react";

import { useModalA11y } from "./useModalA11y";

interface PromptDialogProps {
  title: string;
  body?: string;
  label: string;
  placeholder?: string;
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}

export function PromptDialog({
  title,
  body,
  label,
  placeholder,
  confirmLabel = "Confirm",
  onCancel,
  onConfirm,
}: PromptDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [value, setValue] = useState("");
  useModalA11y(dialogRef, onCancel);

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div
        ref={dialogRef}
        className="modal confirm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="prompt-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="prompt-title">{title}</h2>
        {body && <p className="muted">{body}</p>}
        <div className="prompt-field">
          <label htmlFor="prompt-input">{label}</label>
          <input
            id="prompt-input"
            type="text"
            value={value}
            placeholder={placeholder}
            autoFocus
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onConfirm(value);
              }
            }}
          />
        </div>
        <div className="modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onConfirm(value)}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Add `.prompt-field` CSS**

In `apps/web/src/index.css`, find the `.confirm-modal` block (line 1135). Add the following immediately after its closing brace:

```css
.prompt-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  margin-bottom: 4px;
}

.prompt-field label {
  font-size: 13px;
  color: var(--ink-2, #aab1bd);
}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd apps/web && npm test -- src/PromptDialog.test.tsx
```

Expected: 7 tests PASS.

- [ ] **Step 6: Run full suite to check for regressions**

```
cd apps/web && npm test
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/PromptDialog.tsx apps/web/src/PromptDialog.test.tsx apps/web/src/index.css
git commit -m "feat(ui): add PromptDialog component with accessible text-input modal"
```

---

### Task 2: Extend ConfirmProvider with usePrompt

**Files:**
- Modify: `apps/web/src/ConfirmProvider.tsx`
- Create: `apps/web/src/ConfirmProvider.test.tsx`

**Interfaces:**
- Consumes: `PromptDialog` from Task 1.
- Produces:
  ```ts
  export interface PromptOptions {
    title: string;
    body?: string;
    label: string;
    placeholder?: string;
    confirmLabel?: string;
  }
  export function usePrompt(): (options: PromptOptions) => Promise<string | null>
  ```
  `usePrompt` is exported from `"./ConfirmProvider"` — call sites import it from there.

- [ ] **Step 1: Write the failing tests**

Create `apps/web/src/ConfirmProvider.test.tsx`:

```tsx
import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConfirmProvider, useConfirm, usePrompt } from "./ConfirmProvider";

function ConfirmHarness() {
  const confirm = useConfirm();
  const [result, setResult] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={async () => {
          const ok = await confirm({ title: "Delete?", body: "Cannot undo." });
          setResult(ok ? "confirmed" : "cancelled");
        }}
      >
        trigger-confirm
      </button>
      {result !== null && <output>{result}</output>}
    </>
  );
}

function PromptHarness() {
  const prompt = usePrompt();
  const [result, setResult] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={async () => {
          const value = await prompt({ title: "New org", label: "Name" });
          setResult(value ?? "__null__");
        }}
      >
        trigger-prompt
      </button>
      {result !== null && <output>{result}</output>}
    </>
  );
}

describe("ConfirmProvider", () => {
  it("useConfirm resolves true when the confirm button is clicked", async () => {
    render(
      <ConfirmProvider>
        <ConfirmHarness />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger-confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText("confirmed")).toBeTruthy();
  });

  it("useConfirm resolves false when Cancel is clicked", async () => {
    render(
      <ConfirmProvider>
        <ConfirmHarness />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger-confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("cancelled")).toBeTruthy();
  });

  it("usePrompt resolves with typed string when Confirm is clicked", async () => {
    render(
      <ConfirmProvider>
        <PromptHarness />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger-prompt" }));
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Acme" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Acme")).toBeTruthy();
  });

  it("usePrompt resolves with null when Cancel is clicked", async () => {
    render(
      <ConfirmProvider>
        <PromptHarness />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger-prompt" }));
    await screen.findByLabelText("Name");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("__null__")).toBeTruthy();
  });

  it("useConfirm and usePrompt work independently in the same provider", async () => {
    render(
      <ConfirmProvider>
        <ConfirmHarness />
        <PromptHarness />
      </ConfirmProvider>,
    );
    // Trigger confirm dialog
    fireEvent.click(screen.getByRole("button", { name: "trigger-confirm" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText("confirmed")).toBeTruthy();
    // Now trigger prompt dialog
    fireEvent.click(screen.getByRole("button", { name: "trigger-prompt" }));
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Noodle" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Noodle")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/web && npm test -- src/ConfirmProvider.test.tsx
```

Expected: tests for `usePrompt` FAIL with `usePrompt is not a function` or similar. The `useConfirm` tests may already pass — that is fine.

- [ ] **Step 3: Replace ConfirmProvider.tsx with the extended version**

Replace the entire contents of `apps/web/src/ConfirmProvider.tsx` with:

```tsx
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";

import { ConfirmDialog } from "./ConfirmDialog";
import { PromptDialog } from "./PromptDialog";

export interface ConfirmOptions {
  title: string;
  body: string;
  confirmLabel?: string;
}

export interface PromptOptions {
  title: string;
  body?: string;
  label: string;
  placeholder?: string;
  confirmLabel?: string;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;
type PromptFn = (options: PromptOptions) => Promise<string | null>;

const ConfirmContext = createContext<ConfirmFn | null>(null);
const PromptContext = createContext<PromptFn | null>(null);

/**
 * App-wide confirmation and text-prompt dialogs. Renders themed, focus-trapped
 * dialogs and exposes promise-based hooks so call sites read almost exactly
 * like the native browser APIs they replace:
 *
 *   if (!(await confirm({ title, body }))) return;
 *   const name = await prompt({ title, label });
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<ConfirmOptions | null>(null);
  const resolverRef = useRef<((value: boolean) => void) | null>(null);

  const [pendingPrompt, setPendingPrompt] = useState<PromptOptions | null>(null);
  const promptResolverRef = useRef<((value: string | null) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
      setPending(options);
    });
  }, []);

  const settle = useCallback((value: boolean) => {
    resolverRef.current?.(value);
    resolverRef.current = null;
    setPending(null);
  }, []);

  const prompt = useCallback<PromptFn>((options) => {
    return new Promise<string | null>((resolve) => {
      promptResolverRef.current = resolve;
      setPendingPrompt(options);
    });
  }, []);

  const settlePrompt = useCallback((value: string | null) => {
    promptResolverRef.current?.(value);
    promptResolverRef.current = null;
    setPendingPrompt(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      <PromptContext.Provider value={prompt}>
        {children}
        {pending && (
          <ConfirmDialog
            title={pending.title}
            body={pending.body}
            confirmLabel={pending.confirmLabel}
            onCancel={() => settle(false)}
            onConfirm={() => settle(true)}
          />
        )}
        {pendingPrompt && (
          <PromptDialog
            title={pendingPrompt.title}
            body={pendingPrompt.body}
            label={pendingPrompt.label}
            placeholder={pendingPrompt.placeholder}
            confirmLabel={pendingPrompt.confirmLabel}
            onCancel={() => settlePrompt(null)}
            onConfirm={(value) => settlePrompt(value)}
          />
        )}
      </PromptContext.Provider>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    return (options) =>
      Promise.resolve(window.confirm(`${options.title}\n\n${options.body}`));
  }
  return ctx;
}

export function usePrompt(): PromptFn {
  const ctx = useContext(PromptContext);
  if (!ctx) {
    return (options) => Promise.resolve(window.prompt(options.label));
  }
  return ctx;
}
```

- [ ] **Step 4: Run targeted tests**

```
cd apps/web && npm test -- src/ConfirmProvider.test.tsx
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run full suite**

```
cd apps/web && npm test
```

Expected: all tests pass. Confirm the existing `ConfirmDialog` usages in `WorkflowsPage` etc. still compile (`useConfirm` shape unchanged).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/ConfirmProvider.tsx apps/web/src/ConfirmProvider.test.tsx
git commit -m "feat(ui): extend ConfirmProvider with usePrompt / PromptContext"
```

---

### Task 3: Replace window.* at call sites

**Files:**
- Modify: `apps/web/src/HomeHeader.tsx`
- Modify: `apps/web/src/EditorPage.tsx`

**IMPORTANT:** Make all changes to both files before running tests. Commit both in one commit.

**Interfaces:**
- Consumes:
  - `usePrompt(): PromptFn` from `"./ConfirmProvider"` (Task 2)
  - `useToast(): { notify(message: string, tone?: ToastTone): void }` from `"./ToastProvider"` (already used elsewhere in the codebase)
  - `useConfirm(): ConfirmFn` from `"./ConfirmProvider"` (Task 2)

- [ ] **Step 1: Update HomeHeader.tsx — OrgSwitcher**

In `apps/web/src/HomeHeader.tsx`:

**a) Add two imports** at the top of the file (after the existing imports):

```tsx
import { usePrompt } from "./ConfirmProvider";
import { useToast } from "./ToastProvider";
```

**b) Inside the `OrgSwitcher` function component**, add two hook calls at the top of the function body (after the existing `useState` and `useRef` calls):

```tsx
const prompt = usePrompt();
const { notify } = useToast();
```

**c) Replace the `createOrg` function** (currently lines 56–66) with:

```tsx
async function createOrg(): Promise<void> {
  const name = await prompt({
    title: "New organization",
    label: "Name",
    placeholder: "Acme Inc.",
    confirmLabel: "Create",
  });
  if (!name?.trim()) return;
  try {
    const created = await api.createOrg({ name: name.trim() });
    setOrgId(created.id);
    window.location.assign("/");
  } catch (err) {
    notify(errorMessage(err), "error");
  }
}
```

The only changes from the original `createOrg` are:
- `window.prompt("Organization name")` → `await prompt({ title: ..., label: ..., placeholder: ..., confirmLabel: ... })`
- `window.alert(errorMessage(err))` → `notify(errorMessage(err), "error")`

- [ ] **Step 2: Update EditorPage.tsx — toggleActive**

In `apps/web/src/EditorPage.tsx`:

**a) Add `useConfirm` to the existing ConfirmProvider import.** Currently line 8 imports `ConfirmDialog` directly. Add a new import line alongside the other hook imports (near the `useToast` import at line 37):

```tsx
import { useConfirm } from "./ConfirmProvider";
```

**b) Add `useConfirm` hook call** inside the `EditorPage` component function, near the existing `const { notify } = useToast();` call at line 321:

```tsx
const confirm = useConfirm();
```

**c) Replace line 806** (inside `toggleActive`):

```tsx
// Before:
if (!next && !window.confirm("Pause this workflow? It stops running in production until you switch it back on. Version history is kept.")) return;

// After:
if (
  !next &&
  !(await confirm({
    title: "Pause this workflow?",
    body: "It stops running in production until you switch it back on. Version history is kept.",
    confirmLabel: "Pause",
  }))
) return;
```

`toggleActive` is already declared `async` (line 801: `async function toggleActive(next: boolean): Promise<void>`), so no function-signature change is needed.

- [ ] **Step 3: Type-check**

```
cd apps/web && npx tsc --noEmit
```

Expected: zero new errors.

- [ ] **Step 4: Run full test suite**

```
cd apps/web && npm test
```

Expected: all tests pass.

- [ ] **Step 5: Commit both files atomically**

```bash
git add apps/web/src/HomeHeader.tsx apps/web/src/EditorPage.tsx
git commit -m "fix(ui): replace window.prompt/alert/confirm with themed app dialogs"
```
