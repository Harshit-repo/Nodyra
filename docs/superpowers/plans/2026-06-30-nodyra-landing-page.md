# Nodyra Landing Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `brand/homepage/nodyra.html` — a self-contained static marketing landing page for Nodyra with an animated canvas hero using the exact node UI from the app, and a waitlist email capture CTA.

**Architecture:** Single HTML file, all CSS inlined in a `<style>` block, all JS in a `<script>` block at the bottom. CSS tokens and class names copied verbatim from `apps/web/src/index.css` and `apps/web/src/editor.css` so the node canvas looks identical to the real app. No build step, no dependencies, opens directly in a browser.

**Tech Stack:** HTML5, CSS (custom properties, grid, flexbox, SVG animations via `stroke-dashoffset`), vanilla JS (no frameworks).

## Global Constraints

- Output file: `brand/homepage/nodyra.html` — do not modify `brand/homepage/index.html`
- Node CSS classes must match the app exactly: `.node`, `.node-tile`, `.node-label`, `--cat` CSS variable for category colour
- All CSS tokens match `apps/web/src/index.css` verbatim (see Task 1 for full token list)
- Mark B SVG (`brand/icons/nodyra-mark-b.svg`) inlined — no external image references
- Fonts: Bricolage Grotesque + Hanken Grotesk + IBM Plex Mono from Google Fonts
- `prefers-reduced-motion` disables all animations
- No external JS, no analytics, no tracking scripts
- Mobile responsive down to 375px viewport
- ARIA landmarks: `<nav>`, `<main>`, `<footer>`. Decorative SVGs have `aria-hidden="true"`
- Waitlist form: JS intercepts submit, shows inline spinner → ✓ confirmation, no page reload

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `brand/homepage/nodyra.html` | **Create** | Entire landing page — HTML + inlined CSS + inlined JS |

---

### Task 1: HTML shell + CSS foundation

**Files:**
- Create: `brand/homepage/nodyra.html`

**Produces:** A dark page with correct fonts loading, body background `#090b10`, and a global radial blue glow — visually identical to the app's base layer. No content yet.

- [ ] **Step 1: Create the file**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nodyra — Your AI agent builds the workflow</title>
<meta name="description" content="Connect any LLM via MCP. It writes Python workflows and you see them as nodes on a canvas. Self-hostable, Python-native, no lock-in.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,700;12..96,800&family=Hanken+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
/* ── tokens (verbatim from apps/web/src/index.css) ── */
:root {
  --bg:#090b10; --surface:#10141c; --surface-2:#161b25; --surface-3:#1f2632;
  --canvas:#0b0e14; --line:#242c3a; --line-2:#1a2029;
  --ink:#e9eef6; --ink-2:#98a4ba; --ink-3:#8a93a6;
  --accent:#4c9eff; --accent-2:#79b6ff; --accent-deep:#1f4f8f;
  --accent-glow:rgba(76,158,255,0.18);
  --amber:#ffd479; --ok:#57c98a;
  --radius:11px; --radius-sm:7px;
  --shadow:0 18px 44px -18px rgba(0,0,0,0.78);
  --font-display:"Bricolage Grotesque",system-ui,sans-serif;
  --font-sans:"Hanken Grotesk",system-ui,sans-serif;
  --font-mono:"IBM Plex Mono",ui-monospace,monospace;
}

*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  font-family:var(--font-sans);
  font-size:14px;
  background:var(--bg);
  color:var(--ink);
  -webkit-font-smoothing:antialiased;
  line-height:1.55;
  overflow-x:hidden;
}
/* global blue radial glow — matches the app */
body::before{
  content:"";
  position:fixed;inset:0;z-index:0;pointer-events:none;
  background:
    radial-gradient(900px 520px at 12% -8%, rgba(76,158,255,0.09), transparent 60%),
    radial-gradient(760px 620px at 102% 108%, rgba(76,158,255,0.06), transparent 55%);
}
.layer{position:relative;z-index:1}
a{color:inherit;text-decoration:none}
h1,h2,h3{margin:0;letter-spacing:-0.02em}
.wrap{max-width:1140px;margin:0 auto;padding:0 32px}

