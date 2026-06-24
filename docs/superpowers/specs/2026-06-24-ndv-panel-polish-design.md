# NDV Panel Polish Design

**Date:** 2026-06-24  
**Branch:** feat/node-expansion-plan  
**Scope:** Visual and UX polish for the Node Detail View (NDV) modal — fixed height, scroll, drag-resize, listening animation, and general polish.

---

## Overview

The NDV modal currently has no fixed height, which means the middle parameters column never scrolls and the panel can grow taller than the viewport. This spec covers:

1. Fixed default height + drag-to-resize
2. Middle column scrollable
3. Full-panel listening animation for trigger nodes
4. In-place Listen/Stop button swap
5. General chrome polish (badges, groups, chips, column labels)

---

## 1. Fixed Height & Drag-to-Resize

### Default Height

`.ndv-modal` changes from `max-height: calc(100vh - 48px)` (content-driven) to a fixed `height: 65vh`. This gives the middle column a bounded height so its `overflow: auto` actually activates.

### Drag Handle

A 14px drag bar lives at the bottom of `.ndv-modal`, inside `.ndv-inner`, full width. It contains five dots (`●●●●●`) centered horizontally.

- **CSS:** `cursor: ns-resize`, subtle purple gradient (`linear-gradient(90deg, transparent, rgba(124,58,237,0.18), transparent)`), `border-top: 1px solid rgba(124,58,237,0.15)`
- **Hover state:** gradient and border lighten; dots turn lavender (`rgba(196,181,253,0.7)`)
- **Drag behavior:** `mousedown` on the handle enters resize mode; `mousemove` on `document` updates modal height clamped to `[35vh, 92vh]`; `mouseup` exits resize mode
- **Double-click:** snaps height back to 65vh with `transition: height 220ms ease`

### Hover Tooltip

On handle hover only, a floating tooltip appears above the handle: `"double-click to reset"`. Tooltip fades in with `opacity + translateY` transition (160ms). Never shown as permanent text — dots are always visible, text only on hover.

**Implementation:** tooltip div is a child of the drag handle, positioned `bottom: calc(100% + 7px)`, centered, with a CSS triangle arrow pointing down.

---

## 2. Middle Column Scroll

No code change needed to the params body — `.ndv-middle-body` already has `overflow: auto`. The fixed modal height (§1) is sufficient to activate it. A subtle scrollbar style is applied: `scrollbar-width: thin; scrollbar-color: rgba(124,58,237,0.3) transparent`.

---

## 3. Listening Animation (Trigger Left Panel)

When a trigger node is in "listening" state, the entire left column (`trigger-col` / `WebhookPanel`) shows an ambient glow animation.

### Layer Architecture

Three z-index layers, all inside `.trigger-col` (`position: relative`):

| Layer | z-index | Element | pointer-events |
|-------|---------|---------|----------------|
| Animation | 0 | `.anim-layer` (position: absolute, inset: 0) | none |
| Content | 1 | `.trigger-urls`, `.trigger-center` (position: relative) | auto |
| Column header | 2 | `.col-head` (position: relative) | auto |

**Critical:** the animation layer uses `pointer-events: none` so it never blocks clicks. All real interactive content stays in normal flow at z-index 1+.

### Animation Elements (inside `.anim-layer`)

**Breathing blob:**
```css
position: absolute; left: 50%; top: 70%; transform: translate(-50%, -50%);
width: 260px; height: 200px; border-radius: 50%;
background: radial-gradient(ellipse, rgba(124,58,237,0.24) 0%, transparent 68%);
animation: breathe 3s ease-in-out infinite;
```
`@keyframes breathe`: scale 0.85→1.16, opacity 0.5→1.

**Floating particles (8 total):**
Small circles (2–3px), `background: rgba(167,139,250,0.9)`, glow box-shadow. Each at a different `left` position (7%–91%), varying durations (2.6s–3.9s), varying delays. `@keyframes rise`: rise from below the panel to above it, fade in then out.

**Central orb (in `.trigger-center`):**
```
Outer: 56×56px div → orb-ring (border animation) + orb-bg (radial glow blob)
Inner: 22×22px div → radial gradient sphere, strong purple box-shadow, float animation
```
- `orb-ring`: `border: 1px solid rgba(124,58,237,0.3)`, scales 0.88→1.12
- `orb-core`: `background: radial-gradient(circle at 35% 35%, #c4b5fd, #7c3aed)`, `box-shadow: 0 0 18px 5px rgba(124,58,237,0.65), 0 0 36px 12px rgba(124,58,237,0.25)`
- `@keyframes float`: translateY 0 → -4px → 0 over 3s

### Trigger Center Layout

