# Nodyra Landing Page — Design Spec

**Date:** 2026-06-30  
**Output:** `brand/homepage/nodyra.html` — single self-contained static HTML file  
**Primary CTA:** Waitlist email capture  
**Visual direction:** Power + precision + alive (dark, engineered, motion in the hero)

---

## Core decisions

- **Format:** Single self-contained HTML file with inlined CSS and vanilla JS. No build step. Opens directly in a browser. Replaces nothing — the existing `brand/homepage/index.html` is left untouched.
- **Node UI:** The hero canvas uses the **exact** CSS classes and markup from `apps/web/src` — `.node`, `.node-tile`, `.node-label`, port handles as SVG circles, bezier edges — not custom card designs. CSS variables are copied verbatim from `apps/web/src/index.css`.
- **Logo mark:** Nodyra Mark B (`brand/icons/nodyra-mark-b.svg`) — the pulse arc (single S-curve, large input node, small output node, amber midpoint dot).
- **Fonts:** Bricolage Grotesque (display 800), Hanken Grotesk (body 400–600), IBM Plex Mono (code/labels) — loaded from Google Fonts.
- **Animations:** One staggered entrance on page load (nodes draw in, edges trace). One scroll-reveal pass on sections. `prefers-reduced-motion` disables all animation. No looping, no parallax.

---

## Page sections

### 1. Nav
- Sticky, frosted (`backdrop-filter: blur(10px)`, `rgba(9,11,16,0.72)` bg, `--line-2` bottom border)
- Left: Mark B SVG (24px) + "Nodyra" wordmark (Bricolage Grotesque 800, 26px, `--ink`)
- Centre: nav links (AI Agent · Platform · Docs) — hidden below 820px
- Right: "Join waitlist" (`btn-primary`)
- On scroll past 40px: border-bottom transitions from transparent to `--line-2`

### 2. Hero
Full-viewport (`min-height: 100vh`). Two columns, 55% / 45%, aligned centre. Stacks to single column below 940px.

**Left — live canvas panel**
- Background: `--canvas` (`#0b0e14`) with `radial-gradient(ellipse at 50% 50%, rgba(255,255,255,0.015) 0%, transparent 60%)` — identical to the in-app canvas
- Subtle dot-grid overlay (CSS `background-image: radial-gradient`, 1px dots, `rgba(255,255,255,0.04)`, 28px spacing)
- 4 real `.node` tiles: `Webhook Trigger` → `Claude AI` → `Transform` → `HTTP Request`
  - `.node-tile`: 72×72, `border-radius: 17px`, `--surface-2` bg, 1.5px `--line` border, category-colour tint via `::before`
  - Category colours: trigger = `#4c9eff`, AI = `#8fd06f`, transform = `#c084fc`, http = `#f97316`
  - Port handles: 10px circles on left/right edges of each tile, coloured by port kind
  - Edges: SVG `<path>` bezier curves between output → input ports, `--accent` stroke, `stroke-width: 2`, `fill: none`
- **Animation sequence on load:** 
  1. Node 1 fades+slides in (0ms), edge 1–2 traces (300ms)
  2. Node 2 (400ms), edge 2–3 (700ms)
  3. Node 3 (800ms), edge 3–4 (1100ms)
  4. Node 4 (1200ms)
  - A blinking `|` cursor overlays `Claude AI` tile briefly (500ms pulse) suggesting live editing
- Canvas panel has `overflow: hidden`, slight `perspective` transform (`rotateX(2deg)`) — like peering at the app
- **Toggle bar** bottom-left of canvas: `[  Graph  ]  [  Python  ]` pill toggle
  - **Graph mode** (default): canvas shown above
  - **Python mode**: canvas fades out, Python code panel slides in showing the `Transform` node's source:
    ```python
    def run(inputs):
        df = inputs["data"]
        return {
            "filtered": df[df["status"] == "active"],
            "count": len(df)
        }
    ```
    Syntax highlighted with app colours: keywords `#4c9eff`, strings `#8fd06f`, comments `--ink-3`

**Right — copy + CTA**
- Status pill: `●  live on your infra` (green dot, mono font, accent-glow bg)
- Headline: `"Your AI agent`  
  `builds the workflow."` — Bricolage Grotesque 800, `clamp(44px, 6vw, 72px)`, tight tracking, "builds" in `--accent`→`--accent-2` gradient