/* ── buttons (verbatim from apps/web/src/index.css) ── */
.btn{
  font-size:13px;font-weight:600;
  padding:8px 15px;border-radius:var(--radius-sm);
  border:1px solid var(--line);background:var(--surface-2);color:var(--ink);
  display:inline-flex;align-items:center;gap:7px;cursor:pointer;
  transition:background 120ms ease,border-color 120ms ease,transform 80ms ease;
}
.btn:hover{background:var(--surface-3);border-color:var(--accent-deep)}
.btn:active{transform:translateY(1px)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.btn-primary{
  font-weight:700;
  background:linear-gradient(180deg,var(--accent-2),var(--accent));
  color:#fff;border-color:var(--accent-deep);
  box-shadow:0 1px 0 rgba(255,255,255,0.22) inset,0 6px 16px -8px rgba(76,158,255,0.55);
}
.btn-primary:hover{background:linear-gradient(180deg,#93c4ff,var(--accent-2));border-color:var(--accent-deep)}
.btn-ghost{background:transparent;border-color:transparent}
.btn-ghost:hover{background:var(--surface-2);border-color:transparent}

/* ── field input (verbatim from apps/web/src/index.css) ── */
.field-input{
  width:100%;font-family:var(--font-sans);font-size:13px;
  color:var(--ink);background:var(--bg);
  border:1px solid var(--line);border-radius:var(--radius-sm);
  padding:8px 10px;outline:none;
  transition:border-color 120ms ease,box-shadow 120ms ease;
}
.field-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow)}
.field-input::placeholder{color:var(--ink-3)}
</style>
</head>
<body>
<nav id="site-nav" aria-label="Main navigation"></nav>
<main>
  <section id="hero" class="layer"></section>
  <section id="how" class="layer"></section>
  <section id="why" class="layer"></section>
  <section id="platform" class="layer"></section>
  <section id="cta" class="layer"></section>
</main>
<footer id="site-footer" class="layer"></footer>
<script>
// JS goes here in later tasks
</script>
</body>
</html>
```

- [ ] **Step 2: Open in browser and verify**

Open `brand/homepage/nodyra.html` in a browser (double-click or `start brand\homepage\nodyra.html`).

Expected: Near-black background (`#090b10`), subtle blue radial glow in top-left and bottom-right corners, no content yet, no console errors.

- [ ] **Step 3: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add Nodyra landing page shell with CSS tokens"
```

---

### Task 2: Nav

**Files:**
- Modify: `brand/homepage/nodyra.html` — fill `<nav id="site-nav">` + add nav CSS

**Produces:** Sticky frosted-glass nav with inlined Mark B SVG, wordmark, centre links, and primary "Join waitlist" CTA button.

- [ ] **Step 1: Add nav CSS inside `<style>` before `</style>`**

```css
/* ── nav ── */
#site-nav{
  position:sticky;top:0;z-index:50;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  background:rgba(9,11,16,0.72);
  border-bottom:1px solid transparent;
  transition:border-color 200ms ease;
}
#site-nav.scrolled{border-bottom-color:var(--line-2)}
.nav-in{
  display:flex;align-items:center;justify-content:space-between;
  height:66px;
}
.nav-brand{display:flex;align-items:center;gap:10px;flex-shrink:0}
.nav-brand-name{
  font-family:var(--font-display);font-size:26px;font-weight:800;
  letter-spacing:-0.045em;color:var(--ink);
}
.nav-links{display:flex;gap:2px}
.nav-links a{
  font-size:13px;font-weight:600;color:var(--ink-3);
  padding:7px 13px;border-radius:7px;
  transition:background 120ms ease,color 120ms ease;
}
.nav-links a:hover{color:var(--ink);background:var(--surface-2)}
.nav-links a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:7px}
.nav-cta{display:flex;align-items:center;gap:12px;flex-shrink:0}
@media(max-width:820px){.nav-links{display:none}}
```

- [ ] **Step 2: Fill `<nav id="site-nav">` with HTML**

Replace `<nav id="site-nav" aria-label="Main navigation"></nav>` with:

```html
<nav id="site-nav" aria-label="Main navigation">
  <div class="wrap nav-in">
    <a href="#" class="nav-brand" aria-label="Nodyra home">
      <svg width="28" height="28" viewBox="0 0 50 50" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <rect width="50" height="50" rx="11" fill="#090b10"/>
        <path d="M10 39 C 10 22, 40 28, 40 11" stroke="url(#nav-gb)" stroke-width="4" stroke-linecap="round" fill="none"/>
        <circle cx="10" cy="39" r="5" fill="#4c9eff"/>
        <circle cx="10" cy="39" r="5" stroke="#79b6ff" stroke-width="1" opacity="0.4"/>
        <circle cx="40" cy="11" r="3.8" fill="#79b6ff"/>
        <circle cx="25" cy="25" r="2" fill="#ffd479" opacity="0.85"/>
        <defs>
          <linearGradient id="nav-gb" x1="10" y1="39" x2="40" y2="11" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stop-color="#4c9eff"/>
            <stop offset="50%" stop-color="#79b6ff"/>
            <stop offset="100%" stop-color="#c8a6ff"/>
          </linearGradient>
        </defs>
      </svg>
      <span class="nav-brand-name">Nodyra</span>
    </a>
    <nav class="nav-links" aria-label="Site sections">
      <a href="#how">AI Agent</a>
      <a href="#why">Platform</a>
      <a href="#cta">Docs</a>
    </nav>
    <div class="nav-cta">
      <a href="#cta" class="btn btn-primary">Join waitlist</a>
    </div>
  </div>
</nav>
```

- [ ] **Step 3: Add nav scroll JS inside `<script>` block**

```js
(function () {
  var nav = document.getElementById('site-nav');
  window.addEventListener('scroll', function () {
    nav.classList.toggle('scrolled', window.scrollY > 40);
  }, { passive: true });
})();
```

- [ ] **Step 4: Open in browser and verify**

Expected: Sticky nav bar with Mark B logo + "Nodyra" wordmark, three centre links, blue "Join waitlist" button. Scroll down (add temp `height:200vh` to body to test) — border-bottom appears at 40px scroll. Remove temp height after testing.

- [ ] **Step 5: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add nav with Mark B logo and frosted scroll behaviour"
```

---

### Task 3: Hero canvas panel (left side)

**Files:**
- Modify: `brand/homepage/nodyra.html` — add hero section CSS + left canvas panel HTML with real node markup

**Produces:** The left 55% of the hero: dark canvas background, 4 positioned `.node` tiles with port handles, SVG bezier edges between them, dot-grid overlay. Nodes are visible but static (animation added in Task 5).

**Key CSS to know (from `apps/web/src/editor.css`):**
- `.node` — 72px wide, flex-column, `align-items: center`
- `.node-tile` — 72×72, `border-radius: 17px`, `--surface-2` bg, `1.5px` border, uses `--cat` CSS var for category tint colour
- `.node-tile::before` — absolute inset overlay using `background: var(--cat); opacity: 0.12` — this is the coloured tint
- `.node-label` — `width: 116px; margin: 9px -22px 0; text-align: center; font-weight: 600; font-size: 12px`
- Port handles are small circles on tile edges: `width: 10px; height: 10px; border-radius: 50%`

- [ ] **Step 1: Add hero + canvas CSS inside `<style>`**

