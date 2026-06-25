# NDV Credential Picker Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the NDV credential picker trigger, dropdown, and create modal with a clean-minimal production-grade design covering all edge cases (missing credentials, OAuth expiry, empty states, keyboard navigation, show/hide password toggles, per-field validation errors).

**Architecture:** All changes live in two files: `apps/web/src/index.css` (CSS) and `apps/web/src/editor/NodeDetails.tsx` (JSX/logic). A new `CredentialModalField` component replaces `CredentialFieldInput` inside the modal only, keeping `CredentialsPage.tsx` and the shared `CredentialFieldInput` untouched.

**Tech Stack:** React (TSX), CSS custom properties, Phosphor Icons (already imported)

## Global Constraints

- `CredentialsPage.tsx` and `CredentialFieldInput` in `credentialPresets.tsx` must not be changed — they are out of scope.
- All CSS changes go inside the existing `/* ---- credentials ---- */` block in `index.css`. No new CSS files.
- Use existing CSS variables: `--bg`, `--surface`, `--line`, `--line-2`, `--accent`, `--accent-glow`, `--ink`, `--ink-2`, `--ink-3`, `--radius-sm`, `--font-mono`, `--z-ndv-picker`.
- Use existing semantic color variables: `--color-error`, `--color-error-bg`, `--color-error-border`, `--color-warning`, `--color-warning-bg`.
- New amber warning color: `--color-warning: #ffd479` already exists; use `rgba(245,158,11,…)` for amber surfaces (matches existing `.credential-picker-trigger.is-warning`).
- Phosphor icons already imported in NodeDetails.tsx: `Info`, `MagnifyingGlass`, `Plus`, `PushPin`, `WarningCircle`, `X`. Add `Eye`, `EyeSlash` for show/hide toggles.
- `isSecretField` from `./node-details/labels` already imported — use it to decide which fields get show/hide.
- Dev server: `cd apps/web && npm run dev` (already running under docker or locally on port 5173).

---

### Task 1: CSS variables + picker trigger + dropdown styles

**Files:**
- Modify: `apps/web/src/index.css` (`:root` block at line 10, credential section lines 3079–3284)

**What this produces:** All visual styles for the new trigger (40px single-row, ghost state, amber states) and the new dropdown (borderless search, option rows with dot/checkmark, empty states, keyboard focus, footer).

- [ ] **Step 1: Add CSS custom properties for type-colored dots**

In `apps/web/src/index.css`, inside the `:root` block (after line 62, before the closing `}`), add:

```css
  /* credential type dot colors */
  --cred-dot-ai: #f2a35c;
  --cred-dot-messaging: #22d3a5;
  --cred-dot-db: #60a5fa;
  --cred-dot-cloud: #f59e0b;
  --cred-dot-dev: #c084fc;
```

- [ ] **Step 2: Replace picker trigger CSS**

In `apps/web/src/index.css`, replace lines 3079–3160 (`.credential-param` through `.credential-picker-caret`) with:

```css
.credential-param {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.credential-select-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.credential-picker {
  position: relative;
  flex: 1 1 220px;
  min-width: 0;
}
.credential-picker-trigger {
  width: 100%;
  height: 40px;
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--ink);
  padding: 0 10px;
  cursor: pointer;
  transition: border-color 120ms ease, background 120ms ease;
}
.credential-picker-trigger:hover {
  border-color: var(--accent-deep);
  background: var(--surface-2);
}
.credential-picker-trigger:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}
/* selected state */
.credential-picker-trigger.has-selection {
  border-color: color-mix(in srgb, var(--accent) 40%, var(--line));
}
/* unset / no credential chosen */
.credential-picker-trigger.is-unset {
  border: 1px dashed var(--line);
  background: transparent;
  color: var(--ink-3);
}
.credential-picker-trigger.is-unset:hover {
  border-color: var(--accent-deep);
  background: var(--surface-2);
  color: var(--ink);
}
/* amber: missing or expiring */
.credential-picker-trigger.is-warning {
  border-color: rgba(245, 158, 11, 0.45);
  background: rgba(245, 158, 11, 0.06);
}
/* type dot */
.credential-type-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--accent);
}
.credential-type-dot.is-hollow {
  background: transparent;
  border: 1.5px dashed rgba(245, 158, 11, 0.7);
}
/* credential name on trigger */
.credential-picker-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
}
.credential-picker-name.is-placeholder {
  font-weight: 400;
  color: var(--ink-3);
}
/* scope pill on trigger */
.credential-scope-pill {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 99px;
  background: var(--accent-glow);
  color: var(--accent-2);
  letter-spacing: 0.03em;
  text-transform: lowercase;
}
.credential-scope-pill.is-warning {
  background: rgba(245, 158, 11, 0.18);
  color: #f59e0b;
}
/* inline test chip on trigger */
.credential-test-chip {
  flex-shrink: 0;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 4px;
  border: 1px solid var(--line);
  background: rgba(255,255,255,0.04);
  color: var(--ink-3);
  cursor: pointer;
  line-height: 1;
  white-space: nowrap;
  transition: border-color 100ms, color 100ms;
}
.credential-test-chip:hover {
  border-color: var(--accent-deep);
  color: var(--ink);
}
.credential-test-chip:disabled {
  opacity: 0.5;
  cursor: default;
}
/* unset +New pill */
.credential-new-pill {
  flex-shrink: 0;
  font-size: 10.5px;
  padding: 2px 8px;
  border-radius: 99px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  transition: border-color 100ms, color 100ms;
}
.credential-new-pill:hover {
  border-color: var(--accent);
  color: var(--ink);
}
/* caret */
.credential-picker-caret {
  flex-shrink: 0;
  color: var(--ink-3);
  font-size: 11px;
}
```

- [ ] **Step 3: Replace picker dropdown CSS**

In `apps/web/src/index.css`, replace lines 3157–3284 (`.credential-picker-menu` through `.cred-add-btn`) with:

```css
.credential-picker-menu {
  position: absolute;
  z-index: var(--z-ndv-picker);
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
  overflow: hidden;
}
/* borderless search row */
.credential-picker-search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-2);
}
.credential-picker-search svg {
  flex-shrink: 0;
  color: var(--ink-3);
}
.credential-picker-search input {
  flex: 1;
  font-family: var(--font-sans);
  font-size: 12.5px;
  color: var(--ink);
  background: transparent;
  border: none;
  outline: none;
}
.credential-picker-search input::placeholder {
  color: var(--ink-3);
}
/* option list */
.credential-picker-list {
  max-height: 220px;
  overflow-y: auto;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.credential-picker-option {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  text-align: left;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--ink);
  cursor: pointer;
  transition: background 80ms;
}
.credential-picker-option:hover {
  background: var(--surface-2);
}
.credential-picker-option.is-selected {
  background: var(--accent-glow);
}
.credential-picker-option.is-keyboard-focused {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
  background: var(--surface-2);
}
/* checkmark slot — always reserves space so names stay aligned */
.credential-picker-check {
  width: 14px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--accent);
}
/* text block inside option */
.credential-picker-option-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.credential-picker-option-name {
  font-size: 12.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.credential-picker-option-meta {
  font-size: 11px;
  color: var(--ink-3);
}
/* empty state inside list */
.credential-picker-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 20px 16px;
  text-align: center;
}
.credential-picker-empty-icon {
  font-size: 22px;
  line-height: 1;
}
.credential-picker-empty strong {
  font-size: 12.5px;
  color: var(--ink);
}
.credential-picker-empty span {
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.4;
}
/* footer */
.credential-picker-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-top: 1px solid var(--line-2);
}
.credential-picker-foot-clear {
  font-size: 12px;
  color: var(--ink-3);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0;
  line-height: 1;
}
.credential-picker-foot-clear:hover {
  color: var(--ink);
}
.credential-picker-foot-spacer {
  flex: 1;
}
.credential-picker-foot-manage {
  font-size: 11.5px;
  color: var(--ink-3);
  text-decoration: none;
}
.credential-picker-foot-manage:hover {
  color: var(--ink);
}
```

- [ ] **Step 4: Verify CSS compiles without errors**

Run: `cd D:/noodle/apps/web && npm run build 2>&1 | head -30`
Expected: No CSS errors. TypeScript errors fine at this stage (JSX not yet updated).

---

### Task 2: Create modal + fields + buttons + warning banner CSS

**Files:**
- Modify: `apps/web/src/index.css` (lines 3287–3434, the `.cred-quick-modal` through `.credential-test-result` block)

- [ ] **Step 1: Replace quick-modal + form field + button + warning CSS**

In `apps/web/src/index.css`, replace everything from `.cred-quick-modal {` (line 3287) through `.credential-test-result.is-error {` (line 3434) with:

```css
/* ---- credential create modal (NDV quick-create) ---- */
.cred-quick-modal {
  width: min(600px, calc(100vw - 40px));
  max-height: calc(100vh - 44px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* single-row modal header */
.cred-modal-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line-2);
}
.cred-modal-title {
  flex: 1;
  font-size: 14px;
  font-weight: 650;
  color: var(--ink);
  margin: 0;
}
.cred-modal-doc-link {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: var(--ink-3);
  text-decoration: none;
  flex-shrink: 0;
}
.cred-modal-doc-link:hover {
  color: var(--accent-2);
}
.cred-modal-type-pill {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-3);
  border: 1px solid var(--line);
  border-radius: 5px;
  padding: 2px 7px;
  flex-shrink: 0;
}
.cred-modal-close {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border: none;
  border-radius: 5px;
  background: rgba(255,255,255,0.05);
  color: var(--ink-3);
  cursor: pointer;
}
.cred-modal-close:hover {
  background: rgba(255,255,255,0.1);
  color: var(--ink);
}
/* scope card tiles */
.cred-scope-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 6px;
}
.cred-scope-tile {
  padding: 8px 10px;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: var(--bg);
  cursor: pointer;
  text-align: left;
  transition: border-color 100ms, background 100ms;
}
.cred-scope-tile:hover {
  border-color: var(--line);
  background: var(--surface-2);
}
.cred-scope-tile.is-selected {
  border-color: var(--accent);
  background: var(--accent-glow);
}
.cred-scope-tile-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}
.cred-scope-tile.is-selected .cred-scope-tile-label {
  color: var(--accent-2);
}
.cred-scope-tile-sub {
  display: block;
  font-size: 10.5px;
  color: var(--ink-3);
  margin-top: 2px;
  line-height: 1.3;
}
/* high-contrast credential form fields */
.cred-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.cred-field-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.cred-field-label-text {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-2);
  letter-spacing: 0.02em;
}
.cred-field-label-text.has-error {
  color: var(--color-error);
}
.cred-field-required {
  font-size: 10.5px;
  color: var(--ink-3);
  font-weight: 400;
}
.cred-field-input-wrap {
  position: relative;
}
.cred-field-input {
  width: 100%;
  background: var(--canvas);
  border: 1.5px solid var(--line);
  border-radius: 7px;
  padding: 9px 12px;
  font-size: 13px;
  font-family: var(--font-sans);
  color: var(--ink);
  outline: none;
  box-sizing: border-box;
  transition: border-color 120ms, box-shadow 120ms;
}
.cred-field-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}
.cred-field-input.has-error {
  border-color: var(--color-error);
  box-shadow: 0 0 0 3px var(--color-error-bg);
}
.cred-field-input.has-toggle {
  padding-right: 56px;
}
.cred-field-select {
  appearance: none;
  cursor: pointer;
  padding-right: 28px;
}
.cred-field-select-caret {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--ink-3);
  font-size: 11px;
  pointer-events: none;
}
/* password show/hide toggle */
.cred-field-show-toggle {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 10.5px;
  padding: 2px 6px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--ink-3);
  cursor: pointer;
  line-height: 1.4;
  transition: border-color 100ms, color 100ms;
}
.cred-field-show-toggle:hover {
  border-color: var(--accent-deep);
  color: var(--ink);
}
.cred-field-error {
  font-size: 11.5px;
  color: var(--color-error);
  line-height: 1.4;
}
.cred-field-help {
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.4;
}
.cred-field-textarea {
  resize: vertical;
  min-height: 80px;
}
/* security note */
.cred-security-note {
  font-size: 11px;
  color: var(--ink-3);
  line-height: 1.5;
  opacity: 0.7;
}
/* OAuth panel */
.credential-oauth-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}
.credential-oauth-panel h3 {
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 3px;
  color: var(--ink);
}
.credential-oauth-panel p,
.credential-oauth-panel small {
  font-size: 12px;
  color: var(--ink-3);
  margin: 0;
  line-height: 1.4;
}
.credential-oauth-panel small {
  display: block;
  margin-top: 4px;
}
/* modal footer buttons */
.cred-modal-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  padding-top: 4px;
}
.cred-btn-cancel {
  padding: 7px 14px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--ink-3);
  font-size: 12.5px;
  cursor: pointer;
  transition: color 100ms;
}
.cred-btn-cancel:hover {
  color: var(--ink);
}
.cred-btn-test {
  padding: 7px 14px;
  border: 1.5px solid var(--line);
  border-radius: 6px;
  background: transparent;
  color: var(--ink-2);
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color 100ms, color 100ms;
}
.cred-btn-test:hover {
  border-color: var(--accent-deep);
  color: var(--ink);
}
.cred-btn-test:disabled {
  opacity: 0.5;
  cursor: default;
}
.cred-btn-create {
  padding: 7px 16px;
  border: none;
  border-radius: 6px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  font-size: 12.5px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.35);
  transition: opacity 120ms, box-shadow 120ms;
  letter-spacing: 0.01em;
}
.cred-btn-create:hover {
  box-shadow: 0 3px 12px rgba(59, 130, 246, 0.5);
}
.cred-btn-create:disabled {
  opacity: 0.55;
  cursor: default;
  box-shadow: none;
}
/* warning banners (missing cred, inline secret, missing scopes) */
.credential-warning-banner {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: 6px;
  background: rgba(245, 158, 11, 0.07);
  font-size: 12px;
  color: var(--ink-2);
}
.credential-warning-banner svg {
  flex-shrink: 0;
  color: #f59e0b;
}
/* keep legacy classes working for CredentialsPage (out of scope) */
.credential-inline-warning {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: 6px;
  background: rgba(245, 158, 11, 0.07);
  font-size: 12px;
  color: var(--ink-2);
}
.credential-inline-warning svg {
  flex-shrink: 0;
  color: #f59e0b;
}
.credential-create-inline {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
}
.credential-create-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.credential-test-result {
  margin: 10px 0 0;
  font-size: 12px;
}
.credential-test-result.is-ok {
  color: #8fd06f;
}
.credential-test-result.is-error {
  color: #f0747a;
}
/* legacy form field (CredentialsPage, untouched) */
.credential-form-grid {
  display: grid;
  gap: 10px;
}
.credential-form-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13px;
}
.credential-form-field span {
  font-size: 12px;
  color: var(--ink-2);
}
.credential-form-field small {
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.35;
}
.credential-form-field textarea {
  resize: vertical;
}
```

