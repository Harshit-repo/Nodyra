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
    connector-curl.svg   ★ primary mark — two ports joined by a curling strand
    nodyra-n.svg           monoline lowercase "n" as one strand, port-capped
    knotted-strand.svg     strand threading through three step-nodes
    squiggle-node.svg      a node with a wavy nodyra tail (minimal)
    bowl-flow.svg          ramen bowl whose steam branches into a flow (avatar)
    favicon.svg            bolded strand on an ink tile, legible to 16px
    index.html             icon showcase — marks, dark variants, scale test, palette
  homepage/
    index.html             full self-contained landing page
```

## Viewing

Both pages are static HTML with Google-Fonts links — just open them:

- `brand/icons/index.html` — the icon system
- `brand/homepage/index.html` — the landing page

```powershell
start brand\icons\index.html
start brand\homepage\index.html
```

## Notes / next steps

- SVGs use hard-coded hex. To theme a mark with CSS, swap the fill/stroke for
  `currentColor`.
- The homepage hero has an interactive **Inspector ⇄ Python** toggle — the core
  differentiator demo. It auto-plays once on load to hint interactivity.
- All copy positions Nodyra on its own merits (Python-native execution), not as a
  clone of any other tool.
- If you adopt a distinct product brand (see the name/trademark notes), the
  wordmark in nav/footer is the only text to swap.