```css
/* ── hero ── */
#hero{
  min-height:100vh;
  display:grid;
  grid-template-columns:55fr 45fr;
  align-items:center;
}
@media(max-width:940px){
  #hero{grid-template-columns:1fr;min-height:auto}
  .hero-canvas-wrap{height:360px}
}

/* ── canvas panel ── */
.hero-canvas-wrap{
  position:relative;
  height:100vh;
  background:
    radial-gradient(ellipse at 50% 50%, rgba(255,255,255,0.015) 0%, transparent 60%),
    var(--canvas);
  overflow:hidden;
}
/* dot grid overlay */
.hero-canvas-wrap::before{
  content:"";
  position:absolute;inset:0;pointer-events:none;
  background-image:radial-gradient(circle, rgba(255,255,255,0.04) 1px, transparent 1px);
  background-size:28px 28px;
}
.canvas-stage{
  position:absolute;inset:0;
}

/* ── real app node styles (verbatim from apps/web/src/editor.css) ── */
.node{
  width:72px;
  display:flex;flex-direction:column;align-items:center;
  position:absolute;
  opacity:0;transform:translateY(14px) scale(0.94);
  transition:opacity 320ms ease,transform 320ms ease;
}
.node.visible{opacity:1;transform:none}
.node-tile{
  position:relative;
  width:72px;height:72px;border-radius:17px;
  background:var(--surface-2);
  border:1.5px solid var(--line);
  display:flex;align-items:center;justify-content:center;
  color:var(--cat,var(--accent));
  box-shadow:0 9px 22px -13px rgba(0,0,0,0.85);
  transition:border-color 130ms ease,box-shadow 130ms ease,transform 130ms ease;
}
.node-tile::before{
  content:"";
  position:absolute;inset:0;border-radius:15.5px;
  background:var(--cat,var(--accent));
  opacity:0.12;
}
.node-label{
  margin:9px -22px 0;
  width:116px;text-align:center;
  font-weight:600;font-size:12px;line-height:1.25;color:var(--ink);
}

/* port handles */
.port{
  position:absolute;
  width:10px;height:10px;border-radius:50%;
  border:2px solid var(--canvas);
  top:50%;transform:translateY(-50%);
  z-index:2;
}
.port-out{right:-6px}
.port-in{left:-6px}

/* edges SVG overlay */
.canvas-edges{
  position:absolute;inset:0;
  width:100%;height:100%;
  pointer-events:none;overflow:visible;
}
.canvas-edges path{
  fill:none;
  stroke:var(--accent);
  stroke-width:2;
  stroke-linecap:round;
  stroke-dasharray:1000;
  stroke-dashoffset:1000;
  transition:stroke-dashoffset 500ms ease;
}
.canvas-edges path.drawn{stroke-dashoffset:0}

/* toggle bar */
.canvas-toggle{
  position:absolute;bottom:24px;left:50%;transform:translateX(-50%);
  display:inline-flex;background:rgba(9,11,16,0.82);
  border:1px solid var(--line);border-radius:999px;padding:4px;
  backdrop-filter:blur(8px);z-index:10;gap:2px;
}
.canvas-toggle button{
  font-family:var(--font-mono);font-size:12px;font-weight:500;
  color:var(--ink-3);background:transparent;border:0;
  padding:6px 16px;border-radius:999px;cursor:pointer;
  transition:background 200ms,color 200ms;
}
.canvas-toggle button.active{
  background:linear-gradient(180deg,var(--accent-2),var(--accent));
  color:#06101e;font-weight:600;
}

/* python pane (hidden by default) */
.python-pane{
  position:absolute;inset:0;
  display:flex;flex-direction:column;justify-content:center;
  padding:40px 36px;
  opacity:0;pointer-events:none;
  transition:opacity 300ms ease;
}
.python-pane.visible{opacity:1;pointer-events:auto}
.python-pane pre{
  font-family:var(--font-mono);font-size:13.5px;line-height:1.75;
  color:var(--ink);overflow:auto;
  background:var(--surface);border:1px solid var(--line);
  border-radius:var(--radius);padding:22px 24px;
  box-shadow:var(--shadow);
}
.py-kw{color:#4c9eff}.py-fn{color:#79b6ff}.py-str{color:#8fd06f}
.py-cm{color:var(--ink-3)}.py-dec{color:#ffd479}.py-num{color:#c8a6ff}
.py-node-tag{
  display:inline-block;margin-bottom:14px;
  font-family:var(--font-mono);font-size:11px;letter-spacing:0.06em;
  text-transform:uppercase;color:var(--ink-3);
  border:1px solid var(--line-2);border-radius:5px;padding:3px 10px;
}
```

- [ ] **Step 2: Fill `<section id="hero">` with left canvas panel HTML**

Replace `<section id="hero" class="layer"></section>` with:

```html
<section id="hero" class="layer">
  <!-- LEFT: live canvas -->
  <div class="hero-canvas-wrap" aria-hidden="true">
    <div class="canvas-stage" id="canvas-stage">
      <!-- SVG edges drawn between nodes -->
      <svg class="canvas-edges" id="canvas-edges" aria-hidden="true">
        <!-- edges injected by JS in Task 5 -->
      </svg>

      <!-- Node 1: Webhook Trigger -->
      <div class="node" id="n1" style="left:60px;top:calc(50% - 80px);--cat:#4c9eff">
        <div class="node-tile">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M13.73 21a2 2 0 0 1-3.46 0" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          <div class="port port-out" style="background:#4c9eff"></div>
        </div>
        <div class="node-label">Webhook Trigger</div>
      </div>

      <!-- Node 2: Claude AI -->
      <div class="node" id="n2" style="left:220px;top:calc(50% - 180px);--cat:#8fd06f">
        <div class="node-tile">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z" stroke="currentColor" stroke-width="1.8"/>
            <path d="M8 12h8M12 8v8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
          </svg>
          <div class="port port-in" style="background:#94a3b8"></div>
          <div class="port port-out" style="background:#8fd06f"></div>
        </div>
        <div class="node-label">Claude AI</div>
        <!-- blinking cursor overlay -->
        <div class="node-cursor" id="node-cursor" aria-hidden="true"
             style="position:absolute;top:-4px;right:-4px;width:3px;height:12px;
                    background:var(--accent);border-radius:1px;opacity:0"></div>
      </div>

      <!-- Node 3: Transform -->
      <div class="node" id="n3" style="left:390px;top:calc(50% - 40px);--cat:#c084fc">
        <div class="node-tile">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <polyline points="16 3 21 3 21 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <line x1="4" y1="20" x2="21" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <polyline points="21 16 21 21 16 21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <line x1="15" y1="15" x2="21" y2="21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          <div class="port port-in" style="background:#94a3b8"></div>
          <div class="port port-out" style="background:#c084fc"></div>
        </div>
        <div class="node-label">Transform</div>
      </div>

      <!-- Node 4: HTTP Request -->
      <div class="node" id="n4" style="left:560px;top:calc(50% - 160px);--cat:#f97316">
        <div class="node-tile">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="1.8"/>
            <line x1="2" y1="12" x2="22" y2="12" stroke="currentColor" stroke-width="1.8"/>
            <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" stroke="currentColor" stroke-width="1.8"/>
          </svg>
          <div class="port port-in" style="background:#94a3b8"></div>
        </div>
        <div class="node-label">HTTP Request</div>
      </div>
    </div>

    <!-- Graph / Python toggle -->
    <div class="canvas-toggle" role="group" aria-label="View toggle">
      <button id="btn-graph" class="active" onclick="setCanvasView('graph')">Graph</button>
      <button id="btn-python" onclick="setCanvasView('python')">Python</button>
    </div>

    <!-- Python source pane (hidden until toggled) -->
    <div class="python-pane" id="python-pane">
      <div class="py-node-tag">Transform · Python source</div>
      <pre><span class="py-dec">@nodyra.node</span>
<span class="py-kw">def</span> <span class="py-fn">run</span>(inputs):
    <span class="py-cm"># inputs come from Claude AI node output</span>
    df = inputs[<span class="py-str">"data"</span>]

    result = df[df[<span class="py-str">"status"</span>] == <span class="py-str">"active"</span>]

    <span class="py-kw">return</span> {
        <span class="py-str">"filtered"</span>: result,
        <span class="py-str">"count"</span>: <span class="py-fn">len</span>(result),
        <span class="py-str">"summary"</span>: result.<span class="py-fn">describe</span>()
    }</pre>
    </div>
  </div>

  <!-- RIGHT: copy panel (Task 4) -->
  <div class="hero-copy" id="hero-copy"></div>
</section>
```

- [ ] **Step 3: Open in browser and verify**