- [ ] **Step 2: Remove now-unused classes from the file**

In `apps/web/src/index.css`, delete lines for `.cred-quick-modal-head`, `.cred-quick-modal-head h2`, `.cred-quick-modal-head .muted`, `.cred-quick-head-actions`, `.cred-quick-close`, `.cred-quick-summary`, `.cred-quick-summary > div`, `.cred-quick-summary span`, `.cred-quick-summary strong`, `.cred-quick-grid`, `.cred-quick-grid .credential-form-field:only-child`, `.cred-quick-scope`, `.cred-quick-scope label`, `.cred-quick-scope label.is-selected`, `.cred-quick-scope input`, `.cred-quick-scope span`, `.cred-quick-scope strong`, `.cred-quick-scope small`, `.cred-advanced-toggle`, `.cred-type`, `.cred-dots` (these were in lines ~3064–3393 and are replaced above). Leave `.cred-field-row` if it exists elsewhere, or remove if only in this section.

- [ ] **Step 3: Verify CSS build**

Run: `cd D:/noodle/apps/web && npm run build 2>&1 | grep -i "error\|warn" | head -20`
Expected: No CSS parse errors.

---

### Task 3: Utility functions + Eye icon import in NodeDetails.tsx

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (import line 1, and add utilities after line 180)

**Interfaces:**
- Produces:
  - `CRED_TYPE_COLORS: Record<string, string>` — maps type string to hex dot color
  - `credTypeDotColor(type: string): string` — returns color for a type, falls back to `"var(--accent)"`
  - `formatRelativeTime(iso: string | null | undefined): string` — "2 days ago", "just now", "never used"
  - `getDaysUntilExpiry(iso: string | null | undefined): number | null` — null if no expiry, negative if already expired

- [ ] **Step 1: Add Eye, EyeSlash to the Phosphor import**

In `apps/web/src/editor/NodeDetails.tsx`, line 1, change:

```tsx
import { Info, MagnifyingGlass, Plus, PushPin, WarningCircle, X } from "@phosphor-icons/react";
```

to:

```tsx
import { Eye, EyeSlash, Info, MagnifyingGlass, Plus, PushPin, WarningCircle, X } from "@phosphor-icons/react";
```

- [ ] **Step 2: Add utility functions after the `credentialFieldSummary` function (~line 186)**

After the `credentialMatchesSearch` function (line ~205 in NodeDetails.tsx), add:

```tsx
const CRED_TYPE_COLORS: Record<string, string> = {
  openai: "var(--cred-dot-ai)",
  anthropic: "var(--cred-dot-ai)",
  llm_provider: "var(--cred-dot-ai)",
  cohere: "var(--cred-dot-ai)",
  deepl: "var(--cred-dot-ai)",
  slack_bot: "var(--cred-dot-messaging)",
  discord_webhook: "var(--cred-dot-messaging)",
  smtp: "var(--cred-dot-messaging)",
  postgres: "var(--cred-dot-db)",
  mysql: "var(--cred-dot-db)",
  mongodb: "var(--cred-dot-db)",
  redis: "var(--cred-dot-db)",
  elasticsearch: "var(--cred-dot-db)",
  aws: "var(--cred-dot-cloud)",
  azure_blob: "var(--cred-dot-cloud)",
  github: "var(--cred-dot-dev)",
};

function credTypeDotColor(type: string): string {
  return CRED_TYPE_COLORS[type] ?? "var(--accent)";
}

function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "never used";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return "just now";
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;
  return `${Math.floor(months / 12)}y ago`;
}

function getDaysUntilExpiry(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  return Math.ceil(ms / 86400000);
}
```

