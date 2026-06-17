# Task Bar Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the editor's manual save/publish toolbar with autosave + an animated save indicator, a single status-driven Publish pill, and a ⋯ overflow menu, removing Run from the bar and leaving the env dropdown untouched.

**Architecture:** Extract small, independently-testable pieces out of the large `EditorPage.tsx`: a pure status-derivation helper, a debounced autosave hook, and three presentational components (SaveIndicator, PublishPill, OverflowMenu) plus a consolidated WorkflowSettingsModal. Then rewire the `toolbar-right` JSX to compose them. The status pill is driven entirely by existing backend fields (`active`, `published_version`, `has_unpublished_changes`) plus the store's `dirty` flag. Unpublish reuses the existing PUT `active=false`; no new endpoints.

**Tech Stack:** React + TypeScript, Zustand store (`useEditor`), Vitest + @testing-library/react, existing `api.ts` client.

---

## File Structure

- **Create** `apps/web/src/editor/barStatus.ts` — pure function `deriveBarStatus(...)` returning pill label, dot color, version label, kind.
- **Create** `apps/web/src/editor/barStatus.test.ts` — unit tests for the helper.
- **Create** `apps/web/src/editor/useAutosave.ts` — debounced autosave hook.
- **Create** `apps/web/src/editor/useAutosave.test.tsx` — hook tests with fake timers.
- **Create** `apps/web/src/editor/SaveIndicator.tsx` — animated save status text/glyph.
- **Create** `apps/web/src/editor/SaveIndicator.test.tsx` — render tests.
- **Create** `apps/web/src/editor/PublishPill.tsx` — status pill button.
- **Create** `apps/web/src/editor/PublishPill.test.tsx` — render tests.
- **Create** `apps/web/src/editor/OverflowMenu.tsx` — ⋯ dropdown (generic item list).
- **Create** `apps/web/src/editor/OverflowMenu.test.tsx` — interaction tests.
- **Create** `apps/web/src/editor/A11yModal.tsx` — extract the existing in-file `A11yModal` so other components reuse it (`.modal-overlay`/`.modal` chrome + `useModalA11y` focus trap).
- **Create** `apps/web/src/editor/WorkflowSettingsModal.tsx` — Run timeout + MCP fields (moved off bar), built on `A11yModal`.
- **Create** `apps/web/src/editor/WorkflowSettingsModal.test.tsx` — render tests.
- **Modify** `apps/web/src/EditorPage.tsx` — compose the above; remove Save draft / Run / History / Functions / Export / ? / MCP / timeout / Active from the bar; add publish notes field + unpublish handler.
- **Modify** `apps/web/src/editor.css` — styles/animations for indicator, pill, menu, settings modal.

> **Spec deviation (intentional):** The spec listed "MCP settings" and "Run timeout" as two separate ⋯ items. This plan consolidates them into one **Workflow settings** modal/menu item to avoid two near-empty popovers. Export's three formats become three flat menu items rather than a nested submenu. Both reduce component complexity while keeping everything off the main bar as the spec intends.

---

## Task 1: Status derivation helper

**Files:**
- Create: `apps/web/src/editor/barStatus.ts`
- Test: `apps/web/src/editor/barStatus.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/editor/barStatus.test.ts
import { describe, it, expect } from "vitest";
import { deriveBarStatus } from "./barStatus";

describe("deriveBarStatus", () => {
  it("never published → neutral Publish", () => {
    const s = deriveBarStatus({ publishedVersion: null, active: false, hasUnpublishedChanges: false, dirty: false });
    expect(s).toMatchObject({ kind: "unpublished", pillLabel: "Publish", dot: null, versionLabel: "draft" });
  });

  it("published, active, clean → green Published", () => {
    const s = deriveBarStatus({ publishedVersion: 2, active: true, hasUnpublishedChanges: false, dirty: false });
    expect(s).toMatchObject({ kind: "published", pillLabel: "Published", dot: "green", versionLabel: "v2" });
  });

  it("local dirty edits → amber Publish changes", () => {
    const s = deriveBarStatus({ publishedVersion: 2, active: true, hasUnpublishedChanges: false, dirty: true });
    expect(s).toMatchObject({ kind: "draft", pillLabel: "Publish changes", dot: "amber", versionLabel: "draft · based on v2" });
  });

  it("backend unpublished changes → amber Publish changes", () => {
    const s = deriveBarStatus({ publishedVersion: 5, active: true, hasUnpublishedChanges: true, dirty: false });
    expect(s).toMatchObject({ kind: "draft", dot: "amber", versionLabel: "draft · based on v5" });
  });

  it("published but inactive, clean → neutral Republish", () => {
    const s = deriveBarStatus({ publishedVersion: 3, active: false, hasUnpublishedChanges: false, dirty: false });
    expect(s).toMatchObject({ kind: "inactive", pillLabel: "Republish", dot: "neutral", versionLabel: "v3 (unpublished)" });
  });

  it("draft changes take precedence over inactive", () => {
    const s = deriveBarStatus({ publishedVersion: 3, active: false, hasUnpublishedChanges: true, dirty: false });
    expect(s.kind).toBe("draft");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/editor/barStatus.test.ts`
