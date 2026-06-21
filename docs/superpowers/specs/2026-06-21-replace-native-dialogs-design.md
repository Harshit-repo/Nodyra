# Replace Native Dialogs — Design Spec

**Date:** 2026-06-21
**Status:** Approved
**Batch:** Frontend Optimizations #1 of 6

## Problem

Three `window.*` calls remain in the frontend:
- `HomeHeader.tsx:57` — `window.prompt("Organization name")` — blocks the main thread, ignores app theme, inaccessible
- `HomeHeader.tsx:64` — `window.alert(errorMessage(err))` — same issues
- `EditorPage.tsx:806` — `window.confirm("Pause this workflow?...")` — same issues

`ConfirmProvider` and `useToast` already exist to replace these; only `window.prompt` lacks a proper counterpart.

## Solution

Add `usePrompt()` to `ConfirmProvider`, create a `PromptDialog` component, and update the three call sites.

---

## Architecture

### New: `PromptDialog.tsx`

Mirrors `ConfirmDialog` exactly — same modal structure, `useModalA11y` for Escape/focus-trap, same CSS classes — with one addition: a `<label>` + `<input>` between the body text and the action buttons.

**Props:**
```ts
interface PromptDialogProps {
  title: string;
  body?: string;
  label: string;
  placeholder?: string;
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}
```

**Behaviour:**
- Input gets `autoFocus` so keyboard users land there immediately on open.
- `onKeyDown` on the input fires `onConfirm(value)` when `key === "Enter"`.
- Escape closes via `useModalA11y` → `onCancel` (existing hook, no change needed).
- Confirm button calls `onConfirm(value)` with the current input value.
- No internal validation — callers validate (e.g. `if (!name.trim()) return`).

**CSS:** reuses existing `.modal-overlay`, `.modal`, `.modal-actions`, `.btn` — no new classes needed. The input uses the existing `input` element styling.

### Modified: `ConfirmProvider.tsx`

Add a second context (`PromptContext`) alongside the existing `ConfirmContext`. Both live in the same `ConfirmProvider` component. Each has its own state and resolver ref.

**New exports:**
```ts
export interface PromptOptions {
  title: string;
  body?: string;
  label: string;
  placeholder?: string;
  confirmLabel?: string;
}

type PromptFn = (options: PromptOptions) => Promise<string | null>;

export function usePrompt(): PromptFn;
```

**`usePrompt` fallback (no provider in tree):**
```ts
return (options) => Promise.resolve(window.prompt(options.label));
```
Consistent with `useConfirm`'s fallback — isolated tests work without wrapping in a provider.

**Provider internals:**
```ts
const [pendingPrompt, setPendingPrompt] = useState<PromptOptions | null>(null);
const promptResolverRef = useRef<((value: string | null) => void) | null>(null);
```
`settlePrompt(value: string | null)` calls the resolver, nulls the ref, clears state — same pattern as existing `settle(value: boolean)`.

`ConfirmProvider` renders both `ConfirmDialog` (when `pending`) and `PromptDialog` (when `pendingPrompt`) — in practice only one is open at a time.

### Modified: `HomeHeader.tsx` (`OrgSwitcher`)

```tsx
// Before (lines 57, 64):
const name = window.prompt("Organization name");
window.alert(errorMessage(err));

// After:
const prompt = usePrompt();
const { notify } = useToast();
// inside createOrg:
const name = await prompt({
  title: "New organization",
  label: "Name",
  placeholder: "Acme Inc.",
  confirmLabel: "Create",
});
if (!name?.trim()) return;
// on error:
notify(errorMessage(err), "error");
```

`usePrompt()` and `useToast()` are called at the `OrgSwitcher` component level (both are already available — `ConfirmProvider` wraps the route tree and `ToastProvider` wraps `ConfirmProvider` in `App.tsx`).

### Modified: `EditorPage.tsx`

```tsx
// Before (line 806):
if (!next && !window.confirm("Pause this workflow? It stops running...")) return;

// After:
const confirm = useConfirm(); // added at top of EditorPage component
// inside handler:
if (!next && !(await confirm({
  title: "Pause this workflow?",
  body: "It stops running in production until you switch it back on. Version history is kept.",
  confirmLabel: "Pause",
}))) return;
```

The handler becomes `async`. No other changes to `EditorPage.tsx`.

---

## Edge Cases

| Case | Resolution |
|---|---|
| **Enter submits prompt** | `onKeyDown` on `<input>` calls `onConfirm(value)` when `key === "Enter"` |
| **Escape closes prompt** | `useModalA11y` already wires Escape → `onCancel` |
| **Empty input submitted** | Caller validates — `OrgSwitcher` already has `if (!name?.trim()) return` after the `await` |
| **`usePrompt` outside provider** | Fallback to `window.prompt` — existing test setup works without provider wrapper |
| **Both dialogs open simultaneously** | Cannot happen in practice (sequential user interaction); both are rendered independently if it ever did occur |
| **`EditorPage` handler async** | The handler is an event callback; no synchronous return value is consumed by the caller — safe to make async |
| **`OrgSwitcher` inside providers** | `App.tsx`: `ToastProvider > ConfirmProvider > Routes > HomeLayout > HomeHeader > OrgSwitcher` — both hooks resolve correctly ✓ |

---

## Tests

### `PromptDialog.test.tsx` (new)
- Renders title, label, input, and Cancel/Confirm buttons
- Typing in input updates value; Confirm calls `onConfirm` with that value
- Enter key calls `onConfirm` with current value
- Cancel calls `onCancel`
- `role="dialog"` and `aria-modal="true"` present

### `ConfirmProvider.test.tsx` (new — no existing test file)
- `usePrompt()` resolves with typed string on confirm
- `usePrompt()` resolves with `null` on cancel
- `useConfirm()` still resolves `true`/`false` unaffected (no regression)

---

## Files

| Action | File |
|---|---|
| Create | `apps/web/src/PromptDialog.tsx` |
| Create | `apps/web/src/PromptDialog.test.tsx` |
| Create | `apps/web/src/ConfirmProvider.test.tsx` |
| Modify | `apps/web/src/ConfirmProvider.tsx` |
| Modify | `apps/web/src/HomeHeader.tsx` |
| Modify | `apps/web/src/EditorPage.tsx` |