- [ ] **Step 3: Verify TypeScript sees no new errors from utilities**

Run: `cd D:/noodle/apps/web && npx tsc --noEmit 2>&1 | head -30`
Expected: Only pre-existing errors (if any). No errors from the new functions.

---

### Task 4: Redesign CredentialParamField — trigger + keyboard nav state

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (the `CredentialParamField` function, lines ~1172–1541)

**Interfaces:**
- Consumes: `credTypeDotColor`, `formatRelativeTime`, `getDaysUntilExpiry` from Task 3; `CRED_TYPE_COLORS` map; all existing state/helpers in `CredentialParamField`.
- Produces: New JSX for the trigger button and adds `focusedIndex` state used in Task 5.

- [ ] **Step 1: Add focusedIndex state to CredentialParamField**

Inside `CredentialParamField`, after the existing state declarations (~line 1196), add:

```tsx
const [focusedIndex, setFocusedIndex] = useState(-1);
```

And update the `useEffect` that closes the picker on outside click to also reset focusedIndex:

```tsx
useEffect(() => {
  if (!pickerOpen) {
    setFocusedIndex(-1);
    return;
  }
  // ... existing closeOnOutside / closeOnEscape handlers unchanged ...
}, [pickerOpen]);
```

Also reset `focusedIndex` when `credentialQuery` changes — add this effect:

```tsx
useEffect(() => {
  setFocusedIndex(-1);
}, [credentialQuery]);
```

- [ ] **Step 2: Compute expiry state**

Inside `CredentialParamField`, after the `missingScopes` computation (~line 1252), add:

```tsx
const daysUntilExpiry = getDaysUntilExpiry(selectedCredential?.oauth_expires_at);
const isExpiringSoon = daysUntilExpiry !== null && daysUntilExpiry <= 7;
const isExpired = daysUntilExpiry !== null && daysUntilExpiry <= 0;
```

- [ ] **Step 3: Replace the trigger button JSX**

In `CredentialParamField`'s return, replace the current `<div className="credential-select-row">` block (the trigger button, caret, external New button, external Test button) with:

```tsx
<div className="credential-select-row" ref={pickerRef}>
  <div className="credential-picker">
    <button
      type="button"
      className={[
        "credential-picker-trigger",
        selectedCredential ? "has-selection" : !selected ? "is-unset" : "",
        (selectedMissing || isExpiringSoon) ? "is-warning" : "",
      ].filter(Boolean).join(" ")}
      disabled={loading}
      aria-haspopup="listbox"
      aria-expanded={pickerOpen}
      onClick={() => setPickerOpen((open) => !open)}
    >
      {/* type-colored dot */}
      <span
        className={`credential-type-dot${selectedMissing ? " is-hollow" : ""}`}
        style={selectedCredential ? { background: credTypeDotColor(selectedCredential.type) } : undefined}
      />

      {/* name or placeholder */}
      <span className={`credential-picker-name${!selectedCredential && !selectedMissing ? " is-placeholder" : ""}`}>
        {loading
          ? "Loading…"
          : selectedCredential
            ? selectedCredential.name
            : selectedMissing && selected
              ? "Credential unavailable"
              : "Choose a credential…"}
      </span>

      {/* scope pill (only when selected) */}
      {selectedCredential && (
        <span className={`credential-scope-pill${isExpiringSoon ? " is-warning" : ""}`}>
          {isExpired
            ? "expired"
            : isExpiringSoon
              ? `⚠ ${daysUntilExpiry}d`
              : credentialScopeLabel(selectedCredential)}
        </span>
      )}

      {/* missing badge */}
      {selectedMissing && selected && (
        <span className="credential-scope-pill is-warning">missing</span>
      )}

      {/* unset +New pill */}
      {!selected && !loading && (
        <button
          type="button"
          className="credential-new-pill"
          onClick={(e) => {
            e.stopPropagation();
            openCreateModal();
          }}
        >
          + New
        </button>
      )}

      {/* inline Test chip (only when credential exists) */}
      {selectedCredential && (
        <button
          type="button"
          className="credential-test-chip"
          disabled={testing}
          onClick={(e) => {
            e.stopPropagation();
            void testSelectedCredential();
          }}
        >
          {testing ? "…" : "✓ Test"}
        </button>
      )}

      <span className="credential-picker-caret" aria-hidden="true">▾</span>
    </button>

    {pickerOpen && (/* dropdown — see Task 5 */)}
  </div>
</div>
```

Note: The external `cred-add-btn` (`+ New`) button and the external `Test` button that currently sit outside the picker div are **removed** from the JSX entirely.

- [ ] **Step 4: TypeScript check**

Run: `cd D:/noodle/apps/web && npx tsc --noEmit 2>&1 | head -40`
Expected: Errors only in the incomplete `{/* dropdown */}` placeholder — fix by proceeding to Task 5.

---

### Task 5: Redesign CredentialParamField — picker dropdown

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (the dropdown JSX inside `CredentialParamField`)

**Interfaces:**
- Consumes: `focusedIndex`, `setFocusedIndex` from Task 4; `credTypeDotColor`, `formatRelativeTime` from Task 3.
- Produces: Complete dropdown with search, option rows (dot + checkmark + name + meta + scope pill), keyboard navigation handler, two empty states, footer (Clear · + New · Manage →).

- [ ] **Step 1: Add keyboard navigation handler**

Inside `CredentialParamField`, add this handler function (before the `return`):

