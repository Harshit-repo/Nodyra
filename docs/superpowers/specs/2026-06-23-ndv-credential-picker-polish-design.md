# NDV Credential Picker Polish — Design Spec
Date: 2026-06-23
Status: Approved

## Overview

A production-grade polish pass on the two credential UI surfaces in the Node Detail View (NDV): the inline `CredentialParamField` picker and the `CredentialCreateModal`. The visual direction is **clean minimal** — whitespace-driven, Notion/Vercel feel, reduced noise, no heavy decoration. All changes are Approach B (structural + visual): JSX and CSS both change.

---

## 1. Picker Trigger (`CredentialParamField` — closed state)

**Current:** 48px tall two-line button (bold name, muted meta-line below) + two separate buttons outside the picker (`+ New`, `Test`).

**Proposed:**

- Single 40px row. Left-to-right: type-colored dot (8px circle, color derived from credential type) → credential name (13px, weight 550) → scope pill (10px, rounded, accent-tinted) → `✓ Test` ghost chip (visible only when a credential is selected) → caret.
- The standalone `+ New` button outside the picker is **removed** — creation is always initiated from inside the dropdown footer.
- The `Test` button outside the picker is **removed** — it moves inline as the `✓ Test` chip on the trigger.
- **No-credential state:** dashed border trigger, muted `Choose a credential…` placeholder text, small `+ New` pill on the right (opens create modal directly).
- **Warning state (missing):** amber border + `background: rgba(245,158,11,.06)`, hollow dot with dashed amber ring, name reads "Credential unavailable", badge reads "missing".
- **Warning state (OAuth expiring):** same amber styling, badge reads `⚠ expires in N days` (shown when `oauth_expires_at` is within 7 days).

**Type dot colors** (new `CRED_TYPE_COLORS` map, falls back to `var(--accent)`):
- `slack_bot`, `discord_webhook` → `#22d3a5`
- `openai`, `anthropic`, `llm_provider`, `cohere` → `#f2a35c`
- `postgres`, `mysql` → `#60a5fa`
- `aws`, `azure_blob` → `#f59e0b`
- `github` → `#c084fc`
- Generic / unknown → `var(--accent)`

---

## 2. Picker Dropdown (open state)

**Current:** search box with border, list of options (name + scope badge + meta-line), footer with Clear / New credential / Manage credentials links.

**Proposed:**

- Dropdown container: `background: #111923`, `border: 1px solid #1f2e3d`, `border-radius: 8px`, `box-shadow: 0 4px 16px rgba(0,0,0,.35)`. Reduced shadow vs current.
- **Search row:** borderless integration — icon + placeholder `Search credentials…` on a `border-bottom: 1px solid #1f2e3d` row. No box around the input.
- **Credential option row:** type-colored dot → name (12.5px, weight 600) + meta-line below (`{type} · last used {relative time}`) → scope pill right-aligned. Selected row: checkmark on far-left + `background: rgba(59,130,246,.1)`.
- **Empty state (no matches exist):** centered emoji icon, `No {type} credentials yet`, one-line description, `+ New credential` CTA button — all inside the list area.
- **Empty state (search no results):** `No results for "{query}"` — `Try a different name or scope.`
- **Footer:** `Clear` (ghost link, far-left, visible only when a credential is selected) + `+ New credential` (primary button) + `Manage →` (muted link, far-right).
- **Keyboard navigation:** `↑`/`↓` moves focus through options, `Enter` selects, `Escape` closes, `Tab` closes and moves to next field. Focus ring: `outline: 2px solid var(--accent)` on the focused option row.

---

## 3. Create Modal (`CredentialCreateModal`)

**Current:** header (title + raw `credType` badge chip + close), 2-cell summary info-grid, Name + Scope in a 2-col grid, fields in another grid, security note in a box, footer buttons.

**Proposed:**

### Header
Single clean row: type-colored dot → `New credential` (14px, weight 650) → optional `Docs` link (shown when `preset.documentationUrl` exists, opens in new tab) → type pill (`font-family: monospace`, `border: 1px solid var(--line)`) → close button (24×24, ghost). No separate subtitle line.

### Name field
First field in the body, prominent. Label: `10.5px uppercase, weight 700, color: #5a7a94, letter-spacing: .07em`. "Required" hint on the right in `color: #3d5266`. Input: `background: #0a1018`, `border: 1.5px solid #1f2e3d`, `border-radius: 8px`, `padding: 10px 12px`, `font-size: 13.5px`.

### Scope — card tiles (replaces `<select>`)
Up to four horizontally arranged cards: **Global** ("All workflows") / **This workflow** ("Scoped only here", shown only when `workflowId` is set) / **Environment** ("Enter ID below") / **Runner pool** ("Enter ID below"). Selected card: `border: 1px solid var(--accent)`, `background: rgba(59,130,246,.12)`, label in `color: #6ab4f5`. Environment ID and Runner pool ID inputs appear below the grid (conditionally) when those cards are selected — same as current behaviour.

### Secret fields
Single-column layout (no 2-col grid). Each field:
- Label: `11px, weight 600, color: #8aa`, "required" helper on the right for required fields.
- Input: same high-contrast style as above.
- **Password fields** get a `Show`/`Hide` toggle button (absolute-positioned right inside the input, `10.5px`, `border: 1px solid #1f2e3d`, `border-radius: 5px`). Clicking toggles `type="password"` ↔ `type="text"`. Local state per field.
- **Error state on a field:** `border: 1.5px solid #f0747a`, `box-shadow: 0 0 0 4px rgba(240,116,122,.1)`, label turns `color: #f0747a`, inline error message below (`11.5px, color: #f0747a`). The existing modal-level `error` state still exists as a fallback for non-field errors.