Expected: FAIL — cannot find module `./barStatus`.

- [ ] **Step 3: Write minimal implementation**

```ts
// apps/web/src/editor/barStatus.ts
export type BarStatusKind = "unpublished" | "published" | "draft" | "inactive";

export interface BarStatusInput {
  publishedVersion: number | null;
  active: boolean;
  hasUnpublishedChanges: boolean;
  dirty: boolean;
}

export interface BarStatus {
  kind: BarStatusKind;
  pillLabel: string;
  /** dot color class suffix, or null for no dot */
  dot: "green" | "amber" | "neutral" | null;
  /** text shown in the toolbar meta line, before the node count */
  versionLabel: string;
}

export function deriveBarStatus(input: BarStatusInput): BarStatus {
  const { publishedVersion, active, hasUnpublishedChanges, dirty } = input;
  const isPublished = publishedVersion != null && publishedVersion > 0;
  const hasDraftChanges = hasUnpublishedChanges || dirty;

  if (!isPublished) {
    return { kind: "unpublished", pillLabel: "Publish", dot: null, versionLabel: "draft" };
  }
  if (hasDraftChanges) {
    return {
      kind: "draft",
      pillLabel: "Publish changes",
      dot: "amber",
      versionLabel: `draft · based on v${publishedVersion}`,
    };
  }
  if (!active) {
    return {
      kind: "inactive",
      pillLabel: "Republish",
      dot: "neutral",
      versionLabel: `v${publishedVersion} (unpublished)`,
    };
  }
  return { kind: "published", pillLabel: "Published", dot: "green", versionLabel: `v${publishedVersion}` };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/editor/barStatus.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/barStatus.ts apps/web/src/editor/barStatus.test.ts
git commit -m "feat(editor): bar status derivation helper"
```

---

## Task 2: Debounced autosave hook

**Files:**
- Create: `apps/web/src/editor/useAutosave.ts`
- Test: `apps/web/src/editor/useAutosave.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/useAutosave.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAutosave } from "./useAutosave";

describe("useAutosave", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("fires onSave after the debounce once dirty", () => {
    const onSave = vi.fn();
    renderHook(() => useAutosave({ enabled: true, dirty: true, delayMs: 1500, onSave }));
    expect(onSave).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1500);
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("does not fire when not dirty", () => {
    const onSave = vi.fn();
    renderHook(() => useAutosave({ enabled: true, dirty: false, delayMs: 1500, onSave }));
    vi.advanceTimersByTime(5000);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not fire when disabled", () => {
    const onSave = vi.fn();
    renderHook(() => useAutosave({ enabled: false, dirty: true, delayMs: 1500, onSave }));
    vi.advanceTimersByTime(5000);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("resets the timer when dirty toggles (coalesces rapid edits)", () => {
    const onSave = vi.fn();
    const { rerender } = renderHook(
      ({ d }) => useAutosave({ enabled: true, dirty: d, delayMs: 1500, onSave }),
      { initialProps: { d: true } },
    );
    vi.advanceTimersByTime(1000);
    rerender({ d: false }); // a save cleaned it
    rerender({ d: true });  // new edit
    vi.advanceTimersByTime(1000);
    expect(onSave).not.toHaveBeenCalled(); // not yet — timer restarted
    vi.advanceTimersByTime(500);
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/editor/useAutosave.test.tsx`
Expected: FAIL — cannot find module `./useAutosave`.

- [ ] **Step 3: Write minimal implementation**

```ts
// apps/web/src/editor/useAutosave.ts
import { useEffect, useRef } from "react";

export interface UseAutosaveOptions {
  enabled: boolean;
  dirty: boolean;
  delayMs?: number;
  onSave: () => void;
}

/**
 * Calls `onSave` once, `delayMs` after `dirty` becomes (or stays) true.
 * The timer restarts whenever `dirty`/`enabled` change, so rapid edits
 * coalesce into a single save once editing pauses.
 */
export function useAutosave({ enabled, dirty, delayMs = 1500, onSave }: UseAutosaveOptions): void {
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  useEffect(() => {
    if (!enabled || !dirty) return;
    const handle = window.setTimeout(() => onSaveRef.current(), delayMs);
    return () => window.clearTimeout(handle);
  }, [enabled, dirty, delayMs]);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/editor/useAutosave.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/useAutosave.ts apps/web/src/editor/useAutosave.test.tsx
git commit -m "feat(editor): debounced autosave hook"
```