Expected: Left half is a near-black canvas with dot-grid, 4 node tiles positioned across it (black bg, coloured tint, label below each). Nodes are invisible (opacity 0) — animation added in Task 5. Right half is empty. Toggle buttons visible at bottom of canvas.

- [ ] **Step 4: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add hero canvas panel with real app node markup"
```

---

### Task 4: Hero copy panel (right side) + waitlist form

**Files:**
- Modify: `brand/homepage/nodyra.html` — fill `.hero-copy` div + add right-panel CSS + form JS

**Produces:** The right 45% of the hero with status pill, headline, sub-copy, inline email waitlist form with spinner → ✓ confirmation on submit.

- [ ] **Step 1: Add hero copy CSS inside `<style>`**

```css
/* ── hero copy (right panel) ── */
.hero-copy{
  padding:80px 56px 80px 40px;
  display:flex;flex-direction:column;
  gap:0;
}
@media(max-width:940px){
  .hero-copy{padding:52px 32px;order:-1}
}
@media(max-width:640px){.hero-copy{padding:44px 24px}}

.status-pill{
  display:inline-flex;align-items:center;gap:8px;
  font-family:var(--font-mono);font-size:11px;
  text-transform:uppercase;letter-spacing:0.07em;
  color:var(--accent);background:var(--accent-glow);
  border:1px solid rgba(76,158,255,0.28);
  padding:6px 12px;border-radius:999px;
  margin-bottom:28px;width:fit-content;
}
.status-dot{
  width:7px;height:7px;border-radius:50%;
  background:var(--ok);
  box-shadow:0 0 0 4px rgba(87,201,138,0.18);
}

h1.hero-headline{
  font-family:var(--font-display);
  font-size:clamp(40px,5.6vw,68px);
  font-weight:800;line-height:.97;letter-spacing:-0.035em;
  color:var(--ink);margin-bottom:24px;
}
h1.hero-headline .grad{
  background:linear-gradient(135deg,var(--accent-2) 0%,var(--accent) 60%);
  -webkit-background-clip:text;background-clip:text;color:transparent;
}

.hero-sub{
  font-size:17px;color:var(--ink-2);
  max-width:48ch;line-height:1.65;
  margin-bottom:36px;
}
.hero-sub b{color:var(--ink)}

/* waitlist form */
.waitlist-form{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
.waitlist-form .field-input{
  flex:1;min-width:220px;font-size:14px;padding:10px 13px;
  border-radius:var(--radius-sm);
}
.waitlist-form .btn-primary{
  font-size:14px;padding:10px 20px;white-space:nowrap;flex-shrink:0;
  border-radius:var(--radius-sm);
}
.waitlist-spinner{
  display:inline-block;width:14px;height:14px;
  border:2px solid rgba(255,255,255,0.35);border-top-color:#fff;
  border-radius:50%;animation:wspin 0.65s linear infinite;
}
@keyframes wspin{to{transform:rotate(360deg)}}
.waitlist-success{
  display:none;align-items:center;gap:8px;
  font-size:14px;color:var(--ok);font-weight:600;
  padding:10px 0;
}
.waitlist-success.show{display:flex}

.hero-note{
  margin-top:18px;
  font-family:var(--font-mono);font-size:12px;color:var(--ink-3);
}
.hero-note b{color:var(--ink-2)}
```

- [ ] **Step 2: Fill `<div class="hero-copy" id="hero-copy">` with HTML**

Replace `<div class="hero-copy" id="hero-copy"></div>` with:

```html
<div class="hero-copy">
  <div class="status-pill">
    <span class="status-dot" aria-hidden="true"></span>
    live on your infra
  </div>

  <h1 class="hero-headline">
    Your AI agent<br>
    <span class="grad">builds the workflow.</span>
  </h1>

  <p class="hero-sub">
    Describe what you need. It writes the Python and lays it out as
    <b>nodes on a canvas.</b> Every node is real code — export it, fork it, own it.
  </p>

  <form class="waitlist-form" id="waitlist-hero" novalidate>
    <label for="email-hero" style="position:absolute;opacity:0;pointer-events:none">Email address</label>
    <input
      id="email-hero"
      class="field-input"
      type="email"
      placeholder="you@company.io"
      autocomplete="email"
      aria-label="Email address"
      required
    >
    <button type="submit" class="btn btn-primary" id="btn-hero-submit">
      Join the waitlist
    </button>
  </form>
  <div class="waitlist-success" id="success-hero" role="status" aria-live="polite">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    You're on the list. We'll be in touch.
  </div>

  <p class="hero-note">
    Self-hostable &nbsp;·&nbsp; Python-native &nbsp;·&nbsp; MCP server included
  </p>
</div>
```

- [ ] **Step 3: Add form handling JS inside `<script>`**

```js
function handleWaitlistForm(formId, btnId, successId) {
  var form = document.getElementById(formId);
  var btn = document.getElementById(btnId);
  var success = document.getElementById(successId);
  if (!form) return;
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var email = form.querySelector('input[type="email"]').value.trim();
    if (!email || !email.includes('@')) return;
    // Show spinner
    btn.disabled = true;
    btn.innerHTML = '<span class="waitlist-spinner" aria-hidden="true"></span> Joining…';
    // Simulate async (replace with real API call when backend exists)
    setTimeout(function () {
      form.style.display = 'none';
      success.classList.add('show');
    }, 900);
  });
}
handleWaitlistForm('waitlist-hero', 'btn-hero-submit', 'success-hero');
```

- [ ] **Step 4: Open in browser and verify**

Expected: Right panel shows blue status pill, large headline with "builds the workflow." in blue gradient, grey sub-copy, email input + "Join the waitlist" button in a row. Submit with an email → button shows spinner → success message appears in ~1s.

- [ ] **Step 5: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add hero copy panel with headline and waitlist form"
```

---

### Task 5: Canvas node animation + Graph/Python toggle

**Files:**
- Modify: `brand/homepage/nodyra.html` — add JS for staggered node entrance, edge tracing, blinking cursor, and toggle logic

**Produces:** Nodes draw themselves in at page load with a 600ms stagger, edges trace after each node, a blinking cursor appears on Claude AI, and the Graph/Python toggle swaps views.

- [ ] **Step 1: Add animation CSS inside `<style>`**

```css
/* cursor blink animation */
@keyframes cursor-blink{
  0%,100%{opacity:0} 45%,55%{opacity:1}
}
.node-cursor.blinking{animation:cursor-blink 1.1s ease-in-out 3}

/* reduced motion: skip all canvas animation */
@media(prefers-reduced-motion:reduce){
  .node{opacity:1!important;transform:none!important;transition:none!important}
  .canvas-edges path{stroke-dashoffset:0!important;transition:none!important}
  .node-cursor{display:none}
  .python-pane,.canvas-stage{transition:none!important}
}
```

