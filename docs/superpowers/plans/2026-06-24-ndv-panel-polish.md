# NDV Panel Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the NDV modal with a fixed viewport-relative height, drag-to-resize handle, full-panel listening animation for the webhook trigger, an in-place Listen/Stop button swap, a listening pill in the header, and general chrome improvements (URL badges, IN/OUT labels, group card borders, dashed chips, red delete button).

**Architecture:** All changes are frontend-only. CSS carries layout + animations; React components carry state (resize height, listening propagation). The listening state is threaded upward via an optional `onListeningChange` callback prop through WebhookPanel → NDVPanels → NodeDetailModal so the header pill can render without touching the Zustand store.

**Tech Stack:** React 18, TypeScript, vanilla CSS (editor.css), Zustand (not modified — state stays in component tree)

## Global Constraints

- Only modify files listed per task — do not touch test files or unrelated components
- `editor.css` polish overrides live at the bottom of the file (after line 7186) in the existing "NDV polish" comment block — append new rules there
- Follow existing class naming convention: `ndv-*` prefix for NDV-specific, kebab-case
- No new npm dependencies
- No Zustand store changes
- All animations must use `pointer-events: none` on their containing layer
- All resize logic must clean up `document` event listeners on unmount

---

## File Map

| File | Role |
|------|------|
| `apps/web/src/editor.css` | All new CSS (height, drag handle, animation keyframes, stop button, badges, groups, chips, pill) |
| `apps/web/src/editor/NodeDetailModal.tsx` | Drag handle element + resize state + JS logic + listening pill in header |
| `apps/web/src/editor/NDVPanels.tsx` | Thread `onListeningChange` prop to WebhookPanel; add IN badge to webhook panel head |
| `apps/web/src/editor/NodeDetails.tsx` | Restructure WebhookPanel: animation layer, orb, stop button, URL card badges, `onListeningChange` prop |
| `apps/web/src/editor/DataPanel.tsx` | Add OUT/IN text badge to panel header (alongside existing direction icon) |

---

### Task 1: Fixed height + middle-column scroll

The modal currently uses `max-height` (content-driven). Switching to a fixed `height` activates the `overflow: auto` already on `.ndv-middle-body`.

**Files:**
- Modify: `apps/web/src/editor.css` (polish block, around line 7186)

**Interfaces:**
- Produces: nothing consumed by other tasks — standalone visual change

- [ ] **Step 1: Change `.ndv-modal` height in the polish block**

In `editor.css`, find the polish override for `.ndv-modal` that starts at ~line 7186. Add `height: 65vh` and remove or override the inherited `max-height`. The base rule at line 3834 sets `max-height: calc(100vh - 48px)` — override it by adding `height` and `max-height` to the polish block:

```css
/* ---- modal frame: softer radius, accent hairline, deeper layered shadow --- */
.ndv-modal {
  position: relative;
  border-radius: 14px;
  border-color: color-mix(in srgb, var(--accent) 9%, var(--line));
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--accent) 4%, var(--surface)) 0%,
    var(--surface) 120px
  );
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.05) inset,
    0 40px 90px -36px rgba(0, 0, 0, 0.86),
    0 0 0 1px rgba(0, 0, 0, 0.35);
  height: 65vh;          /* ← new: fixed default height */
  max-height: 92vh;      /* ← new: cap for drag-resize; overrides base max-height */
  min-height: 35vh;      /* ← new: floor for drag-resize */
}
```

- [ ] **Step 2: Verify scroll activates**

Open the app, open any node's NDV with many parameters. The modal should be a fixed height and the middle column should scroll when parameters overflow. Nothing else should change.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/editor.css
git commit -m "style(ndv): fixed 65vh default height — activates middle column scroll"
```

---

### Task 2: Drag-to-resize handle

A thin drag bar at the bottom of the modal. Dragging changes height (35vh–92vh). Double-click resets to 65vh with a smooth transition.

**Files:**
- Modify: `apps/web/src/editor/NodeDetailModal.tsx`
- Modify: `apps/web/src/editor.css`

**Interfaces:**
- Produces: `.ndv-drag-handle` DOM element; `ndvHeight` state on NodeDetailModal (consumed internally only)

- [ ] **Step 1: Add `ndvHeight` state and inline style to NodeDetailModal**

In `apps/web/src/editor/NodeDetailModal.tsx`, add state and a ref for the drag gesture. Edit the imports to add `useCallback`, then add state just before the existing `[editingName, ...]` line:

```tsx
// After the existing imports and before the component function:
// (no new imports needed — useRef and useState are already imported)