`.trigger-center` is a flex column (align: center, justify: center, gap: 12px), `flex: 1`, takes remaining space below the URL cards. Contains:

1. Orb (56×56, flex-shrink: 0)
2. `"Listening for a test event…"` label (color: #a78bfa, subtle pulse animation)
3. Stop button (full width, see §4)

### Ghosted URL Cards During Listening

The `.trigger-urls` section (webhook URL cards) remains visible but `opacity: 0.28` during listening state, to indicate context without competing with the animation.

---

## 4. Listen / Stop Button In-Place Swap

The "Listen for test event" button and the "Stop listening" button occupy the **same slot** in `.trigger-center`. When listening starts, the Listen button is replaced by the Stop button — no layout shift, same width, same position.

**Stop button appearance:**
```css
border: 1.5px solid rgba(240,116,122,0.55);
background: rgba(240,116,122,0.13);
color: #fca5a5;
animation: stop-glow 2.5s ease-in-out infinite; /* pulsing red shadow */
```
Contains a square icon (8×8px, `border-radius: 2px`, red glow, blink animation) + "Stop listening" text.

**Listen button appearance** (when not listening):
```css
border: 1px solid rgba(124,58,237,0.3);
background: rgba(124,58,237,0.1);
color: #a78bfa;
```
Icon: ▶ play triangle.

**State managed by:** existing `isListening` / `listeningForWebhook` state in `WebhookPanel`.

---

## 5. Header Listening Indicator

When listening is active, the header run button is replaced with a "Listening…" pill:

```
● Listening…
```

The dot blinks (opacity 1→0.3 over 1s). The pill has `background: rgba(124,58,237,0.12)`, `border: 1px solid rgba(124,58,237,0.3)`, `color: #a78bfa`, `border-radius: 20px`, `padding: 3px 10px`.

This is rendered in the NDV header actions area (`.ndv-actions`), replacing the run button while `isListening` is true.

---

## 6. General Chrome Polish

### Column Direction Badges

Each column header gains a small direction badge next to the column label:

- Left column (Trigger/Input): `IN` badge
- Right column (Output): `OUT` badge

Style: `font-size: 8.5px; font-weight: 700; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.07); border-radius: 4px; padding: 1px 5px;`

### Output Column Toolbar

The right column header (`col-head`) gains an inline toolbar:

```
[JSON] [Table] [Copy]
```

These are small toggle/action buttons (10px font, 5-button border radius). JSON/Table are radio-style (active state: `background: rgba(124,58,237,0.14); border-color: rgba(124,58,237,0.28); color: #c4b5fd`). Copy is a plain ghost button.

### Test / Prod URL Badges

Each webhook URL card gains a badge before the URL text:

- **Test:** `background: rgba(251,191,36,0.12); border: 1px solid rgba(251,191,36,0.22); color: #fbbf24;` text: "Test"
- **Prod:** `background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.22); color: #4ade80;` text: "Prod"

### Option Groups in Bordered Cards

Parameter groups (e.g., Authentication, Response) are wrapped in a bordered card:

```css
border: 1px solid rgba(255,255,255,0.07);
border-radius: 10px;
overflow: hidden;
```

Group header: `background: rgba(255,255,255,0.03); border-bottom: 1px solid rgba(255,255,255,0.05);`, uppercase label + remove (×) button.

### Dashed "Add Option" Chips

Add-option chips use a dashed border instead of solid:

```css
border: 1px dashed rgba(124,58,237,0.35);
color: #a78bfa;
background: rgba(124,58,237,0.07);
border-radius: 20px;
```

### Delete Button Red Tint

The Delete button in the header action bar gets:

```css
background: rgba(240,116,122,0.07);
border-color: rgba(240,116,122,0.2);
color: #f39a9e;
```

---

## Files to Change

| File | Change |
|------|--------|
| `apps/web/src/editor.css` | `.ndv-modal` height, drag handle styles, listening animation CSS, stop button CSS, badge styles, group card styles, chip dashed border, output toolbar styles |
| `apps/web/src/editor/NodeDetailModal.tsx` | Add drag handle element + resize JS logic, render listening pill in header |
| `apps/web/src/editor/NDVPanels.tsx` | `WebhookPanel`: animation layer, orb, stop button swap, URL card badges; Output column header toolbar; IN/OUT badges |

---

## State

No new state is needed. The existing `isListening` / `listeningForWebhook` boolean already drives the Listen button. The drag handle height is local component state (`useState<number>(65)` stored as vh value on `NodeDetailModal`).

---

## Out of Scope

- Saving/persisting the user's preferred panel height across sessions
- Any changes to the Parameters tab content (field types, validation, etc.)
- Animations on non-trigger nodes