---

## Task 3: SaveIndicator component

**Files:**
- Create: `apps/web/src/editor/SaveIndicator.tsx`
- Test: `apps/web/src/editor/SaveIndicator.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/SaveIndicator.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SaveIndicator } from "./SaveIndicator";

describe("SaveIndicator", () => {
  it("shows Saving… while saving", () => {
    render(<SaveIndicator state="saving" />);
    expect(screen.getByText(/saving…/i)).toBeInTheDocument();
  });

  it("shows saved text when saved", () => {
    render(<SaveIndicator state="saved" />);
    expect(screen.getByText(/all changes saved/i)).toBeInTheDocument();
  });

  it("shows unsaved text when there are pending edits", () => {
    render(<SaveIndicator state="unsaved" />);
    expect(screen.getByText(/unsaved/i)).toBeInTheDocument();
  });

  it("shows a retry button on error and calls onRetry", async () => {
    const onRetry = vi.fn();
    render(<SaveIndicator state="error" onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/editor/SaveIndicator.test.tsx`
Expected: FAIL — cannot find module `./SaveIndicator`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// apps/web/src/editor/SaveIndicator.tsx
export type SaveState = "saving" | "saved" | "unsaved" | "error";

export function SaveIndicator({ state, onRetry }: { state: SaveState; onRetry?: () => void }) {
  if (state === "saving") {
    return (
      <span className="save-indicator is-saving">
        <span className="save-spinner" aria-hidden /> Saving…
      </span>
    );
  }
  if (state === "unsaved") {
    return (
      <span className="save-indicator is-unsaved">
        <span className="save-pendingdot" aria-hidden /> Unsaved edits
      </span>
    );
  }
  if (state === "error") {
    return (
      <span className="save-indicator is-error">
        ⚠ Save failed —{" "}
        <button type="button" className="save-retry" onClick={onRetry}>
          Retry
        </button>
      </span>
    );
  }
  return (
    <span className="save-indicator is-saved">
      <span className="save-check" aria-hidden>✓</span> All changes saved
    </span>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/editor/SaveIndicator.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/SaveIndicator.tsx apps/web/src/editor/SaveIndicator.test.tsx
git commit -m "feat(editor): SaveIndicator component"
```

---

## Task 4: PublishPill component

**Files:**
- Create: `apps/web/src/editor/PublishPill.tsx`
- Test: `apps/web/src/editor/PublishPill.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/PublishPill.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PublishPill } from "./PublishPill";
import { deriveBarStatus } from "./barStatus";

describe("PublishPill", () => {
  it("renders the label and a green dot when published", () => {
    const status = deriveBarStatus({ publishedVersion: 2, active: true, hasUnpublishedChanges: false, dirty: false });
    const { container } = render(<PublishPill status={status} onClick={() => {}} />);
    expect(screen.getByRole("button", { name: /published/i })).toBeInTheDocument();
    expect(container.querySelector(".publish-dot.is-green")).toBeTruthy();
  });

  it("renders an amber dot for draft changes", () => {
    const status = deriveBarStatus({ publishedVersion: 2, active: true, hasUnpublishedChanges: true, dirty: false });
    const { container } = render(<PublishPill status={status} onClick={() => {}} />);
    expect(container.querySelector(".publish-dot.is-amber")).toBeTruthy();
  });

  it("renders no dot when never published", () => {
    const status = deriveBarStatus({ publishedVersion: null, active: false, hasUnpublishedChanges: false, dirty: false });
    const { container } = render(<PublishPill status={status} onClick={() => {}} />);
    expect(container.querySelector(".publish-dot")).toBeNull();
  });

  it("fires onClick", async () => {
    const onClick = vi.fn();
    const status = deriveBarStatus({ publishedVersion: 1, active: true, hasUnpublishedChanges: false, dirty: false });
    render(<PublishPill status={status} onClick={onClick} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/editor/PublishPill.test.tsx`
Expected: FAIL — cannot find module `./PublishPill`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// apps/web/src/editor/PublishPill.tsx
import type { BarStatus } from "./barStatus";

export function PublishPill({
  status,
  onClick,
  disabled,
}: {
  status: BarStatus;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={`btn publish-pill is-${status.kind}`}
      onClick={onClick}
      disabled={disabled}
      title="Publish the current draft as a production version"
    >
      {status.dot && <span className={`publish-dot is-${status.dot}`} aria-hidden />}
      {status.pillLabel}
    </button>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/editor/PublishPill.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/PublishPill.tsx apps/web/src/editor/PublishPill.test.tsx
git commit -m "feat(editor): PublishPill component"
```

---

## Task 5: OverflowMenu component

**Files:**
- Create: `apps/web/src/editor/OverflowMenu.tsx`
- Test: `apps/web/src/editor/OverflowMenu.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/OverflowMenu.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OverflowMenu } from "./OverflowMenu";

describe("OverflowMenu", () => {
  const items = [
    { id: "history", label: "History & versions", onSelect: vi.fn() },
    { id: "functions", label: "Functions", onSelect: vi.fn() },
    { id: "unpublish", label: "Unpublish workflow", danger: true, onSelect: vi.fn() },
  ];

  it("hides items until opened", () => {
    render(<OverflowMenu items={items} />);
    expect(screen.queryByText("History & versions")).toBeNull();
  });

  it("opens on trigger click and shows items", async () => {
    render(<OverflowMenu items={items} />);
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    expect(screen.getByText("History & versions")).toBeInTheDocument();
  });

  it("calls onSelect and closes when an item is clicked", async () => {
    const onSelect = vi.fn();
    render(<OverflowMenu items={[{ id: "x", label: "Do thing", onSelect }]} />);
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    await userEvent.click(screen.getByText("Do thing"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Do thing")).toBeNull();
  });

  it("skips falsy items (conditional entries)", async () => {
    render(<OverflowMenu items={[{ id: "a", label: "Shown", onSelect: vi.fn() }, null]} />);
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    expect(screen.getByText("Shown")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/editor/OverflowMenu.test.tsx`
Expected: FAIL — cannot find module `./OverflowMenu`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// apps/web/src/editor/OverflowMenu.tsx
import { useEffect, useRef, useState } from "react";

export interface OverflowItem {
  id: string;
  label: string;
  danger?: boolean;
  dividerBefore?: boolean;
  onSelect: () => void;
}

export function OverflowMenu({ items }: { items: (OverflowItem | null | false)[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const visible = items.filter((i): i is OverflowItem => Boolean(i));

  return (
    <div className="overflow-menu" ref={ref}>
      <button
        type="button"
        className="btn btn-icon overflow-trigger"
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        ⋯
      </button>
      {open && (
        <div className="overflow-dropdown" role="menu">
          {visible.map((item) => (
            <div key={item.id}>
              {item.dividerBefore && <div className="overflow-sep" />}
              <button
                type="button"
                role="menuitem"
                className={`overflow-item${item.danger ? " is-danger" : ""}`}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
              >
                {item.label}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/editor/OverflowMenu.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/OverflowMenu.tsx apps/web/src/editor/OverflowMenu.test.tsx
git commit -m "feat(editor): OverflowMenu component"
```

---

## Task 6: WorkflowSettingsModal (Run timeout + MCP)

**Files:**
- Create: `apps/web/src/editor/A11yModal.tsx`
- Create: `apps/web/src/editor/WorkflowSettingsModal.tsx`
- Test: `apps/web/src/editor/WorkflowSettingsModal.test.tsx`
- Modify: `apps/web/src/EditorPage.tsx` (replace the in-file `A11yModal` with an import)

This hosts the controls moved off the bar. It is presentational: values and setters are passed in, so save-on-change behavior stays in `EditorPage`. The editor's modals use a local `A11yModal` (`EditorPage.tsx:257`) that renders `.modal-overlay` > `.modal` and wires `useModalA11y`. Extract it so the new modal reuses the same chrome and focus trap.

- [ ] **Step 0: Extract A11yModal into its own file**

Create `apps/web/src/editor/A11yModal.tsx` by moving the existing component out of `EditorPage.tsx` verbatim (it is a module-level function at `EditorPage.tsx:257`), exporting it:

```tsx
// apps/web/src/editor/A11yModal.tsx
import { useRef, type ReactNode } from "react";
import { useModalA11y } from "../useModalA11y";

export function A11yModal({
  className,
  titleId,
  title,
  onClose,
  closeDisabled = false,
  children,
}: {
  className: string;
  titleId: string;
  title: string;
  onClose: () => void;
  closeDisabled?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useModalA11y(ref, onClose);
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className={`modal ${className}`}
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} disabled={closeDisabled} aria-label="Close">
            ✕
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
```

Then in `EditorPage.tsx`: delete the local `function A11yModal(...) { ... }` definition (lines ~257–300) and add `import { A11yModal } from "./editor/A11yModal";` with the other imports. Run `cd apps/web && npx tsc --noEmit` — expected PASS, all existing `<A11yModal>` usages now resolve to the import.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/editor/WorkflowSettingsModal.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowSettingsModal } from "./WorkflowSettingsModal";

const baseProps = {
  runTimeout: "",
  onRunTimeoutChange: vi.fn(),
  mcpEnabled: false,
  onMcpEnabledChange: vi.fn(),
  mcpToolName: "",
  onMcpToolNameChange: vi.fn(),
  mcpDescription: "",
  onMcpDescriptionChange: vi.fn(),
  onClose: vi.fn(),
};

describe("WorkflowSettingsModal", () => {
  it("shows the run timeout field", () => {
    render(<WorkflowSettingsModal {...baseProps} />);
    expect(screen.getByLabelText(/run timeout/i)).toBeInTheDocument();
  });

  it("calls onMcpEnabledChange when MCP toggled", async () => {
    const onMcpEnabledChange = vi.fn();
    render(<WorkflowSettingsModal {...baseProps} onMcpEnabledChange={onMcpEnabledChange} />);
    await userEvent.click(screen.getByLabelText(/expose as mcp tool/i));
    expect(onMcpEnabledChange).toHaveBeenCalledWith(true);
  });

  it("hides MCP tool name/description until MCP is enabled", () => {
    const { rerender } = render(<WorkflowSettingsModal {...baseProps} mcpEnabled={false} />);
    expect(screen.queryByLabelText(/tool name/i)).toBeNull();
    rerender(<WorkflowSettingsModal {...baseProps} mcpEnabled={true} />);
    expect(screen.getByLabelText(/tool name/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/editor/WorkflowSettingsModal.test.tsx`
Expected: FAIL — cannot find module `./WorkflowSettingsModal`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// apps/web/src/editor/WorkflowSettingsModal.tsx
import { A11yModal } from "./A11yModal";

export interface WorkflowSettingsModalProps {
  runTimeout: string;
  onRunTimeoutChange: (v: string) => void;
  mcpEnabled: boolean;
  onMcpEnabledChange: (v: boolean) => void;
  mcpToolName: string;
  onMcpToolNameChange: (v: string) => void;
  mcpDescription: string;
  onMcpDescriptionChange: (v: string) => void;
  onClose: () => void;
}

export function WorkflowSettingsModal(props: WorkflowSettingsModalProps) {
  const {
    runTimeout, onRunTimeoutChange,
    mcpEnabled, onMcpEnabledChange,
    mcpToolName, onMcpToolNameChange,
    mcpDescription, onMcpDescriptionChange,
    onClose,
  } = props;

  return (
    <A11yModal
      className="workflow-settings-modal"
      titleId="workflow-settings-title"
      title="Workflow settings"
      onClose={onClose}
    >
      <div className="workflow-settings-body">
        <label className="field">
          <span>Run timeout (seconds)</span>
          <input
            type="number"
            min={0}
            step={1}
            value={runTimeout}
            placeholder="No timeout"
            aria-label="Run timeout (seconds)"
            onChange={(e) => onRunTimeoutChange(e.target.value)}
          />
        </label>

        <label className="field-toggle">
          <input
            type="checkbox"
            checked={mcpEnabled}
            aria-label="Expose as MCP tool"
            onChange={(e) => onMcpEnabledChange(e.target.checked)}
          />
          <span>Expose as MCP tool (AI agents can call this workflow)</span>
        </label>

        {mcpEnabled && (
          <>
            <label className="field">
              <span>Tool name</span>
              <input
                value={mcpToolName}
                placeholder="Auto-generated from workflow name"
                aria-label="MCP tool name"
                onChange={(e) => onMcpToolNameChange(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Tool description</span>
              <input
                value={mcpDescription}
                placeholder="Shown to calling AI agents"
                aria-label="MCP tool description"
                onChange={(e) => onMcpDescriptionChange(e.target.value)}
              />
            </label>
          </>
        )}

        <div className="modal-actions">
          <button type="button" className="btn btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </A11yModal>
  );
}
```

> Note: `A11yModal` renders an `<h2>` title ("Workflow settings") in its header, so the body should not repeat it. The `aria-label`-based test query is replaced — the settings test queries fields by their own labels (Run timeout, Expose as MCP tool), which still resolve.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/editor/WorkflowSettingsModal.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/WorkflowSettingsModal.tsx apps/web/src/editor/WorkflowSettingsModal.test.tsx
git commit -m "feat(editor): WorkflowSettingsModal for timeout + MCP"
```

---

## Task 7: CSS for indicator, pill, menu, settings modal

**Files:**
- Modify: `apps/web/src/editor.css` (append a new section near the existing `/* ---- toolbar ---- */` styles)

- [ ] **Step 1: Add styles**

Append to `apps/web/src/editor.css`:

```css
/* ---- task bar: save indicator ---- */
.save-indicator { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; }
.save-indicator.is-saving { color: #9aa4b1; }
.save-indicator.is-saved { color: #6e7681; }
.save-indicator.is-unsaved { color: #e3b341; }
.save-indicator.is-error { color: #f08c8c; }
.save-spinner {
  width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid #2a3442; border-top-color: #58a6ff;
  animation: save-spin 0.7s linear infinite;
}
@keyframes save-spin { to { transform: rotate(360deg); } }
.save-check {
  width: 13px; height: 13px; border-radius: 50%;
  background: rgba(46, 160, 67, 0.19); color: #3fb950;
  display: inline-flex; align-items: center; justify-content: center; font-size: 9px;
  animation: save-pop 0.3s ease;
}
@keyframes save-pop { 0% { transform: scale(0.4); opacity: 0; } 100% { transform: scale(1); opacity: 1; } }
.save-pendingdot {
  width: 6px; height: 6px; border-radius: 50%; background: #e3b341;
  animation: save-blink 1s ease-in-out infinite;
}
@keyframes save-blink { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
.save-retry { background: none; border: none; color: #58a6ff; cursor: pointer; padding: 0; font: inherit; text-decoration: underline; }

/* ---- task bar: publish pill ---- */
.publish-pill { font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
.publish-pill.is-published { background: #11261a; border-color: rgba(46, 160, 67, 0.4); color: #7ee2a0; }
.publish-pill.is-draft { background: #2a2113; border-color: rgba(210, 153, 34, 0.4); color: #f0c674; }
.publish-pill.is-inactive,
.publish-pill.is-unpublished { /* inherit default .btn look */ }
.publish-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.publish-dot.is-green { background: #3fb950; box-shadow: 0 0 7px #3fb950; }
.publish-dot.is-amber { background: #e3b341; box-shadow: 0 0 7px #e3b341; animation: publish-pulse 1.6s ease-in-out infinite; }
.publish-dot.is-neutral { background: #6e7681; }
@keyframes publish-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.55; transform: scale(0.82); } }

/* ---- task bar: overflow menu ---- */
.overflow-menu { position: relative; }
.overflow-trigger { font-weight: 700; letter-spacing: 1px; }
.overflow-dropdown {
  position: absolute; right: 0; top: calc(100% + 6px); width: 230px; z-index: 30;
  background: #161b22; border: 1px solid #2a3442; border-radius: 9px; padding: 6px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6);
}
.overflow-item {
  display: flex; align-items: center; width: 100%; gap: 10px; padding: 8px 10px;
  border-radius: 6px; font-size: 12.5px; color: #c5ccd6; background: none; border: none;
  cursor: pointer; text-align: left;
}
.overflow-item:hover { background: #1e2733; }
.overflow-item.is-danger { color: #f08c8c; }
.overflow-sep { height: 1px; background: #222b36; margin: 5px 8px; }

/* ---- workflow settings modal ---- */
.workflow-settings-body { padding: 4px 2px; }
.workflow-settings-body .field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
.workflow-settings-body .field-toggle { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
```

- [ ] **Step 2: Typecheck/build sanity**

Run: `cd apps/web && npx tsc --noEmit`
Expected: PASS (no type errors introduced; CSS is not type-checked but confirms nothing else broke).

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/editor.css
git commit -m "style(editor): task bar indicator, pill, overflow menu, settings modal"
```

---

## Task 8: Wire the new bar into EditorPage

**Files:**
- Modify: `apps/web/src/EditorPage.tsx`

This is the integration task. Work in small edits and keep `npx tsc --noEmit` green throughout.

- [ ] **Step 1: Add imports and new state**

Near the other `editor/` imports at the top of `EditorPage.tsx`, add:

```tsx
import { deriveBarStatus } from "./editor/barStatus";
import { useAutosave } from "./editor/useAutosave";
import { SaveIndicator, type SaveState } from "./editor/SaveIndicator";
import { PublishPill } from "./editor/PublishPill";
import { OverflowMenu, type OverflowItem } from "./editor/OverflowMenu";
import { WorkflowSettingsModal } from "./editor/WorkflowSettingsModal";
```

With the other `useState` declarations (around line 341–347), add:

```tsx
const [settingsOpen, setSettingsOpen] = useState(false);
const [publishNotes, setPublishNotes] = useState("");
const [saveError, setSaveError] = useState(false);
const [unpublishing, setUnpublishing] = useState(false);
```

- [ ] **Step 2: Track save errors in `save()`**

In `save()` (around line 709), set `setSaveError(false)` at the start of the `try`, and in the `catch` add `setSaveError(true);` before the existing `notify(...)`. Leave the rest unchanged.

- [ ] **Step 3: Derive status + wire autosave**

After the `dirty` / `childDirty` / `nodeCount` selectors (around line 374), add:

```tsx
const barStatus = deriveBarStatus({
  publishedVersion: workflow?.published_version ?? null,
  active,
  hasUnpublishedChanges: Boolean(workflow?.has_unpublished_changes),
  dirty: dirty || childDirty,
});

const saveState: SaveState = saveError
  ? "error"
  : saving
    ? "saving"
    : dirty || childDirty
      ? "unsaved"
      : "saved";

useAutosave({
  enabled: Boolean(canWrite && id),
  dirty: dirty || childDirty,
  delayMs: 1500,
  onSave: () => { void save({ notifySuccess: false }); },
});
```

- [ ] **Step 4: Add the unpublish handler**

Near `publishDraft` (around line 760), add:

```tsx
async function unpublishWorkflow(): Promise<void> {
  if (!id || unpublishing) return;
  if (!window.confirm("Unpublish this workflow? It stops running in production until you publish again. Version history is kept.")) return;
  setUnpublishing(true);
  try {
    const updated = await api.updateWorkflow(id, { active: false });
    setWorkflow(updated);
    setActive(false);
    notify("Workflow unpublished.", "success");
  } catch (err) {
    notify(`Could not unpublish. ${errorMessage(err)}`, "error");
  } finally {
    setUnpublishing(false);
  }
}
```

- [ ] **Step 5: Pass real notes to publish**

In `publishDraft` (around line 767), change the hardcoded notes to use the field, then clear it on success:

```tsx
const published = await api.publishWorkflow(id, {
  notes: publishNotes.trim() || undefined,
  update_deployments: updateDeployments,
});
```

After the successful `setMessage(...)` / `notify(...)` lines, add `setPublishNotes("");`.

- [ ] **Step 6: Replace the toolbar-right JSX**

Replace the contents of `<div className="toolbar-right"> … </div>` (lines ~1351–1557) with the composed bar below. Keep `RunSettingsChip` exactly as it currently is — copy its existing props verbatim. Remove the timeout input, MCP toggle+inputs, Active toggle, ƒ Functions, ?, Export menu, Run, Stop, Save draft, Publish, and History buttons from the bar (their handlers now live in the menu / canvas pill / pill). Keep the Runs ▾ dropdown and AI Draft button as-is. `running`/Stop handling stays available via the canvas execute pill (out of scope here) — do not re-add Stop to the bar.

```tsx
<div className="toolbar-right">
  <SaveIndicator state={saveState} onRetry={() => { void save({ notifySuccess: false }); }} />

  {/* env dropdown — unchanged */}
  <RunSettingsChip
    environments={environments}
    environmentId={environmentId}
    onEnvChange={(envId) => {
      setEnvironmentId(envId);
      void saveRunSetting({ environment_id: envId });
    }}
    pools={runnerPoolsQuery.data ?? []}
    defaultRunnerPoolId={defaultRunnerPoolId}
    onRunnerChange={(poolId) => {
      setDefaultRunnerPoolId(poolId);
      void saveRunSetting({ default_runner_pool_id: poolId });
    }}
    saving={chipSaving}
  />

  {hasChatTrigger ? (
    <button type="button" className="btn" onClick={openChat} title="Open chat panel">
      Chat
    </button>
  ) : null}

  <button
    className="btn"
    onClick={() => {
      setAiMode("draft");
      setAiFixStrategy("minimal");
      setAiFailedNodeId(null);
      setAiFailedError(null);
      setAiPreview(null);
      setAiOpen(true);
    }}
    title="Generate an editable draft from a natural-language prompt"
  >
    ✨ AI Draft
  </button>

  <div className="runs-menu" ref={runsMenuRef}>
    <button className="btn" onClick={() => void openRuns()}>Runs ▾</button>
    {runsOpen && (
      <div className="runs-dropdown">
        {runsList.length === 0 && <p className="runs-empty muted">No runs yet.</p>}
        {runsList.map((r) => (
          <button key={r.id} className="runs-item" onClick={() => void viewRun(r.id)}>
            <span className={`run-pill status-run-${r.status}`}>{r.status}</span>
            <span className="runs-item-meta">
              {new Date(r.started_at).toLocaleString()} · {r.trigger_type}
            </span>
          </button>
        ))}
      </div>
    )}
  </div>

  {canWrite && (
    <PublishPill
      status={barStatus}
      onClick={openPublishReview}
      disabled={saving || publishing}
    />
  )}

  <OverflowMenu
    items={[
      { id: "history", label: "History & versions", onSelect: () => setShowHistory(true) },
      { id: "functions", label: "Functions", onSelect: () => setFunctionsOpen(true) },
      { id: "export-py", label: "Export · Python script (.py)", onSelect: () => void triggerExport(`/api/workflows/${id}/export.py`, `${name || "workflow"}.py`) },
      { id: "export-docker", label: "Export · Docker bundle (.zip)", onSelect: () => void triggerExport(`/api/workflows/${id}/export/docker`, `${name || "workflow"}-docker.zip`) },
      { id: "export-module", label: "Export · Python module (.py)", onSelect: () => void triggerExport(`/api/workflows/${id}/export.module.py`, `${name || "workflow"}_module.py`) },
      { id: "settings", label: "Workflow settings", dividerBefore: true, onSelect: () => setSettingsOpen(true) },
      { id: "shortcuts", label: "Keyboard shortcuts", onSelect: () => setShortcutsOpen(true) },
      canWrite && barStatus.kind !== "unpublished" && active
        ? { id: "unpublish", label: "Unpublish workflow", danger: true, dividerBefore: true, onSelect: () => void unpublishWorkflow() }
        : null,
    ] as (OverflowItem | null | false)[]}
  />
</div>
```

- [ ] **Step 7: Update the meta line to use the status**

Replace the `toolbar-meta` span contents (around line 1344–1349) with:

```tsx
<span className="toolbar-meta">
  {barStatus.versionLabel}
  {" · "}
  {nodeCount} node{nodeCount === 1 ? "" : "s"}
</span>
```

- [ ] **Step 8: Render the settings modal and add the publish notes field**

Where the other modals render (near `publishReviewOpen`, around line 1745), add the settings modal:

```tsx
{settingsOpen && (
  <WorkflowSettingsModal
    runTimeout={runTimeout}
    onRunTimeoutChange={setRunTimeout}
    mcpEnabled={mcpEnabled}
    onMcpEnabledChange={setMcpEnabled}
    mcpToolName={mcpToolName}
    onMcpToolNameChange={setMcpToolName}
    mcpDescription={mcpDescription}
    onMcpDescriptionChange={setMcpDescription}
    onClose={() => { setSettingsOpen(false); void save({ notifySuccess: false }); }}
  />
)}
```

Inside the existing `publish-review-modal`, add a notes textarea before the action buttons (after the `publish-review-notes` block, around line 1792):

```tsx
<label className="field publish-notes-field">
  <span>Version notes (optional)</span>
  <textarea
    value={publishNotes}
    placeholder="What changed in this version?"
    onChange={(e) => setPublishNotes(e.target.value)}
    rows={3}
  />
</label>
```

- [ ] **Step 9: Typecheck**

Run: `cd apps/web && npx tsc --noEmit`
Expected: PASS. Fix any references to removed variables (e.g. an unused `webhookListen`/`running` for the old Run button) by deleting the now-dead bar code only — do not remove logic still used by the canvas pill or run streaming.

- [ ] **Step 10: Run the full web test suite**

Run: `cd apps/web && npx vitest run`
Expected: PASS (new component tests + existing suite green).

- [ ] **Step 11: Commit**

```bash
git add apps/web/src/EditorPage.tsx
git commit -m "feat(editor): autosave bar with status pill, overflow menu, settings modal"
```

---

## Task 9: Manual verification

**Files:** none (verification only)

- [ ] **Step 1: Start the app and exercise the states**

Run the web app (per the project's run process) and confirm:
- Open a published, active workflow → green dot, **Published**, meta `v{n} · N nodes`.
- Edit a node → indicator shows `Saving…` then `All changes saved`; pill flips to amber **Publish changes**, meta `draft · based on v{n}`.
- Reload after editing → edit persisted (autosave worked).
- Click the pill → publish-review modal opens with a **Version notes** textarea; publishing clears notes and returns the dot to green.
- ⋯ menu → History, Functions, three Export items, Workflow settings (timeout + MCP), Keyboard shortcuts, and **Unpublish workflow** all work; Unpublish flips the pill to **Republish** and `active` to false.
- The env dropdown looks and behaves exactly as before.
- The canvas "Test workflow" pill still runs the workflow (Run is gone from the bar but running still works).

- [ ] **Step 2: Final typecheck + tests**

Run: `cd apps/web && npx tsc --noEmit && npx vitest run`
Expected: both PASS.

---

## Self-Review Notes

- **Spec coverage:** autosave (Task 2/8), animated indicator (Task 3/7), status pill green/amber/neutral (Task 1/4/7), meta line (Task 8 step 7), ⋯ menu with History/Functions/Export/Settings/Shortcuts/Unpublish (Task 5/8), MCP+timeout relocated (Task 6/8), publish notes field (Task 8), unpublish = `active:false` (Task 8), env dropdown untouched (Task 8 step 6), Run removed (Task 8 step 6). All covered.
- **Type consistency:** `BarStatus`/`deriveBarStatus`, `SaveState`/`SaveIndicator`, `OverflowItem`/`OverflowMenu` names are used identically across tasks.
- **Deviations flagged:** consolidated Settings modal and flat Export items (documented above).
```