export function NodeDetailModal({ nodeId }: { nodeId: string }) {
  // ... existing lines up to the refs ...
  const [ndvHeight, setNdvHeight] = useState(65); // vh units
  const [resizing, setResizing] = useState(false);
  const dragStartY = useRef(0);
  const dragStartH = useRef(0);
```

- [ ] **Step 2: Add drag handler functions**

Add these functions inside `NodeDetailModal`, after the existing `flashNameSaved` / `saveName` functions:

```tsx
function startResize(e: React.MouseEvent): void {
  e.preventDefault();
  dragStartY.current = e.clientY;
  dragStartH.current = ndvHeight;
  setResizing(true);

  function onMove(mv: MouseEvent): void {
    const deltaVh = ((mv.clientY - dragStartY.current) / window.innerHeight) * 100;
    setNdvHeight(Math.min(92, Math.max(35, dragStartH.current + deltaVh)));
  }
  function onUp(): void {
    setResizing(false);
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  }
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function resetHeight(): void {
  setNdvHeight(65);
}
```

- [ ] **Step 3: Apply inline height style and add drag handle element**

Modify the modal div to apply the height and add the drag handle at the bottom. Change this:

```tsx
      <div
        className={`ndv-modal${...}`}
        ref={modalRef}
        ...
      >
        <header className="ndv-head">
          ...
        </header>
        <div className="ndv-body">
          <NDVPanels nodeId={nodeId} />
        </div>
      </div>
```

To this:

```tsx
      <div
        className={`ndv-modal${resizing ? " ndv-resizing" : ""}${
          runStatus === "success"
            ? " ndv-edge-ok"
            : runStatus === "error"
              ? " ndv-edge-error"
              : runStatus === "running"
                ? " ndv-edge-running"
                : ""
        }`}
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ndv-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        style={{ height: `${ndvHeight}vh` }}
      >
        <header className="ndv-head">
          ...
        </header>
        <div className="ndv-body">
          <NDVPanels nodeId={nodeId} />
        </div>
        <div
          className="ndv-drag-handle"
          onMouseDown={startResize}
          onDoubleClick={resetHeight}
          role="separator"
          aria-label="Drag to resize panel, double-click to reset"
        >
          <span className="ndv-drag-dots" aria-hidden="true">
            <span /><span /><span /><span /><span />
          </span>
          <span className="ndv-drag-tip" aria-hidden="true">double-click to reset</span>
        </div>
      </div>
```

- [ ] **Step 4: Add CSS for drag handle**

Append to the NDV polish block in `editor.css`:

```css
/* ---- drag-to-resize handle ---- */
.ndv-drag-handle {
  height: 14px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: ns-resize;
  position: relative;
  background: linear-gradient(
    90deg,
    transparent,
    color-mix(in srgb, var(--accent) 18%, transparent),
    transparent
  );
  border-top: 1px solid color-mix(in srgb, var(--accent) 15%, var(--line-2));
  border-radius: 0 0 14px 14px;
  transition: background 180ms, border-color 180ms;
  user-select: none;
}
.ndv-drag-handle:hover {
  background: linear-gradient(
    90deg,
    transparent,
    color-mix(in srgb, var(--accent) 32%, transparent),
    transparent
  );
  border-top-color: color-mix(in srgb, var(--accent) 38%, var(--line-2));
}
.ndv-drag-dots {
  display: flex;
  gap: 3px;
}
.ndv-drag-dots span {
  display: block;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--accent) 40%, transparent);
  transition: background 180ms;
}
.ndv-drag-handle:hover .ndv-drag-dots span {
  background: color-mix(in srgb, var(--accent-2) 70%, transparent);
}
.ndv-drag-tip {
  position: absolute;
  bottom: calc(100% + 7px);
  left: 50%;
  transform: translateX(-50%) translateY(4px);
  background: var(--surface-2);
  border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
  border-radius: 7px;
  padding: 4px 10px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--accent-2);
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 160ms, transform 160ms;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
}
.ndv-drag-tip::after {
  content: "";
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent;
  border-top-color: color-mix(in srgb, var(--accent) 35%, var(--line));
}
.ndv-drag-handle:hover .ndv-drag-tip {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}
/* suppress tooltip during active drag */
.ndv-resizing .ndv-drag-tip {
  opacity: 0 !important;
}
/* smooth snap-back on double-click reset */
.ndv-modal:not(.ndv-resizing) {
  transition: height 220ms cubic-bezier(0.25, 0.8, 0.25, 1);
}
```

- [ ] **Step 5: Verify resize behavior**

Open any NDV. Grab the bottom bar and drag up/down — the panel should resize between 35vh and 92vh. Double-click the bar — it should snap back to 65vh smoothly. The tooltip should appear on hover but disappear while dragging.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/NodeDetailModal.tsx apps/web/src/editor.css
git commit -m "feat(ndv): drag-to-resize handle with double-click reset"
```