- Sub (18px, `--ink-2`, max 52ch): *"Describe what you need. It writes the Python and lays it out as nodes on a canvas. Every node is real code — export it, fork it, own it."*
- Waitlist form: email `<input class="field-input">` + `<button class="btn btn-primary">Join the waitlist</button>` — inline row, stacks on mobile
- Note below form (13px, `--ink-3`): `Self-hostable · Python-native · MCP server included`
- On form submit: button shows spinner → swaps to ✓ confirmation inline, no page reload

### 3. How it works
3-column strip (`display: grid; grid-template-columns: repeat(3, 1fr)`). Dark surface card (`--surface`, `--line-2` border, `--radius`). Stack to 1 column below 700px.

| Step | Title | Body |
|------|-------|------|
| `01` | Connect your LLM via MCP | Point any OpenAI-compatible model at Nodyra's MCP server. Claude, GPT-4o, local Ollama — your choice. |
| `02` | Describe what you need | The agent writes a workflow and you see it appear as nodes on the canvas, live. |
| `03` | Every node is real Python | Inspect the source, edit it, export it. No black boxes. Your code, your environment. |

### 4. Why Nodyra — 3 pitch cards
Uses `.pcard` style (from existing brand page): `--surface` bg, `--line-2` border, `--radius`, hover lifts 3px + top-gradient line reveals.

1. **Real Python nodes** — Not a DSL. Every node is a `def run(inputs):` function. Use pandas, boto3, your internal libraries — whatever the interpreter can import.
2. **Self-hosted, your data stays** — One `docker compose up`. License verified offline, air-gap friendly. An expired license soft-downgrades; your workflows never stop.
3. **MCP in both directions** — Connect any LLM via MCP to build workflows. Expose your workflows as MCP tools so other agents can call them.

### 5. Platform capabilities — bento grid
6 cards in a 3×2 grid. Each has an icon (SVG, `--accent-glow` background), title, one-line description. Larger "featured" card treatment for the first two.

| Card | Badge |
|------|-------|
| AI & ML suite | — |
| MCP server + client | — |
| Code-first export | — |
| Sandboxed execution | `Pro` |
| Observability | `Pro` |
| Remote runners | `Pro` |

`Pro` badge: amber (`--amber`, `rgba(255,212,121,0.12)` bg).

### 6. Final waitlist CTA — full-width band
Dark panel (`--surface`, `--line` border, 18px radius, subtle blue radial glow at top-right). Centred text:
- Headline: *"Your AI builds it. Your Python runs it. You own it all."*
- Email form repeated (same markup as hero)
- Sub-note: *"All features license-gated and verified offline — no phone-home, air-gap friendly."*

### 7. Footer
`--line-2` top border, `padding: 40px 0`. Two-row: Mark B + "Nodyra" left, nav links right. Copyright below: `© 2026 Nodyra · Community edition MIT-licensed`.

---

## Colour tokens (verbatim from `apps/web/src/index.css`)

```css
--bg: #090b10; --surface: #10141c; --surface-2: #161b25; --surface-3: #1f2632;
--canvas: #0b0e14; --line: #242c3a; --line-2: #1a2029;
--ink: #e9eef6; --ink-2: #98a4ba; --ink-3: #8a93a6;
--accent: #4c9eff; --accent-2: #79b6ff; --accent-deep: #1f4f8f;
--accent-glow: rgba(76,158,255,0.18);
--amber: #ffd479; --ok: #57c98a;
--radius: 11px; --radius-sm: 7px;
--shadow: 0 18px 44px -18px rgba(0,0,0,0.78);
```

---

## Accessibility

- `prefers-reduced-motion`: all transitions and animations disabled
- Focus rings: `outline: 2px solid var(--accent); outline-offset: 2px` on all interactive elements
- Email input has `aria-label="Email address"` and associated visible label
- Nav landmark, main landmark, footer landmark
- Decorative SVG edges have `aria-hidden="true"`

---

## What's explicitly excluded

- No scroll-jacking
- No looping animations (one-shot entrance only)
- No stock photos or external images
- No third-party analytics scripts
- No pricing section (waitlist-only page, not a sales page)
- No testimonials (product is pre-launch)