- [ ] **Step 2: Add canvas animation + toggle JS inside `<script>`, after the `handleWaitlistForm` call**

```js
(function () {
  // ── node entrance animation ──
  // Node positions used to calculate edge endpoints:
  //   n1: left=60, top=calc(50%-80px) → centre at (60+36, y+36)
  //   n2: left=220, top=calc(50%-180px)
  //   n3: left=390, top=calc(50%-40px)
  //   n4: left=560, top=calc(50%-160px)
  // Port centres are at tile edge (left or right) + 36px vertical
  // We compute these relative to the canvas-stage bounding box in JS.

  function getPortPos(nodeEl, side) {
    var stage = document.getElementById('canvas-stage');
    var stageRect = stage.getBoundingClientRect();
    var tile = nodeEl.querySelector('.node-tile');
    var tileRect = tile.getBoundingClientRect();
    var x = side === 'out'
      ? tileRect.right - stageRect.left
      : tileRect.left - stageRect.left;
    var y = tileRect.top + tileRect.height / 2 - stageRect.top;
    return { x: x, y: y };
  }

  function drawEdge(svgEl, fromNode, toNode, delay) {
    setTimeout(function () {
      var from = getPortPos(fromNode, 'out');
      var to = getPortPos(toNode, 'in');
      var cx = (from.x + to.x) / 2;
      var d = 'M ' + from.x + ' ' + from.y +
              ' C ' + cx + ' ' + from.y + ', ' + cx + ' ' + to.y + ', ' + to.x + ' ' + to.y;
      var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d);
      svgEl.appendChild(path);
      // force reflow then trigger CSS transition
      path.getBoundingClientRect();
      path.classList.add('drawn');
    }, delay);
  }

  function animateCanvas() {
    var nodes = [
      document.getElementById('n1'),
      document.getElementById('n2'),
      document.getElementById('n3'),
      document.getElementById('n4'),
    ];
    var svg = document.getElementById('canvas-edges');
    var cursor = document.getElementById('node-cursor');
    var delays = [0, 420, 840, 1260];

    nodes.forEach(function (node, i) {
      setTimeout(function () {
        node.classList.add('visible');
        // draw edge TO this node after it appears (except first node)
        if (i > 0 && svg) {
          drawEdge(svg, nodes[i - 1], node, 180);
        }
        // blink cursor on Claude AI (node 2) after it appears
        if (i === 1 && cursor) {
          setTimeout(function () {
            cursor.style.opacity = '1';
            cursor.classList.add('blinking');
            cursor.addEventListener('animationend', function () {
              cursor.style.opacity = '0';
            }, { once: true });
          }, 300);
        }
      }, delays[i]);
    });
  }

  // Run on load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', animateCanvas);
  } else {
    animateCanvas();
  }

  // ── Graph / Python toggle ──
  window.setCanvasView = function (view) {
    var stage = document.getElementById('canvas-stage');
    var pythonPane = document.getElementById('python-pane');
    var btnGraph = document.getElementById('btn-graph');
    var btnPython = document.getElementById('btn-python');
    if (view === 'python') {
      stage.style.opacity = '0';
      stage.style.pointerEvents = 'none';
      pythonPane.classList.add('visible');
      btnPython.classList.add('active');
      btnGraph.classList.remove('active');
    } else {
      stage.style.opacity = '1';
      stage.style.pointerEvents = 'auto';
      pythonPane.classList.remove('visible');
      btnGraph.classList.add('active');
      btnPython.classList.remove('active');
    }
  };

  // Add transition to canvas-stage for smooth toggle
  var stage = document.getElementById('canvas-stage');
  if (stage) stage.style.transition = 'opacity 300ms ease';
})();
```

- [ ] **Step 3: Open in browser and verify**

Expected: On page load, node 1 appears (fades in from below), edge traces to node 2 (~180ms later), node 2 appears (~420ms), a blinking cursor appears briefly on it, edge traces to node 3, etc. Full sequence completes in ~1.5s. Click "Python" toggle: canvas fades out, Python code panel slides in with colour-highlighted source. Click "Graph" toggle: switches back.

- [ ] **Step 4: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add node entrance animation and graph/python toggle"
```

---

### Task 6: How it works section

**Files:**
- Modify: `brand/homepage/nodyra.html` — fill `<section id="how">` + add CSS

**Produces:** 3-column step strip with scroll-reveal animation.

- [ ] **Step 1: Add how-it-works CSS inside `<style>`**

```css
/* ── section shared styles ── */
.section-pad{padding:96px 0}
.eyebrow{
  font-family:var(--font-mono);font-size:11.5px;
  letter-spacing:0.18em;text-transform:uppercase;
  color:var(--accent);margin-bottom:14px;
}
h2.section-h{
  font-family:var(--font-display);
  font-size:clamp(28px,3.8vw,44px);
  font-weight:800;line-height:1.05;letter-spacing:-0.03em;
  max-width:22ch;
}
.section-lede{
  font-size:17px;color:var(--ink-2);max-width:56ch;
  margin-top:16px;line-height:1.65;
}

/* scroll reveal */
.reveal{
  opacity:0;transform:translateY(22px);
  transition:opacity 560ms ease,transform 560ms ease;
}
.reveal.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){.reveal{opacity:1;transform:none;transition:none}}

/* ── how-it-works steps ── */
.steps{
  display:grid;grid-template-columns:repeat(3,1fr);gap:0;
  margin-top:52px;
  border:1px solid var(--line-2);border-radius:var(--radius);
  overflow:hidden;background:var(--surface);
}
@media(max-width:740px){.steps{grid-template-columns:1fr}}
.step{
  padding:28px 26px;
  border-right:1px solid var(--line-2);
}
.step:last-child{border-right:0}
@media(max-width:740px){.step{border-right:0;border-bottom:1px solid var(--line-2)}}
@media(max-width:740px){.step:last-child{border-bottom:0}}
.step-num{
  font-family:var(--font-mono);font-size:11px;
  color:var(--accent);font-weight:600;letter-spacing:0.04em;
  margin-bottom:12px;
}
.step h3{
  font-family:var(--font-display);font-size:17px;font-weight:700;
  margin-bottom:8px;
}
.step p{font-size:14px;color:var(--ink-2);line-height:1.6}
```

- [ ] **Step 2: Fill `<section id="how">` with HTML**

Replace `<section id="how" class="layer"></section>` with:

```html
<section id="how" class="layer section-pad">
  <div class="wrap">
    <p class="eyebrow reveal">How it works</p>
    <h2 class="section-h reveal">From prompt to running Python in seconds.</h2>

    <div class="steps reveal">
      <div class="step">
        <div class="step-num">01</div>
        <h3>Connect your LLM via MCP</h3>
        <p>Point any OpenAI-compatible model at Nodyra's MCP server. Claude, GPT-4o, local Ollama — your choice, your keys.</p>
      </div>
      <div class="step">
        <div class="step-num">02</div>
        <h3>Describe what you need</h3>
        <p>The agent writes a workflow and you see it appear as nodes on the canvas, live. Missing an integration? It writes that node too.</p>
      </div>
      <div class="step">
        <div class="step-num">03</div>
        <h3>Every node is real Python</h3>
        <p>Inspect the source, edit it in place, export it all as a Python package. No black boxes. No lock-in. Your code, your environment.</p>
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Add scroll-reveal JS inside `<script>`**