---

### Task 3: WebhookPanel ambient glow animation

Restructure the WebhookPanel component to show a full-panel ambient glow with floating particles and a central orb while listening. Uses a z-index layering strategy: animation layer at z-index 0 (pointer-events: none), interactive content at z-index 1.

**Files:**
- Modify: `apps/web/src/editor/NodeDetails.tsx`
- Modify: `apps/web/src/editor.css`

**Interfaces:**
- Produces: `onListeningChange?: (listening: boolean) => void` prop on `WebhookPanel` (consumed by Task 4)
- Produces: `.ndv-webhook-panel` has new internal structure (consumed by Task 5 for badge placement)

- [ ] **Step 1: Add CSS keyframes and layer styles**

Append to the NDV polish block in `editor.css`:

```css
/* ---- WebhookPanel listening animation ---- */
.ndv-panel.ndv-webhook-panel {
  position: relative;
  overflow: hidden;
  background: var(--bg);
  display: flex;
  flex-direction: column;
}

/* animation layer — absolute, zero z, never blocks clicks */
.ndv-webhook-anim {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
}
.ndv-webhook-anim-blob {
  position: absolute;
  left: 50%;
  top: 70%;
  transform: translate(-50%, -50%);
  width: 260px;
  height: 200px;
  border-radius: 50%;
  background: radial-gradient(
    ellipse,
    color-mix(in srgb, var(--accent) 24%, transparent) 0%,
    transparent 68%
  );
  animation: ndv-breathe 3s ease-in-out infinite;
}
@keyframes ndv-breathe {
  0%, 100% { opacity: 0.5; transform: translate(-50%, -50%) scale(0.85); }
  50%       { opacity: 1;   transform: translate(-50%, -50%) scale(1.16); }
}
.ndv-webhook-particle {
  position: absolute;
  border-radius: 50%;
  background: var(--accent-2);
  box-shadow: 0 0 6px 2px color-mix(in srgb, var(--accent) 50%, transparent);
  animation: ndv-rise linear infinite;
  opacity: 0;
}
@keyframes ndv-rise {
  0%   { bottom: -8px;  opacity: 0; }
  15%  { opacity: 0.9; }
  80%  { opacity: 0.35; }
  100% { bottom: 108%; opacity: 0; transform: translateX(5px); }
}

/* URL cards — z-index 1, ghosted when listening */
.ndv-webhook-urls {
  position: relative;
  z-index: 1;
  padding: 12px 14px 8px;
  flex-shrink: 0;
  transition: opacity 300ms ease;
}
.ndv-webhook-urls.ndv-webhook-urls--listening {
  opacity: 0.28;
}
.ndv-webhook-url-row {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 5px;
}
.ndv-url-badge {
  font-size: 7.5px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 4px;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}
.ndv-url-badge--test {
  background: rgba(251, 191, 36, 0.12);
  border: 1px solid rgba(251, 191, 36, 0.22);
  color: #fbbf24;
}
.ndv-url-badge--prod {
  background: rgba(34, 197, 94, 0.1);
  border: 1px solid rgba(34, 197, 94, 0.22);
  color: #4ade80;
}

/* center stage: orb + label + button — z-index 1, fills remaining height */
.ndv-webhook-center {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 8px 16px 16px;
  min-height: 0;
}

/* orb */
.ndv-webhook-orb-wrap {
  position: relative;
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.ndv-webhook-orb-bg {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: radial-gradient(circle, color-mix(in srgb, var(--accent) 40%, transparent) 0%, transparent 70%);
  animation: ndv-breathe 3s ease-in-out infinite;
}
.ndv-webhook-orb-ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  animation: ndv-orb-ring 3s ease-in-out infinite;
}
@keyframes ndv-orb-ring {
  0%, 100% { transform: scale(0.88); border-color: color-mix(in srgb, var(--accent) 18%, transparent); }
  50%       { transform: scale(1.12); border-color: color-mix(in srgb, var(--accent) 50%, transparent); }
}
.ndv-webhook-orb-core {
  position: relative;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, var(--accent-2), var(--accent));
  box-shadow:
    0 0 18px 5px color-mix(in srgb, var(--accent) 65%, transparent),
    0 0 36px 12px color-mix(in srgb, var(--accent) 25%, transparent);
  animation: ndv-orb-float 3s ease-in-out infinite;
}
@keyframes ndv-orb-float {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(-4px); }
}

/* listening label */
.ndv-webhook-listen-label {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--accent-2);
  text-align: center;
  flex-shrink: 0;
  animation: ndv-breathe 3s ease-in-out infinite;
}

/* stop button */
.ndv-webhook-stop {
  width: 100%;
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 650;
  padding: 9px 14px;
  border-radius: 9px;
  border: 1.5px solid rgba(240, 116, 122, 0.55);
  background: rgba(240, 116, 122, 0.13);
  color: #fca5a5;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  animation: ndv-stop-glow 2.5s ease-in-out infinite;
}
@keyframes ndv-stop-glow {
  0%, 100% { box-shadow: none; }
  50%       { box-shadow: 0 0 20px -4px rgba(240, 116, 122, 0.5); }
}
.ndv-webhook-stop-sq {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  background: #f87171;
  flex-shrink: 0;
  box-shadow: 0 0 8px 2px rgba(248, 113, 113, 0.7);
  animation: ndv-blink 1s ease-in-out infinite;
}
@keyframes ndv-blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.3; }
}

/* listen button (default state) */
.ndv-webhook-listen {
  width: 100%;
  flex-shrink: 0;
}
```

