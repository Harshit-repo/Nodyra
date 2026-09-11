# Nodyra — brand assets

Generated brand kit for the Nodyra workflow-automation platform.

## Aesthetic direction

The **`homepage/`** matches the live product theme (`apps/web/src/index.css`) verbatim:
**black & blue, dark dashboard.** Near-black background with subtle blue radial glows,
a blue accent (`#4c9eff` → `#79b6ff` gradient), and the app's own nodyra-strand logo.
Amber (`#ffd479`) appears only on expression (ƒx) fields, exactly as in the editor.
Fonts: **Bricolage Grotesque** (display) · **Hanken Grotesk** (body) · **IBM Plex Mono** (code).

The standalone **`icons/`** kit explores a warmer alternate "paper & ink" direction
(amber/coral on wheat) — kept for reference. The unifying motif across both is **the
nodyra-strand that doubles as a workflow edge**.

## Palette — homepage (matches the app)

| Token | Hex | Use |
|-------|-----|-----|
| `--bg` | `#090b10` | background |
| `--surface` | `#10141c` | cards / panels |
| `--accent` | `#4c9eff` | primary blue / the strand |
| `--accent-2` | `#79b6ff` | gradient top / output ports |
| `--ink` | `#e9eef6` | text |
| `--amber` | `#ffd479` | expression (ƒx) fields only |
| `--ok` | `#57c98a` | success / "ready" states |

## Palette — icon kit (alternate, warm)

| Token | Hex | Use |
|-------|-----|-----|
| `--ink` | `#1C1A17` | text, nodes |
| `--amber` | `#E29A2C` | accent / the strand |
| `--coral` | `#E0573E` | output ports |
| `--paper` | `#F6F1E7` | background |

## Contents

```
brand/
  icons/
    nodyra-app-mark.svg     ★ app mark — the white-N tile used by the live app
                             (traced from apps/web/public/brand/nodyra-mark.png)
    connector-curl.svg       two ports joined by a curling strand
    nodyra-n.svg             monoline lowercase "n" as one strand, port-capped
    knotted-strand.svg       strand threading through three step-nodes
    squiggle-node.svg        a node with a wavy nodyra tail (minimal)
    bowl-flow.svg            ramen bowl whose steam branches into a flow (avatar)
    favicon.svg              bolded strand on an ink tile, legible to 16px
    index.html               icon showcase — marks, dark variants, scale test, palette
  homepage/
    nodyra.html              ★ live brand page — full landing page with the app mark,
                             How it works, Platform, Integrations, Documentation,
                             Environments, Runners, Enterprise, Pricing
    index.html               earlier self-contained landing page (kept for reference)
  docs.html                  ★ documentation site — sidebar-navigated, searchable
                             single-page app (hash-routed) covering getting started,
                             MCP quickstart, nodes, architecture, deployment, GitOps,
                             licensing, security, recipes, and the status matrix
```

## Viewing

These pages are static HTML with Google-Fonts links — just open them:

- `brand/icons/index.html` — the icon system
- `brand/homepage/nodyra.html` — the live brand page
- `brand/docs.html` — the documentation site (also linked from the brand page)

```powershell
start brand\icons\index.html
start brand\homepage\nodyra.html
start brand\docs.html
```

## Notes / next steps

- The brand page header/footer logo is the **app mark** (`nodyra-app-mark.svg`):
  a white "N" on a `#1456D0` squircle tile, traced from the PNG the app itself
  renders (`apps/web/public/brand/nodyra-mark.png`) and verified against it
  pixel-for-pixel (sub-pixel fidelity at any size).
- The page favicon is the same mark inlined as data URIs (SVG + 32px PNG fallback),
  so the tab matches the app icon.
- The homepage hero has an interactive **Inspector ⇄ Python** toggle — the core
  differentiator demo. It auto-plays once on load to hint interactivity.
- Feature claims are grounded in the repo: 500+ nodes (≈650 node/spec definitions
  in `packages/nodes`), 40+ v2 providers (`integrations_v2/providers`), chat
  trigger + hosted chat pages, API Endpoint node, durable queue with dead-letter
  replay, datasets + DuckDB, CLI/Python client, Helm chart, and 16 templates.
- `docs.html` is a self-contained docs site: sidebar groups, client-side search
  with ⌘K, on-page TOC, prev/next, copy buttons, mobile drawer. Its content is
  condensed from the real markdown in `docs/` and `SECURITY.md`, and each page
  links back to its source on GitHub. The brand page's Documentation section
  routes into it via `docs.html#/<page>` hashes.
- All copy positions Nodyra on its own merits (Python-native execution), not as a
  clone of any other tool.
- If you adopt a distinct product brand (see the name/trademark notes), the
  wordmark in nav/footer is the only text to swap.