```js
(function () {
  var els = document.querySelectorAll('.reveal');
  if (!els.length) return;
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
    });
  }, { threshold: 0.12 });
  els.forEach(function (el) { io.observe(el); });
})();
```

- [ ] **Step 4: Open in browser and verify**

Expected: Three-column step strip (stacks on mobile). Scroll down — elements fade and slide up into view as they enter the viewport. "How it works" eyebrow, headline, then the 3 steps panel in a single bordered card.

- [ ] **Step 5: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add 'how it works' section with scroll reveal"
```

---

### Task 7: Why Nodyra pitch cards

**Files:**
- Modify: `brand/homepage/nodyra.html` — fill `<section id="why">` + add card CSS

**Produces:** 3 feature pitch cards with hover lift effect and top-gradient line reveal, matching the app's card style.

- [ ] **Step 1: Add pitch card CSS inside `<style>`**

```css
/* ── pitch cards ── */
.pitch{
  display:grid;grid-template-columns:repeat(3,1fr);
  gap:16px;margin-top:48px;
}
@media(max-width:780px){.pitch{grid-template-columns:1fr}}

.pcard{
  background:var(--surface);border:1px solid var(--line-2);
  border-radius:var(--radius);padding:28px;
  position:relative;overflow:hidden;
  transition:border-color 140ms ease,transform 140ms ease,background 140ms ease;
}
.pcard::before{
  content:"";position:absolute;left:0;right:0;top:0;height:2px;
  background:linear-gradient(90deg,var(--accent),transparent);
  opacity:0;transition:opacity 160ms ease;
}
.pcard:hover{
  border-color:var(--accent-deep);background:var(--surface-2);
  transform:translateY(-3px);
}
.pcard:hover::before{opacity:1}

.pcard-icon{
  width:44px;height:44px;display:grid;place-items:center;
  border-radius:10px;background:var(--accent-glow);
  border:1px solid rgba(76,158,255,0.25);margin-bottom:16px;
  color:var(--accent);
}
.pcard h3{
  font-family:var(--font-display);font-size:19px;font-weight:700;
  margin-bottom:8px;
}
.pcard p{font-size:14px;color:var(--ink-2);line-height:1.6}
```

- [ ] **Step 2: Fill `<section id="why">` with HTML**

Replace `<section id="why" class="layer"></section>` with:

```html
<section id="why" class="layer section-pad" style="background:linear-gradient(180deg,rgba(76,158,255,0.03),transparent)">
  <div class="wrap">
    <p class="eyebrow reveal">Why Nodyra</p>
    <h2 class="section-h reveal">Built for engineers who want to own what they run.</h2>

    <div class="pitch">
      <div class="pcard reveal">
        <div class="pcard-icon" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <polyline points="16 18 22 12 16 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <polyline points="8 6 2 12 8 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <h3>Real Python nodes</h3>
        <p>Not a DSL, not YAML, not a click-through. Every node is a <code style="font-family:var(--font-mono);font-size:12px;color:var(--accent)">def run(inputs):</code> function. Use pandas, boto3, your internal libraries — whatever the interpreter can import.</p>
      </div>

      <div class="pcard reveal">
        <div class="pcard-icon" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" stroke="currentColor" stroke-width="2"/>
            <path d="M7 11V7a5 5 0 0 1 10 0v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </div>
        <h3>Self-hosted, your data stays</h3>
        <p>One <code style="font-family:var(--font-mono);font-size:12px;color:var(--accent)">docker compose up</code>. License verified offline — air-gap friendly. An expired license soft-downgrades to Community; your workflows never stop running.</p>
      </div>

      <div class="pcard reveal">
        <div class="pcard-icon" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <h3>MCP in both directions</h3>
        <p>Connect any LLM via MCP to build and run workflows. Expose your workflows as MCP tools so other agents can call them. Fully bidirectional, model-agnostic.</p>
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Open in browser and verify**

Expected: Three cards in a row (stack on mobile). Hover a card — it lifts 3px, blue top-line gradient reveals, border brightens. Scroll — cards fade in via `.reveal`.

- [ ] **Step 4: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add 'Why Nodyra' pitch cards section"
```

---

### Task 8: Platform capabilities bento grid

**Files:**
- Modify: `brand/homepage/nodyra.html` — fill `<section id="platform">` + add grid CSS

**Produces:** 6-card bento grid (3×2) showing platform features, with amber Pro badges on gated features.

- [ ] **Step 1: Add bento grid CSS inside `<style>`**

```css
/* ── platform grid ── */
.bento{
  display:grid;
  grid-template-columns:repeat(3,1fr);
  gap:12px;margin-top:48px;
}
@media(max-width:860px){.bento{grid-template-columns:repeat(2,1fr)}}
@media(max-width:540px){.bento{grid-template-columns:1fr}}

.bento-card{
  background:var(--surface);border:1px solid var(--line-2);
  border-radius:var(--radius);padding:22px 20px;
  display:flex;flex-direction:column;gap:10px;
  transition:border-color 140ms ease,background 140ms ease;
}
.bento-card:hover{border-color:var(--accent-deep);background:var(--surface-2)}