- [ ] **Step 2: Restructure WebhookPanel JSX**

In `apps/web/src/editor/NodeDetails.tsx`, find the `WebhookPanel` function (line 3665) and replace its props signature and return statement:

Replace the current props:
```tsx
export function WebhookPanel({
  path,
  nodeId,
}: {
  path: string;
  nodeId?: string;
}) {
```

With:
```tsx
export function WebhookPanel({
  path,
  nodeId,
  onListeningChange,
}: {
  path: string;
  nodeId?: string;
  onListeningChange?: (listening: boolean) => void;
}) {
```

- [ ] **Step 3: Update listen/stop to call the callback**

In the same `WebhookPanel`, update `stop` and `listen` to call `onListeningChange`:

```tsx
function stop(): void {
  if (timerRef.current !== null) {
    window.clearInterval(timerRef.current);
    timerRef.current = null;
  }
  void api.stopListen(slug).catch(() => undefined);
  setListening(false);
  onListeningChange?.(false);
}
```

And in `listen()`, after `setListening(true)`:
```tsx
setListening(true);
onListeningChange?.(true);
```

- [ ] **Step 4: Replace the return JSX**

Replace the entire `return (...)` of `WebhookPanel` (lines 3724–3762) with:

```tsx
  const origin = window.location.origin;
  const testUrl = `${origin}/api/webhook-test/${slug}`;
  const prodUrl = `${origin}/api/webhook/${slug}`;

  return (
    <div className="ndv-panel ndv-webhook-panel" style={{ position: "relative", display: "flex", flexDirection: "column", flex: 1, overflow: "hidden" }}>
      {/* z-index 0: animation layer — never blocks clicks */}
      {listening && (
        <div className="ndv-webhook-anim" aria-hidden="true">
          <div className="ndv-webhook-anim-blob" />
          {[
            { left: "13%", dur: "3.2s", delay: "0s",   size: 3 },
            { left: "30%", dur: "2.6s", delay: "0.7s", size: 2 },
            { left: "50%", dur: "3.9s", delay: "0.2s", size: 3 },
            { left: "65%", dur: "2.8s", delay: "1.3s", size: 2 },
            { left: "79%", dur: "3.4s", delay: "0.5s", size: 3 },
            { left: "7%",  dur: "2.7s", delay: "1.9s", size: 2 },
            { left: "43%", dur: "3.1s", delay: "2.2s", size: 2 },
            { left: "91%", dur: "3.6s", delay: "1.0s", size: 3 },
          ].map((p, i) => (
            <div
              key={i}
              className="ndv-webhook-particle"
              style={{
                left: p.left,
                width: p.size,
                height: p.size,
                animationDuration: p.dur,
                animationDelay: p.delay,
              }}
            />
          ))}
        </div>
      )}

      {/* z-index 1: ghosted URL cards */}
      <div className={`ndv-webhook-urls${listening ? " ndv-webhook-urls--listening" : ""}`}>
        <div className="inspector-section-head">Webhook URLs</div>
        <p className="field-desc">Test URL — captures requests while you build.</p>
        <div className="ndv-webhook-url-row">
          <span className="ndv-url-badge ndv-url-badge--test">Test</span>
          <UrlRow url={testUrl} />
        </div>
        <p className="field-desc">Production URL — runs this workflow when it is active.</p>
        <div className="ndv-webhook-url-row">
          <span className="ndv-url-badge ndv-url-badge--prod">Prod</span>
          <UrlRow url={prodUrl} />
        </div>
      </div>

      {/* z-index 1: center stage — orb + label + button */}
      <div className="ndv-webhook-center">
        {listening ? (
          <>
            <div className="ndv-webhook-orb-wrap">
              <div className="ndv-webhook-orb-bg" />
              <div className="ndv-webhook-orb-ring" />
              <div className="ndv-webhook-orb-core" />
            </div>
            <p className="ndv-webhook-listen-label">Listening for a test event…</p>
            <button type="button" className="ndv-webhook-stop" onClick={stop}>
              <span className="ndv-webhook-stop-sq" />
              Stop listening
            </button>
          </>
        ) : (
          <button type="button" className="ndv-webhook-listen btn btn-sm" onClick={() => void listen()}>
            ▶ Listen for test event
          </button>
        )}
        {received && !listening && (
          <span className="muted" style={{ marginTop: 8 }}>Request captured — see Output panel.</span>
        )}
        {error && <p className="error-text">{error}</p>}
      </div>
    </div>
  );
```