```tsx
function handlePickerKeyDown(e: React.KeyboardEvent<HTMLDivElement>): void {
  if (!pickerOpen) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    setFocusedIndex((i) => Math.min(i + 1, visibleMatching.length - 1));
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    setFocusedIndex((i) => Math.max(i - 1, 0));
  } else if (e.key === "Enter" && focusedIndex >= 0 && focusedIndex < visibleMatching.length) {
    e.preventDefault();
    selectCredential(visibleMatching[focusedIndex]);
  } else if (e.key === "Escape") {
    e.preventDefault();
    setPickerOpen(false);
  } else if (e.key === "Tab") {
    setPickerOpen(false);
  }
}
```

- [ ] **Step 2: Replace the picker dropdown JSX**

Replace the `{pickerOpen && (<div className="credential-picker-menu">…</div>)}` block with:

```tsx
{pickerOpen && (
  <div
    className="credential-picker-menu"
    role="listbox"
    onKeyDown={handlePickerKeyDown}
  >
    {/* search */}
    <div className="credential-picker-search">
      <MagnifyingGlass size={13} aria-hidden="true" />
      <input
        autoFocus
        placeholder="Search credentials…"
        value={credentialQuery}
        onChange={(e) => setCredentialQuery(e.target.value)}
        aria-label="Search credentials"
      />
    </div>

    {/* list */}
    <div className="credential-picker-list">
      {visibleMatching.length > 0 ? (
        visibleMatching.map((cred, idx) => {
          const isSelected = selected?.id === cred.id;
          const isFocused = idx === focusedIndex;
          return (
            <button
              type="button"
              key={`${cred.id}:${targetKey}`}
              className={[
                "credential-picker-option",
                isSelected ? "is-selected" : "",
                isFocused ? "is-keyboard-focused" : "",
              ].filter(Boolean).join(" ")}
              role="option"
              aria-selected={isSelected}
              onClick={() => selectCredential(cred)}
              onMouseEnter={() => setFocusedIndex(idx)}
            >
              {/* checkmark slot — always present to keep alignment */}
              <span className="credential-picker-check" aria-hidden="true">
                {isSelected ? "✓" : ""}
              </span>
              {/* type dot */}
              <span
                className="credential-type-dot"
                style={{ background: credTypeDotColor(cred.type) }}
              />
              {/* text */}
              <span className="credential-picker-option-body">
                <span className="credential-picker-option-name">{cred.name}</span>
                <span className="credential-picker-option-meta">
                  {credentialTypeLabel(cred.type)} · {formatRelativeTime(cred.last_used_at)}
                </span>
              </span>
              {/* scope pill */}
              <span className="credential-scope-pill">
                {credentialScopeLabel(cred)}
              </span>
            </button>
          );
        })
      ) : matching.length === 0 ? (
        /* No credentials exist for this type */
        <div className="credential-picker-empty">
          <span className="credential-picker-empty-icon">🔑</span>
          <strong>No {credentialTypeLabel(meta?.type ?? spec.name)} credentials yet</strong>
          <span>Create one to connect this node.</span>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={openCreateModal}
          >
            <Plus size={12} weight="bold" /> New credential
          </button>
        </div>
      ) : (
        /* Search returned no results */
        <div className="credential-picker-empty">
          <strong>No results for "{credentialQuery}"</strong>
          <span>Try a different name, type, or scope.</span>
        </div>
      )}
    </div>

    {/* footer */}
    <div className="credential-picker-foot">
      {selected && (
        <button
          type="button"
          className="credential-picker-foot-clear"
          onClick={() => {
            onChange("");
            setPickerOpen(false);
          }}
        >
          Clear
        </button>
      )}
      <span className="credential-picker-foot-spacer" />
      <button
        type="button"
        className="btn btn-sm btn-primary"
        onClick={openCreateModal}
      >
        <Plus size={12} weight="bold" /> New credential
      </button>
      <a
        className="credential-picker-foot-manage"
        href="/credentials"
        target="_blank"
        rel="noreferrer"
      >
        Manage →
      </a>
    </div>
  </div>
)}
```

- [ ] **Step 3: Verify Credential type has last_used_at**

Run: `grep -n "last_used_at" D:/noodle/apps/web/src/types.ts`
Expected: `last_used_at?: string | null` (or similar) on the `Credential` interface. If missing, add it. If the field name is different, update `formatRelativeTime(cred.last_used_at)` calls to match.

- [ ] **Step 4: TypeScript check**

Run: `cd D:/noodle/apps/web && npx tsc --noEmit 2>&1 | head -40`
Expected: No new errors. Fix any type mismatches before continuing.

- [ ] **Step 5: Commit CSS + trigger + dropdown**

```bash
cd D:/noodle
git add apps/web/src/index.css apps/web/src/editor/NodeDetails.tsx
git commit -m "feat: redesign credential picker trigger and dropdown"
```

---

### Task 6: Redesign warning banners in CredentialParamField

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (warning banner JSX in `CredentialParamField`, after the picker div)

**What this replaces:** The three `<div className="credential-inline-warning">` blocks (missing credential, inline secret, missing OAuth scopes).

- [ ] **Step 1: Replace missing credential warning**

Replace:
```tsx
{selectedMissing && selected && (
  <div className="credential-inline-warning">
    <WarningCircle size={15} weight="fill" />
    <span>
      The selected credential is unavailable or no longer contains the
      field this node needs.
    </span>
  </div>
)}
```
With:
```tsx
{selectedMissing && selected && (
  <div className="credential-warning-banner">
    <WarningCircle size={14} weight="fill" />
    <span style={{ flex: 1 }}>
      This credential was deleted or is no longer visible to this workflow.
    </span>
    <button
      type="button"
      className="btn btn-sm btn-ghost"
      onClick={() => { onChange(""); setError(""); }}
    >
      Clear
    </button>
  </div>
)}
```

- [ ] **Step 2: Replace inline secret warning**

Replace:
```tsx
{inlineValue && !selected && (
  <div className="credential-inline-warning">
    <WarningCircle size={15} weight="fill" />
    <span>Inline secret in workflow — move to credential store.</span>
    <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void moveInline()}>
      Move
    </button>
    <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange("")}>
      Clear
    </button>
  </div>
)}
```
With:
```tsx
{inlineValue && !selected && (
  <div className="credential-warning-banner">
    <WarningCircle size={14} weight="fill" />
    <span style={{ flex: 1 }}>Inline secret in workflow — move to credential store.</span>
    <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => void moveInline()}>
      {busy ? "Moving…" : "Move"}
    </button>
    <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange("")}>
      Clear
    </button>
  </div>
)}
```