### Security note
Muted inline line (no box): `font-size: 11px; color: #2d4255; line-height: 1.5` — `🔒 Secret values are encrypted at rest and never returned by the API after creation.`

### Footer buttons
Right-aligned row. **Cancel**: `background: transparent`, `color: #4a6075`, no border. **Test connection**: `border: 1.5px solid #1f2e3d`, `background: transparent`, `color: #8aa`. **Create credential**: `background: linear-gradient(135deg, #3b82f6, #2563eb)`, `color: #fff`, `font-weight: 700`, `box-shadow: 0 2px 8px rgba(59,130,246,.35)`. Loading states: "Creating…" / "Testing…" with `opacity: 0.7` on the active button.

---

## 4. Warning Banners

**Missing credential banner** (replaces current `credential-inline-warning`):
- `background: rgba(245,158,11,.08)`, `border: 1px solid rgba(245,158,11,.2)`, `border-radius: 6px`, `padding: 8px 10px`.
- ⚠ icon + message text + `Clear` button (ghost, amber border).

**Inline secret banner** (value typed directly, not a ref):
- Same amber styling + `Move` button (primary-sm) + `Clear` (ghost).

**Missing OAuth scopes banner:**
- Same amber styling, message lists missing scopes.

---

## 5. CSS Changes

All changes are in `index.css` under the existing `/* ---- credentials ---- */` section. No new CSS files. Classes that change:

- `.credential-picker-trigger` — height 40px, remove `min-height: 48px`, restructure flex layout for single-row design.
- `.credential-picker-trigger.has-selection` — lighter accent tint.
- `.credential-picker-menu` — reduced shadow, tighter radius.
- `.credential-picker-search` — remove border, add `border-bottom` separator.
- `.credential-picker-option` — add type dot slot, restructure for new meta-line layout.
- `.credential-picker-foot` — simplify to two items only.
- `.cred-quick-modal` — widen slightly to `min(640px, ...)`.
- `.cred-quick-modal-head` → replaced by inline header row styles.
- `.cred-quick-summary` → **removed** (replaced by inline header pill).
- `.cred-quick-grid` → **removed** (single-column layout).
- `.credential-form-field` — tighten label styles to match high-contrast direction.
- Add `.cred-scope-tiles` for the card-grid scope picker.
- Add `.cred-field-show-toggle` for the password show/hide button.
- `.credential-inline-warning` — update to amber panel style.

New CSS variables added to `:root`:
- `--cred-dot-ai: #f2a35c`
- `--cred-dot-messaging: #22d3a5`
- `--cred-dot-db: #60a5fa`
- `--cred-dot-cloud: #f59e0b`
- `--cred-dot-dev: #c084fc`

---

## 6. JS/TSX Changes

### `NodeDetails.tsx`

- **`CredentialParamField`**: remove the external `cred-add-btn` (`+ New`) and `Test` buttons. Add inline `✓ Test` chip to the trigger. Add keyboard nav to the dropdown list (`onKeyDown` on the wrapper, `aria-activedescendant`). Add `CRED_TYPE_COLORS` map. Add relative time formatter (`formatRelativeTime(last_used_at)`).
- **`CredentialCreateModal`**: replace `<select>` scope picker with card tiles (`cred-scope-tiles`). Add per-field show/hide toggle state (`showFields: Record<string, boolean>`). Move error display from modal-level only to per-field where possible (required field validation). Add `documentationUrl` link to header when present.
- **`credentialScopeLabel`** and **`credentialFieldSummary`** helpers stay, used in picker option meta-line.

### `credentialPresets.tsx`

No changes needed — `documentationUrl` and `authMethod` already on `CredentialPreset`.

---

## 7. What Does NOT Change

- Backend API, credential encryption, `org_keys.py` — untouched.
- `CredentialFieldInput` shared component — stays as-is (used in full `CredentialsPage` too; that page is out of scope).
- `CredentialsPage.tsx` full-page modal — out of scope.
- OAuth connect flow logic (`startOAuth`, popup handler) — unchanged.
- Credential matching logic (`credentialMatchesParam`) — unchanged.

---

## 8. Testing Checklist

- [ ] Picker shows type-colored dot for known types, falls back to accent for unknown
- [ ] Trigger shows `✓ Test` chip only when a credential is selected; clicking it calls `testSelectedCredential`
- [ ] No-credential state shows dashed ghost trigger with `Choose a credential…`
- [ ] `+ New` inside ghost trigger opens create modal directly
- [ ] Dropdown search filters by name, type, scope, fields
- [ ] ↑↓ moves keyboard focus through options; Enter selects; Esc closes
- [ ] Empty state (no credentials exist for type) shows icon + CTA
- [ ] Empty state (search returns nothing) shows "No results for…" text
- [ ] OAuth credential within 7 days of expiry shows amber expiry badge
- [ ] Missing credential shows hollow dot, "Credential unavailable", "missing" badge, amber banner
- [ ] Scope card tiles: Global / This workflow (when `workflowId` set) / Environment / Runner pool
- [ ] Selecting Environment or Runner pool reveals ID input below tiles
- [ ] Password fields have Show/Hide toggle; toggling reveals/hides value
- [ ] Required field validation shows per-field error (red border + inline message) on submit
- [ ] `documentationUrl` in preset → Docs link appears in modal header; absent → no link
- [ ] Create button shows gradient + shadow; disabled + "Creating…" during submit
- [ ] Test connection button shows "Testing…" during test call
- [ ] Warning banners: missing cred, inline secret, missing OAuth scopes — all amber-styled
- [ ] Existing `CredentialsPage.tsx` unaffected (verify page loads and creates credentials)