.bento-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.bento-icon{
  width:36px;height:36px;display:grid;place-items:center;
  border-radius:9px;background:var(--accent-glow);
  border:1px solid rgba(76,158,255,0.22);color:var(--accent);flex-shrink:0;
}
.pro-badge{
  font-family:var(--font-mono);font-size:10px;font-weight:600;
  letter-spacing:0.04em;text-transform:uppercase;
  color:var(--amber);background:rgba(255,212,121,0.12);
  border:1px solid rgba(255,212,121,0.28);
  border-radius:5px;padding:2px 7px;white-space:nowrap;
}
.bento-card h3{font-size:15px;font-weight:700;margin:0}
.bento-card p{font-size:13px;color:var(--ink-2);line-height:1.55;margin:0}
```

- [ ] **Step 2: Fill `<section id="platform">` with HTML**

Replace `<section id="platform" class="layer"></section>` with:

```html
<section id="platform" class="layer section-pad">
  <div class="wrap">
    <p class="eyebrow reveal">Platform</p>
    <h2 class="section-h reveal">Everything you need to run AI workflows in production.</h2>

    <div class="bento">
      <div class="bento-card reveal">
        <div class="bento-head">
          <div class="bento-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/>
              <path d="M8 12h8M12 8v8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </div>
        </div>
        <h3>AI &amp; ML suite</h3>
        <p>AI Agent, LLM chain, embeddings, vector store, text splitter, and output parser nodes — all wired for tool-calling and streaming.</p>
      </div>

      <div class="bento-card reveal">
        <div class="bento-head">
          <div class="bento-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </div>
        </div>
        <h3>MCP server + client</h3>
        <p>Nodyra runs as an MCP server so any LLM can invoke your workflows as tools. It also connects upstream to any MCP-compatible model.</p>
      </div>

      <div class="bento-card reveal">
        <div class="bento-head">
          <div class="bento-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <polyline points="16 18 22 12 16 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <polyline points="8 6 2 12 8 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </div>
        </div>
        <h3>Code-first export</h3>
        <p>Export any workflow as a standalone Python package with a single CLI command. Run it anywhere without Nodyra installed.</p>
      </div>

      <div class="bento-card reveal">
        <div class="bento-head">
          <div class="bento-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="2"/>
              <path d="M9 9h6M9 12h6M9 15h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="pro-badge">Pro</span>
        </div>
        <h3>Sandboxed execution</h3>
        <p>Run untrusted code in isolated Docker containers with CPU, memory, and network limits. Safe multi-tenant execution.</p>
      </div>

      <div class="bento-card reveal">
        <div class="bento-head">
          <div class="bento-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>
          <span class="pro-badge">Pro</span>
        </div>
        <h3>Observability</h3>
        <p>Per-node token streaming, execution timelines, run diffs, and audit logs. Know exactly what ran, when, and why.</p>
      </div>

      <div class="bento-card reveal">
        <div class="bento-head">
          <div class="bento-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <rect x="2" y="3" width="20" height="14" rx="2" stroke="currentColor" stroke-width="2"/>
              <line x1="8" y1="21" x2="16" y2="21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <line x1="12" y1="17" x2="12" y2="21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </div>
          <span class="pro-badge">Pro</span>
        </div>
        <h3>Remote runners</h3>
        <p>Deploy execution agents on your cloud or on-prem servers. Route workflows to specific environments by label.</p>
      </div>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Open in browser and verify**

Expected: 3×2 grid of cards. Amber "Pro" badge on the last 3. Hover — border brightens, background darkens slightly. Stacks to 2-col then 1-col on mobile.

- [ ] **Step 4: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add platform capabilities bento grid"
```

---

### Task 9: Final CTA band + footer

**Files:**
- Modify: `brand/homepage/nodyra.html` — fill `<section id="cta">` and `<footer id="site-footer">` + add CSS

**Produces:** Dark full-width CTA band with repeated waitlist form, and a minimal footer.

- [ ] **Step 1: Add CTA + footer CSS inside `<style>`**

```css
/* ── final CTA band ── */
.cta-band{
  background:var(--surface);border:1px solid var(--line);
  border-radius:18px;padding:72px 56px;
  position:relative;overflow:hidden;text-align:center;
  margin:0 0 40px;
}
.cta-band::after{
  content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(700px 340px at 90% -20%, rgba(76,158,255,0.10), transparent 60%);
}
.cta-band>*{position:relative;z-index:1}
.cta-band h2{
  font-family:var(--font-display);
  font-size:clamp(26px,3.8vw,44px);
  font-weight:800;letter-spacing:-0.03em;
  max-width:26ch;margin:0 auto 28px;line-height:1.08;
}
.cta-form-wrap{
  display:flex;gap:10px;justify-content:center;flex-wrap:wrap;
  max-width:520px;margin:0 auto;
}
.cta-form-wrap .field-input{
  flex:1;min-width:220px;font-size:14px;padding:10px 13px;
}
.cta-form-wrap .btn-primary{font-size:14px;padding:10px 20px;white-space:nowrap}
.cta-note{
  margin-top:18px;font-family:var(--font-mono);font-size:11.5px;color:var(--ink-3);
  line-height:1.6;
}

/* ── footer ── */
#site-footer{
  border-top:1px solid var(--line-2);padding:40px 0;
}
.foot-in{
  display:flex;justify-content:space-between;align-items:center;
  flex-wrap:wrap;gap:18px;
}
.foot-brand{display:flex;align-items:center;gap:9px}
.foot-brand-name{
  font-family:var(--font-display);font-size:18px;font-weight:800;
  letter-spacing:-0.04em;
}
.foot-links{display:flex;gap:20px;flex-wrap:wrap}
.foot-links a{font-size:13px;color:var(--ink-3);transition:color 120ms ease}
.foot-links a:hover{color:var(--accent)}
.foot-copy{
  margin-top:20px;font-size:12.5px;color:var(--ink-3);
  display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;
}
@media(max-width:600px){
  .cta-band{padding:52px 28px}
  .foot-in{flex-direction:column;align-items:flex-start}
}
```

- [ ] **Step 2: Fill `<section id="cta">` with HTML**

Replace `<section id="cta" class="layer"></section>` with:

```html
<section id="cta" class="layer section-pad" style="padding-bottom:0">
  <div class="wrap">
    <div class="cta-band reveal">
      <h2>Your AI builds it. Your Python runs it. You own it all.</h2>

      <form class="cta-form-wrap" id="waitlist-cta" novalidate>
        <label for="email-cta" style="position:absolute;opacity:0;pointer-events:none">Email address</label>
        <input
          id="email-cta"
          class="field-input"
          type="email"
          placeholder="you@company.io"
          autocomplete="email"
          aria-label="Email address"
          required
        >
        <button type="submit" class="btn btn-primary" id="btn-cta-submit">
          Join the waitlist
        </button>
      </form>
      <div class="waitlist-success" id="success-cta" role="status" aria-live="polite">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        You're on the list. We'll be in touch.
      </div>

      <p class="cta-note">
        Self-hostable &nbsp;·&nbsp; Air-gap friendly &nbsp;·&nbsp;
        One <code style="font-family:var(--font-mono);color:var(--accent)">docker compose up</code> away
      </p>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Fill `<footer id="site-footer">` with HTML**

Replace `<footer id="site-footer" class="layer"></footer>` with:

```html
<footer id="site-footer" class="layer">
  <div class="wrap">
    <div class="foot-in">
      <a href="#" class="foot-brand" aria-label="Nodyra home">
        <svg width="22" height="22" viewBox="0 0 50 50" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <rect width="50" height="50" rx="11" fill="#090b10"/>
          <path d="M10 39 C 10 22, 40 28, 40 11" stroke="url(#foot-gb)" stroke-width="4" stroke-linecap="round" fill="none"/>
          <circle cx="10" cy="39" r="5" fill="#4c9eff"/>
          <circle cx="40" cy="11" r="3.8" fill="#79b6ff"/>
          <circle cx="25" cy="25" r="2" fill="#ffd479" opacity="0.85"/>
          <defs>
            <linearGradient id="foot-gb" x1="10" y1="39" x2="40" y2="11" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stop-color="#4c9eff"/>
              <stop offset="50%" stop-color="#79b6ff"/>
              <stop offset="100%" stop-color="#c8a6ff"/>
            </linearGradient>
          </defs>
        </svg>
        <span class="foot-brand-name">Nodyra</span>
      </a>
      <nav class="foot-links" aria-label="Footer links">
        <a href="#how">AI Agent</a>
        <a href="#why">Platform</a>
        <a href="#cta">Docs</a>
        <a href="https://github.com" target="_blank" rel="noopener">GitHub</a>
      </nav>
    </div>
    <div class="foot-copy">
      <span>© 2026 Nodyra · Community edition MIT-licensed</span>
      <span style="color:var(--ink-3)">Built with Python &amp; caffeine</span>
    </div>
  </div>
</footer>
```

- [ ] **Step 4: Wire the CTA form JS inside `<script>`** (add after `handleWaitlistForm('waitlist-hero', ...)` call)

```js
handleWaitlistForm('waitlist-cta', 'btn-cta-submit', 'success-cta');
```

- [ ] **Step 5: Open in browser and verify**

Expected: Dark rounded CTA band with blue glow accent at top-right, headline centred, email form centred below it, mono note text below that. Footer: logo + wordmark left, links right, copyright below. Full page scrolls correctly start to finish.

- [ ] **Step 6: Commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): add final CTA band and footer"
```

---

### Task 10: Accessibility, responsive polish, and section padding fix

**Files:**
- Modify: `brand/homepage/nodyra.html` — final polish pass

**Produces:** All focus rings verified, `prefers-reduced-motion` confirmed, mobile layout verified at 375px, section spacing consistent.

- [ ] **Step 1: Verify focus rings in browser**

Open the page, press Tab repeatedly. Every interactive element (nav links, CTA button, email inputs, submit buttons, toggle buttons) must show a `2px solid #4c9eff` outline. If any is missing, add this to the element's CSS:

```css
element:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
```

The global `<style>` block already includes:
```css
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.field-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow)}
```

Check nav links and toggle buttons specifically.

- [ ] **Step 2: Add missing focus ring rules to `<style>` if needed**

```css
.nav-links a:focus-visible,.foot-links a:focus-visible{
  outline:2px solid var(--accent);outline-offset:2px;border-radius:5px;
}
.canvas-toggle button:focus-visible{
  outline:2px solid var(--accent);outline-offset:2px;
}
```

- [ ] **Step 3: Test prefers-reduced-motion in browser**

In Chrome DevTools → Rendering → Emulate CSS media feature `prefers-reduced-motion: reduce`. Expected: Nodes appear instantly (no fade-in), no edge trace animation, no cursor blink, section reveal elements are visible immediately. The `@media(prefers-reduced-motion:reduce)` rules handle this — verify they're working.

- [ ] **Step 4: Test at 375px viewport**

In DevTools, set viewport to 375×812. Check each section:
- Nav: links hidden, logo + CTA visible, no overflow
- Hero: single column, canvas panel stacks above copy
- Steps: single column
- Pitch cards: single column
- Bento: single column
- CTA band: form stacks, text readable
- Footer: stacked layout

Fix any overflow or padding issues by adding rules to existing `@media(max-width:...)` blocks.

- [ ] **Step 5: Add `::selection` style + scrollbar style to `<style>` (matches the app)**

```css
::selection{background:var(--accent);color:#06101e}
*{scrollbar-width:thin;scrollbar-color:var(--surface-3) transparent}
```

- [ ] **Step 6: Final browser check — full scroll-through**

Open in browser. Scroll from top to bottom. Verify:
- [ ] Nav sticks and border appears on scroll
- [ ] Nodes animate in on load
- [ ] Toggle switches between graph and Python views
- [ ] "How it works" elements reveal on scroll
- [ ] Pitch cards hover correctly
- [ ] Bento grid renders 3×2
- [ ] CTA form submit → spinner → success
- [ ] Footer renders cleanly
- [ ] No horizontal scroll at any viewport width

- [ ] **Step 7: Final commit**

```bash
git add brand/homepage/nodyra.html
git commit -m "feat(landing): final polish — a11y, reduced-motion, responsive"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| Nav: Mark B + "Nodyra" + links + "Join waitlist" | Task 2 |
| Nav frosted + border on scroll | Task 2 |
| Hero: full-viewport 55/45 split | Task 3–4 |
| Hero canvas: dot-grid, `--canvas` bg | Task 3 |
| Hero canvas: 4 real `.node` tiles with `.node-tile`, `.node-label`, ports | Task 3 |
| Hero canvas: SVG bezier edges | Task 3 |
| Hero canvas: staggered node entrance animation | Task 5 |
| Hero canvas: blinking cursor on Claude AI | Task 5 |
| Hero canvas: Graph/Python toggle | Task 5 |
| Hero copy: status pill | Task 4 |
| Hero copy: gradient headline | Task 4 |
| Hero copy: sub-copy | Task 4 |
| Waitlist form: email + submit + spinner + success inline | Task 4 |
| How it works: 3 steps | Task 6 |
| Why Nodyra: 3 pitch cards with hover lift | Task 7 |
| Platform bento: 6 cards, Pro badges | Task 8 |
| Final CTA band: headline + repeated form | Task 9 |
| Footer: logo + links + copyright | Task 9 |
| `prefers-reduced-motion` disables animation | Tasks 5, 6, 10 |
| Focus rings on all interactive elements | Task 10 |
| Mobile responsive to 375px | Tasks 3,4,6,7,8,9,10 |
| No external JS, no analytics | All tasks |
| `brand/homepage/index.html` left untouched | All tasks |

**Placeholder scan:** No TBD, TODO, or "implement later" present. Every code step has actual markup, CSS, or JS. ✓

**Type/name consistency:** `handleWaitlistForm` defined in Task 4, called in Tasks 4 and 9. `setCanvasView` defined in Task 5, called from HTML `onclick` in Task 3. `animateCanvas`, `drawEdge`, `getPortPos` all defined and used in Task 5 only. `reveal`/`in` CSS classes defined Task 6, applied in Tasks 6–9. `waitlist-hero`/`btn-hero-submit`/`success-hero` IDs consistent between HTML (Task 4) and JS (Task 4). `waitlist-cta`/`btn-cta-submit`/`success-cta` consistent between HTML (Task 9) and JS call (Task 9). ✓