- [ ] **Step 3: Replace missing OAuth scopes warning**

Replace:
```tsx
{missingScopes.length > 0 && (
  <div className="credential-inline-warning">
    <WarningCircle size={15} weight="fill" />
    <span>Missing OAuth scopes: {missingScopes.join(", ")}</span>
  </div>
)}
```
With:
```tsx
{missingScopes.length > 0 && (
  <div className="credential-warning-banner">
    <WarningCircle size={14} weight="fill" />
    <span>Missing OAuth scopes: {missingScopes.join(", ")}</span>
  </div>
)}
```

- [ ] **Step 4: Commit warning banners**

```bash
cd D:/noodle
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat: redesign credential warning banners to amber panel style"
```

---

### Task 7: Redesign CredentialCreateModal — header + scope tiles

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (the `CredentialCreateModal` function, lines ~656–1170)

**Interfaces:**
- Consumes: `credTypeDotColor` from Task 3; `preset.documentationUrl` from existing `CredentialPreset` type.
- Produces: New modal header row (dot + title + optional Docs link + type pill + close); scope state replaced from `useState<CredentialScope>` to card-tile selection UI.

- [ ] **Step 1: Replace modal header JSX**

Find and replace the current `<div className="modal-overlay">` → `<div className="modal cred-quick-modal">` → `<div className="credential-modal-head cred-quick-modal-head">` block with:

```tsx
return (
  <div className="modal-overlay" onClick={onClose}>
    <div
      className="modal cred-quick-modal"
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="cred-quick-modal-title"
      tabIndex={-1}
      onClick={(e) => e.stopPropagation()}
    >
      {/* header row */}
      <div className="cred-modal-header">
        <span
          className="credential-type-dot"
          style={{ background: credTypeDotColor(credType) }}
        />
        <h2 id="cred-quick-modal-title" className="cred-modal-title">
          New credential
        </h2>
        {preset.documentationUrl && (
          <a
            className="cred-modal-doc-link"
            href={preset.documentationUrl}
            target="_blank"
            rel="noreferrer"
            title="Documentation"
          >
            <Info size={13} />
            Docs
          </a>
        )}
        <span className="cred-modal-type-pill">{credType}</span>
        <button
          type="button"
          className="cred-modal-close"
          onClick={onClose}
          aria-label="Close"
        >
          <X size={13} weight="bold" />
        </button>
      </div>
      {/* body continues below… */}
```

- [ ] **Step 2: Remove cred-quick-summary JSX**

Delete the entire `<div className="cred-quick-summary">…</div>` block (the one with the TYPE and FIELDS info-grid) from the modal body. It is replaced by the new header.

- [ ] **Step 3: Replace scope <select> with scope tile cards**

Find the scope select:
```tsx
<label className="credential-form-field">
  <span>Scope</span>
  <select
    className="field-input"
    value={scope}
    onChange={(e) => setScope(e.target.value as CredentialScope)}
  >
    <option value="global">Global</option>
    <option value="environment">Environment</option>
    {workflowId && <option value="workflow">This workflow</option>}
    <option value="runner_pool">Runner pool</option>
  </select>
</label>
```

Replace with:

```tsx
<div>
  <div style={{ fontSize: "11px", fontWeight: 600, color: "var(--ink-2)", marginBottom: 8 }}>
    Scope
  </div>
  <div className="cred-scope-tiles">
    <button
      type="button"
      className={`cred-scope-tile${scope === "global" ? " is-selected" : ""}`}
      onClick={() => setScope("global")}
    >
      <span className="cred-scope-tile-label">Global</span>
      <span className="cred-scope-tile-sub">All workflows</span>
    </button>
    {workflowId && (
      <button
        type="button"
        className={`cred-scope-tile${scope === "workflow" ? " is-selected" : ""}`}
        onClick={() => setScope("workflow")}
      >
        <span className="cred-scope-tile-label">This workflow</span>
        <span className="cred-scope-tile-sub">Scoped only here</span>
      </button>
    )}
    <button
      type="button"
      className={`cred-scope-tile${scope === "environment" ? " is-selected" : ""}`}
      onClick={() => setScope("environment")}
    >
      <span className="cred-scope-tile-label">Environment</span>
      <span className="cred-scope-tile-sub">Enter ID below</span>
    </button>
    <button
      type="button"
      className={`cred-scope-tile${scope === "runner_pool" ? " is-selected" : ""}`}
      onClick={() => setScope("runner_pool")}
    >
      <span className="cred-scope-tile-label">Runner pool</span>
      <span className="cred-scope-tile-sub">Enter ID below</span>
    </button>
  </div>
</div>
```

Keep the existing conditional `scope === "environment"` and `scope === "runner_pool"` ID inputs exactly as they are — they still work; only the scope selector changed.

- [ ] **Step 4: Remove the Name field from the 2-col credential-form-grid**

The current `<div className="credential-form-grid cred-quick-grid">` contains Name + Scope. After the above changes, Scope is now a tile group. Name stays as a standalone `<label className="credential-form-field">` but must be **outside** the old 2-col grid. Move the Name label to sit directly inside the modal body (before the scope tiles). Change its className to use new cred-field classes:

```tsx
<div className="cred-field">
  <div className="cred-field-label">
    <span className="cred-field-label-text">Name</span>
    <span className="cred-field-required">required</span>
  </div>
  <input
    className="cred-field-input"
    placeholder="My credential"
    value={name}
    onChange={(e) => setName(e.target.value)}
    onKeyDown={(e) => {
      if (e.key === "Enter" && !isOAuth) void handleCreate();
      if (e.key === "Escape") onClose();
    }}
  />
</div>
```

- [ ] **Step 5: TypeScript check**

Run: `cd D:/noodle/apps/web && npx tsc --noEmit 2>&1 | head -40`
Expected: Only errors related to the remaining old-style form fields (Task 8 will fix those).