- [ ] **Step 5: Verify animation renders**

Open the webhook trigger NDV. Click "Listen for test event". You should see:
- Particles rising through the panel
- Breathing purple blob in the lower half
- Central glowing orb with a floating animation
- "Listening for a test event…" label
- Red "Stop listening" button below the orb
- URL cards ghosted to ~28% opacity
- Clicking Stop restores the original layout

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/NodeDetails.tsx apps/web/src/editor.css
git commit -m "feat(ndv): webhook panel ambient glow listening animation"
```

---

### Task 4: Listening pill in NDV header

Thread the `listening` boolean from WebhookPanel up through NDVPanels to NodeDetailModal so the header shows a pulsing "Listening…" pill while listening is active.

**Files:**
- Modify: `apps/web/src/editor/NDVPanels.tsx`
- Modify: `apps/web/src/editor/NodeDetailModal.tsx`
- Modify: `apps/web/src/editor.css`

**Interfaces:**
- Consumes: `onListeningChange` prop on `WebhookPanel` (from Task 3)
- Produces: `.ndv-listening-pill` in header DOM

- [ ] **Step 1: Add `onListeningChange` prop to NDVPanels**

In `NDVPanels.tsx`, change:

```tsx
export function NDVPanels({ nodeId }: { nodeId: string }) {
```

To:

```tsx
export function NDVPanels({
  nodeId,
  onListeningChange,
}: {
  nodeId: string;
  onListeningChange?: (listening: boolean) => void;
}) {
```

- [ ] **Step 2: Forward the prop to WebhookPanel in NDVPanels**

Find the `<WebhookPanel` usage inside `NDVPanels` (around line 1079):

```tsx
<WebhookPanel
  path={String(node.data.params.path ?? "noodle")}
  nodeId={nodeId}
/>
```

Change it to:

```tsx
<WebhookPanel
  path={String(node.data.params.path ?? "noodle")}
  nodeId={nodeId}
  onListeningChange={onListeningChange}
/>
```

- [ ] **Step 3: Add `isWebhookListening` state to NodeDetailModal**

In `NodeDetailModal.tsx`, add state after the existing `const [nameSaved, setNameSaved] = useState(false);` line:

```tsx
const [isWebhookListening, setIsWebhookListening] = useState(false);
```

- [ ] **Step 4: Pass callback to NDVPanels in NodeDetailModal**

Find `<NDVPanels nodeId={nodeId} />` and change to:

```tsx
<NDVPanels
  nodeId={nodeId}
  onListeningChange={isWebhook ? setIsWebhookListening : undefined}
/>
```

- [ ] **Step 5: Render listening pill in header**

In the `ndv-actions` div in NodeDetailModal, find the run button block and conditionally replace it with the listening pill when `isWebhookListening`:

```tsx
<div className="ndv-actions">
  {isWebhook && isWebhookListening ? (
    <div className="ndv-listening-pill" aria-live="polite">
      <span className="ndv-listening-dot" aria-hidden="true" />
      Listening…
    </div>
  ) : (
    <button
      className="btn btn-sm btn-run"
      onClick={() => runFromNode(currentNode.id)}
      disabled={!canRunStep}
      title={
        !canRunStep
          ? "Connect a trigger upstream to run this node"
          : isWebhook
            ? "Listen for a test event"
            : "Execute this step using current upstream data"
      }
    >
      {isWebhook ? "▶ Listen for event" : "▶ Execute step"}
    </button>
  )}
  {!isWebhook && (
    <button
      className="btn btn-sm btn-ghost"
      onClick={() => runFromNode(currentNode.id, { reuseUpstream: false })}
      disabled={!canRunStep}
      title={
        !canRunStep
          ? "Connect a trigger upstream to run this node"
          : "Execute this step after recomputing upstream nodes"
      }
    >
      ↻ Run fresh
    </button>
  )}
  <span className="ndv-action-sep" aria-hidden="true" />
  <button
    className="btn btn-sm"
    onClick={() => toggleDisabled(currentNode.id)}
  >
    {disabled ? "Enable" : "Disable"}
  </button>
  <button
    className="btn btn-sm btn-ghost"
    onClick={() => {
      deleteNode(currentNode.id);
      closeNdv();
    }}
  >
    Delete
  </button>
  <button
    className="ndv-close"
    aria-label="Close"
    onClick={closeNdv}
  >
    ×
  </button>
</div>
```

- [ ] **Step 6: Add CSS for listening pill**

Append to the NDV polish block in `editor.css`:

```css
/* ---- header listening pill ---- */
.ndv-listening-pill {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--accent-2);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  border-radius: 20px;
  padding: 4px 11px;
  animation: ndv-breathe 2.5s ease-in-out infinite;
}
.ndv-listening-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent-2);
  flex-shrink: 0;
  animation: ndv-blink 1s ease-in-out infinite;
}
```

- [ ] **Step 7: Verify**

Open webhook trigger NDV. Click "Listen for test event" in the left panel. The header's run button should swap for the "● Listening…" pill with a blinking dot and breathing animation. Clicking Stop in the left panel should restore the run button.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/editor/NDVPanels.tsx apps/web/src/editor/NodeDetailModal.tsx apps/web/src/editor.css
git commit -m "feat(ndv): listening pill in header synced to webhook panel state"
```