- [ ] **Step 6: Commit header + scope tiles**

```bash
cd D:/noodle
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat: modal header single row + scope as card tiles"
```

---

### Task 8: CredentialCreateModal — new CredentialModalField component + per-field errors

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (add `CredentialModalField` component; update `CredentialCreateModal` body fields and `collectData` validation)

**Why a separate component instead of reusing `CredentialFieldInput`:** The new design needs show/hide toggle per password field and per-field error display. `CredentialFieldInput` (in `credentialPresets.tsx`) is also used by `CredentialsPage.tsx` which is out of scope — we must not change it. So we create `CredentialModalField` inside `NodeDetails.tsx` for NDV-only use.

**Interfaces:**
- Produces: `CredentialModalField` component; `fieldErrors: Record<string, string>` state; `showFields: Record<string, boolean>` state.

- [ ] **Step 1: Add CredentialModalField component**

Before the `CredentialCreateModal` function definition (~line 656), add:

```tsx
function CredentialModalField({
  field,
  value,
  onChange,
  error,
}: {
  field: import("../credentialPresets").CredentialFormField;
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  const [showPassword, setShowPassword] = useState(false);
  const isPassword = field.kind === "password";
  const hasError = Boolean(error);

  if (field.kind === "select") {
    return (
      <div className="cred-field">
        <div className="cred-field-label">
          <span className={`cred-field-label-text${hasError ? " has-error" : ""}`}>{field.label}</span>
          {field.required && <span className="cred-field-required">required</span>}
        </div>
        <div className="cred-field-input-wrap">
          <select
            className="cred-field-input cred-field-select"
            value={value}
            onChange={(e) => onChange(e.target.value)}
          >
            {(field.options ?? []).map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <span className="cred-field-select-caret" aria-hidden="true">▾</span>
        </div>
        {field.help && <span className="cred-field-help">{field.help}</span>}
      </div>
    );
  }

  if (field.kind === "textarea") {
    return (
      <div className="cred-field">
        <div className="cred-field-label">
          <span className={`cred-field-label-text${hasError ? " has-error" : ""}`}>{field.label}</span>
          {field.required && <span className="cred-field-required">required</span>}
        </div>
        <textarea
          className={`cred-field-input cred-field-textarea${hasError ? " has-error" : ""}`}
          placeholder={field.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {error && <span className="cred-field-error">{error}</span>}
        {field.help && <span className="cred-field-help">{field.help}</span>}
      </div>
    );
  }

  return (
    <div className="cred-field">
      <div className="cred-field-label">
        <span className={`cred-field-label-text${hasError ? " has-error" : ""}`}>{field.label}</span>
        {field.required && <span className="cred-field-required">required</span>}
      </div>
      <div className="cred-field-input-wrap">
        <input
          className={`cred-field-input${isPassword ? " has-toggle" : ""}${hasError ? " has-error" : ""}`}
          type={isPassword && !showPassword ? "password" : field.kind === "number" ? "number" : "text"}
          placeholder={field.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {isPassword && (
          <button
            type="button"
            className="cred-field-show-toggle"
            onClick={() => setShowPassword((s) => !s)}
            aria-label={showPassword ? "Hide" : "Show"}
          >
            {showPassword ? <EyeSlash size={11} /> : <Eye size={11} />}
            {showPassword ? "Hide" : "Show"}
          </button>
        )}
      </div>
      {error && <span className="cred-field-error">{error}</span>}
      {field.help && !error && <span className="cred-field-help">{field.help}</span>}
    </div>
  );
}
```

- [ ] **Step 2: Add fieldErrors state to CredentialCreateModal**

Inside `CredentialCreateModal`, after the existing state declarations, add:

```tsx
const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
```

- [ ] **Step 3: Update collectData to use per-field errors**

Replace the existing `collectData` function with a version that sets `fieldErrors` instead of setting a single `error`:

```tsx
function collectData(): Record<string, string> | null {
  const newErrors: Record<string, string> = {};
  const data: Record<string, string> = {};

  if (isLlm) {
    data.provider = fieldValues.provider || "openai";
    if (variant?.apiKey === "required" && !fieldValues.api_key?.trim()) {
      newErrors.api_key = "API key is required.";
    }
    if (Object.keys(newErrors).length > 0) {
      setFieldErrors(newErrors);
      return null;
    }
    for (const key of visibleCredentialFields(fieldValues.provider, true)) {
      if (fieldValues[key]?.trim()) data[key] = fieldValues[key].trim();
    }
    setFieldErrors({});
    return data;
  }

  for (const field of preset.fields) {
    const value = fieldValues[field.key] ?? "";
    if (field.required && !value.trim()) {
      newErrors[field.key] = `${field.label} is required.`;
    } else if (value.trim() || field.defaultValue !== undefined) {
      data[field.key] = value.trim();
    }
  }

  if (credType === "google_sheets" && !data.api_key && !data.access_token) {
    newErrors.api_key = "Enter either API key or OAuth access token.";
    newErrors.access_token = "Enter either API key or OAuth access token.";
  }

  if (Object.keys(newErrors).length > 0) {
    setFieldErrors(newErrors);
    return null;
  }
  setFieldErrors({});
  return data;
}
```

Also clear `fieldErrors` when `handleCreate` or `handleTest` succeeds (add `setFieldErrors({})` alongside `setError("")` in the try/catch handlers).

- [ ] **Step 4: Replace CredentialFieldInput usage in modal body with CredentialModalField**

In the modal body, find the section that renders `CredentialFieldInput` components:

```tsx
{isLlm
  ? renderedFields.map((key) => { ... <CredentialFieldInput ... /> })
  : preset.fields.map((field) => <CredentialFieldInput ... />)}
```

Replace the `isLlm` branch's `CredentialFieldInput` with `CredentialModalField` and pass `error`:
```tsx
{isLlm
  ? renderedFields.map((key) => {
      const def = LLM_FIELD_DEFS[key];
      if (!def) return null;
      const required = key === "api_key" && variant?.apiKey === "required";
      return (
        <CredentialModalField
          key={key}
          field={{ ...def, required }}
          value={fieldValues[key] ?? ""}
          onChange={(next) => setField(key, next)}
          error={fieldErrors[key]}
        />
      );
    })
  : preset.fields.map((field) => (
      <CredentialModalField
        key={field.key}
        field={field}
        value={fieldValues[field.key] ?? ""}
        onChange={(next) => setField(field.key, next)}
        error={fieldErrors[field.key]}
      />
    ))}
```

Remove the wrapping `<div className="credential-form-grid cred-quick-grid">` around these fields — replace with a simple `<div style={{ display: "flex", flexDirection: "column", gap: 12 }}>`.

- [ ] **Step 5: Edge case — isOAuth modal has no form fields**

When `isOAuth` is true, the OAuth panel renders instead of form fields. `CredentialModalField` and `fieldErrors` are never used. Verify: `isOAuth && <div className="credential-oauth-panel">…</div>` renders above the field section. The existing OAuth panel JSX stays — just make sure it uses `.credential-oauth-panel` (update it to the new class if needed per Task 2 CSS).

- [ ] **Step 6: TypeScript check**

Run: `cd D:/noodle/apps/web && npx tsc --noEmit 2>&1 | head -40`
Expected: No errors.

- [ ] **Step 7: Commit CredentialModalField + per-field errors**

```bash
cd D:/noodle
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat: add CredentialModalField with show/hide toggle and per-field errors"
```

---

### Task 9: CredentialCreateModal — update security note + footer buttons

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx` (modal footer)

- [ ] **Step 1: Replace security note**

Find:
```tsx
<div className="credential-security-note">
  Secret values are encrypted at rest and are not returned by the API
  after creation.
</div>
```

Replace with:
```tsx
<p className="cred-security-note">
  🔒 Secret values are encrypted at rest and are never returned by the API after creation.
</p>
```

- [ ] **Step 2: Replace modal-level error text**

Find `{error && <p className="error-text">{error}</p>}`. Keep it (it handles non-field errors like network failures and scope validation errors) but also clear `fieldErrors` in `setError("")` calls — already handled in Task 8's `collectData`.

- [ ] **Step 3: Replace modal footer buttons**

Find the `<div className="modal-actions">` block and replace with:

```tsx
<div className="cred-modal-footer">
  <button
    type="button"
    className="cred-btn-cancel"
    onClick={onClose}
  >
    Cancel
  </button>
  {!isOAuth && (
    <button
      type="button"
      className="cred-btn-test"
      disabled={testing}
      onClick={() => void handleTest()}
    >
      {testing ? "Testing…" : "Test connection"}
    </button>
  )}
  <button
    type="button"
    className="cred-btn-create"
    disabled={busy || !name.trim()}
    onClick={() => void handleCreate()}
  >
    {busy ? "Creating…" : isOAuth ? "Save manual" : "Create credential"}
  </button>
</div>
```

- [ ] **Step 4: Final TypeScript check**

Run: `cd D:/noodle/apps/web && npx tsc --noEmit 2>&1`
Expected: Zero errors.

- [ ] **Step 5: Commit modal footer**

```bash
cd D:/noodle
git add apps/web/src/editor/NodeDetails.tsx
git commit -m "feat: update modal security note and footer button styles"
```

---

### Task 10: Visual verification + edge case walkthrough

**Files:** None (verification only)

- [ ] **Step 1: Run the dev server**

```bash
cd D:/noodle && docker compose -f deploy/docker-compose.yml logs web --tail=5
```
Or locally: `cd D:/noodle/apps/web && npm run dev`
Open: http://localhost:5173

- [ ] **Step 2: Verify — no credential selected**
  - Open any node that has a credential param
  - Expected: dashed ghost trigger "Choose a credential…", `+ New` pill on the right, no external buttons alongside

- [ ] **Step 3: Verify — dropdown open, no credentials yet**
  - Click the ghost trigger
  - Expected: borderless search, empty-state with 🔑 icon and "New credential" CTA

- [ ] **Step 4: Verify — create a credential**
  - Click "New credential" in empty state or from picker footer
  - Expected: modal opens with single-row header (dot + "New credential" + type pill + X), scope cards (Global / This workflow / Environment / Runner pool), single-column fields, Show/Hide toggle on password fields

- [ ] **Step 5: Verify — scope tiles**
  - Click "Environment" tile — ID input appears below
  - Click "Runner pool" tile — pool ID input appears
  - Click "Global" — no extra input
  - "This workflow" appears only when workflow has an ID

- [ ] **Step 6: Verify — required field validation**
  - Submit without filling required fields
  - Expected: per-field red border + inline error message per field, NOT a single error at the bottom

- [ ] **Step 7: Verify — credential selected state**
  - Create a credential, select it
  - Expected: trigger shows type dot (colored) + name + scope pill + "✓ Test" chip + caret. No external "New" or "Test" buttons.

- [ ] **Step 8: Verify — dropdown with credentials**
  - Open picker when credentials exist
  - Expected: options show checkmark slot + type dot + name (bold) + meta-line (`{type} · {relative time}`) + scope pill. Selected item has ✓ and accent background.

- [ ] **Step 9: Verify — keyboard navigation**
  - Open picker → Tab to search → ArrowDown → ArrowDown → Enter
  - Expected: second credential selected, picker closes

- [ ] **Step 10: Verify — OAuth expiry warning**
  - If an OAuth credential exists with `oauth_expires_at` within 7 days: trigger shows amber border + `⚠ Nd` scope pill
  - With `oauth_expires_at` in the past: pill reads "expired"

- [ ] **Step 11: Verify — Docs link**
  - Open create modal for a credential type with a `documentationUrl`
  - Expected: "Docs" link appears in header between title and type pill

- [ ] **Step 12: Verify — CredentialsPage unaffected**
  - Navigate to /credentials
  - Expected: page loads, creates credentials, shows credential list — no visual regressions

- [ ] **Step 13: Final commit**

```bash
cd D:/noodle
git add -A
git commit -m "feat: complete NDV credential picker polish (trigger, dropdown, modal, warnings)"
```