---

### Task 5: Chrome polish

IN/OUT direction text badges on all column headers, bordered group cards, dashed add-option chips, red-tinted Delete button.

**Files:**
- Modify: `apps/web/src/editor/NDVPanels.tsx` (IN badge on webhook head)
- Modify: `apps/web/src/editor/DataPanel.tsx` (IN/OUT text badge)
- Modify: `apps/web/src/editor/NodeDetailModal.tsx` (Delete button class)
- Modify: `apps/web/src/editor.css`

**Interfaces:**
- Produces: visual-only changes; no new props or exports

- [ ] **Step 1: Add IN badge to webhook panel head in NDVPanels**

In `NDVPanels.tsx`, find the webhook section header:
```tsx
<header className="ndv-panel-head">
  <h3>Trigger</h3>
</header>
```

Change to:
```tsx
<header className="ndv-panel-head">
  <h3>Trigger</h3>
  <span className="ndv-dir-badge">IN</span>
</header>
```

- [ ] **Step 2: Add IN/OUT text badge to DataPanel**

In `DataPanel.tsx`, find the `<h3>` in the panel header (around line 1434):

```tsx
<h3>
  <DirIcon
    className="ndv-panel-dir"
    size={13}
    weight="bold"
    aria-hidden="true"
  />
  {title}
  ...
</h3>
```

Add a text badge after the icon, before `{title}`:

```tsx
<h3>
  <DirIcon
    className="ndv-panel-dir"
    size={13}
    weight="bold"
    aria-hidden="true"
  />
  {title}
  <span className="ndv-dir-badge">{isInput ? "IN" : "OUT"}</span>
  {!empty && itemCount !== null && (
    <span className="ndv-count-badge">
      {itemCount} {itemCount === 1 ? "item" : "items"}
    </span>
  )}
  {typeof durationMs === "number" && (
    <span className="ndv-duration" title="Execution time">
      {durationMs} ms
    </span>
  )}
</h3>
```

- [ ] **Step 3: Red-tint the Delete button in NodeDetailModal**

In `NodeDetailModal.tsx`, find the Delete button:

```tsx
<button
  className="btn btn-sm btn-ghost"
  onClick={() => {
    deleteNode(currentNode.id);
    closeNdv();
  }}
>
  Delete
</button>
```

Change `btn-ghost` to `btn-danger-soft`:

```tsx
<button
  className="btn btn-sm btn-danger-soft"
  onClick={() => {
    deleteNode(currentNode.id);
    closeNdv();
  }}
>
  Delete
</button>
```

- [ ] **Step 4: CSS for all polish items**

Append to the NDV polish block in `editor.css`:

```css
/* ---- IN / OUT direction badge ---- */
.ndv-dir-badge {
  font-size: 8.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--ink-3);
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 4px;
  padding: 1px 5px;
  flex-shrink: 0;
}

/* ---- bordered group cards ---- */
.ndv-group {
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 10px;
  overflow: hidden;
  margin-bottom: 12px;
}
.ndv-group-head {
  background: rgba(255, 255, 255, 0.03);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  padding: 7px 12px;
}

/* ---- dashed add-option chips ---- */
.ndv-add-chip {
  border-style: dashed;
  border-color: color-mix(in srgb, var(--accent) 35%, transparent);
  color: var(--accent-2);
  background: color-mix(in srgb, var(--accent) 7%, transparent);
}
.ndv-add-chip:hover {
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
  background: color-mix(in srgb, var(--accent) 13%, transparent);
}

/* ---- Delete button red tint ---- */
.btn-danger-soft {
  background: rgba(240, 116, 122, 0.07);
  border-color: rgba(240, 116, 122, 0.2);
  color: #f39a9e;
}
.btn-danger-soft:hover {
  background: rgba(240, 116, 122, 0.14);
  border-color: rgba(240, 116, 122, 0.35);
}
```

- [ ] **Step 5: Verify all polish items**

- Webhook NDV: left panel head shows "Trigger" + "IN" badge
- Input panel head (non-webhook): shows "Input" + "IN" badge
- Output panel head: shows "Output" + "OUT" badge
- Parameters tab: option groups (e.g. Authentication) have a visible card border with a subtle header background
- "Add option" chips have a dashed border
- Delete button in the header is red-tinted instead of ghost

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/editor/NDVPanels.tsx apps/web/src/editor/DataPanel.tsx apps/web/src/editor/NodeDetailModal.tsx apps/web/src/editor.css
git commit -m "style(ndv): IN/OUT badges, bordered groups, dashed chips, red delete button"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Fixed 65vh default height | Task 1 |
| Middle column scrollable | Task 1 (overflow already active) |
| Drag handle at bottom | Task 2 |
| Double-click drag handle → 65vh reset | Task 2 |
| Hover tooltip "double-click to reset" | Task 2 |
| Full panel ambient glow during listening | Task 3 |
| Animation layer z-index 0, pointer-events none | Task 3 |
| Orb + floating animation | Task 3 |
| "Listening…" label | Task 3 |
| Ghosted URL cards | Task 3 |
| Stop button same slot/width as Listen | Task 3 |
| `onListeningChange` prop | Task 3 |
| Header listening pill with blinking dot | Task 4 |
| Test/Prod URL badges (amber/green) | Task 3 |
| IN/OUT column direction badges | Task 5 |
| Bordered group cards | Task 5 |
| Dashed add-option chips | Task 5 |
| Delete button red-tinted | Task 5 |

**Output toolbar note:** DataPanel already renders JSON/Table/Schema/etc. view toggle buttons in the header via `.data-view-toggle`. The spec's "output toolbar in column header" is satisfied by the existing implementation — no changes needed.

**Type / name consistency:** `onListeningChange` used consistently in Task 3 (WebhookPanel), Task 4 (NDVPanels + NodeDetailModal). `ndv-dir-badge` class defined in Task 5 CSS and used in both Task 5 JSX changes.

**Placeholder scan:** No TBDs or vague steps. Every step includes the literal code to write.
