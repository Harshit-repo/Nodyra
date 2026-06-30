# Nodyra — Claude Design Prompts

Copy either prompt directly into Claude Design (claude.ai/design or the Design tab).

---

## 1. Landing Page

```
Design a marketing landing page for **Nodyra** — an AI-native workflow builder aimed at developers and engineering teams who self-host their infrastructure.

**Product in one sentence:** Connect any LLM via MCP; it writes Python workflows as visual node graphs on a canvas — and if an integration is missing, it writes that node in Python too.

**Target audience:** Senior developers, data engineers, platform teams. They are skeptical of marketing fluff and respond to technical precision and control. They self-host because they care about data sovereignty.

**Page sections (in order):**
1. Nav — logo + links (AI Agent, Platform, Pricing, Self-host) + "Get started" CTA
2. Hero — main headline + sub-copy + two CTAs + a code/UI demo card (inspector pane on left showing a node's Python source)
3. "How it works" — 3-step flow: LLM connects via MCP → workflow appears as nodes → missing node? it writes one
4. Why Python-native — 3 pitch cards: Real Python nodes / Runs in your environments / Self-hosted, your data stays
5. Platform capabilities grid — 6 cards (AI & ML suite, MCP both directions, Code-first export, Sandboxed execution [Pro], Observability [Pro], Remote runners [Pro])
6. Enterprise controls band — a dark panel listing RBAC / encrypted vault / audit log / cron triggers / retry / retention
7. Pricing — 3 tiers: Community $0, Pro $49/mo (featured/highlighted), Enterprise custom
8. Self-host terminal — docker compose one-liner with green success output
9. Final CTA — "Your AI builds it. Your Python runs it. You own it all."
10. Footer — logo, nav links, copyright

**Visual identity:**
- **Background:** #090b10 (near-black, very dark navy)
- **Surface layers:** #10141c, #161b25, #1f2632 (elevation steps)
- **Accent blue:** #4c9eff (primary), #79b6ff (lighter), #1f4f8f (deep/border)
- **Accent glow:** rgba(76,158,255,0.18)
- **Amber highlight:** #ffd479 (used for Pro tier badges and expression syntax)
- **Green:** #57c98a (success states, active indicators)
- **Text:** #e9eef6 (primary), #98a4ba (secondary), #5d6878 (tertiary/muted)
- **Borders:** #242c3a (primary), #1a2029 (subtle)

**Typography:**
- Display/headings: Bricolage Grotesque, weight 800, tight tracking (-0.03em), available on Google Fonts
- Body: Hanken Grotesk, weights 400–600, also on Google Fonts
- Monospace (code, labels, pills): IBM Plex Mono, weights 400–600

**Logo mark:** A flowing S-curve path between two filled circles (one at bottom-left, one at top-right), representing a data flow between two workflow nodes. The curve uses the accent blue. The wordmark "Nodyra" is set in Bricolage Grotesque 800 at -0.045em tracking.

**Tone:** Precise and confident, not salesy. Write like a technically excellent team shipping something they're proud of. No meaningless superlatives. Every claim backed by a concrete feature.

**Key copy to include:**
- Hero headline: "Your AI agent builds the workflow." with "workflow" or a key word in the brand blue gradient
- Hero sub: "Connect any LLM via MCP. It writes Python workflows and you see them as nodes on a canvas. Any integration missing? It writes that node too — in a Code node, right on the graph."
- Hero note below CTAs: "Runs on your infra. Self-hostable · Python-native · MCP server included. One docker compose up away."
- Pricing footer: "All paid features are license-gated, verified offline — no phone-home, air-gap friendly. An expired license soft-downgrades to Community; your workflows never stop running."

**Design aesthetic risk to take:** The hero demo card should show a real code tab (Python source for an AI-generated node) and an inspector tab (visual parameter panel) with a toggle between them — make that interactive demo widget the most polished element on the page. Let it feel like using the actual app.

**Constraints:**
- Mobile responsive down to 375px
- No unnecessary animation — one scroll-reveal pass on sections, nothing more
- Reduced-motion should be respected
- No stock photos or illustrations — only geometric/abstract SVG art and code snippets
- The page should feel premium but approachable, not corporate SaaS
```

---

## 2. Sign-in Page

```
Design a sign-in page for **Nodyra** — an AI-native workflow builder for developers. The sign-in page is the first thing users see after visiting the marketing site and deciding to self-host or access a shared instance.

**Context:** This is a developer tool. The sign-in form is part of the self-hosted web app (not a SaaS login). Users are engineers who value clarity and precision over delight animations.

**Page elements:**
- Nodyra logo mark + wordmark (top-left or centered)
- Page title: "Sign in to Nodyra" or just "Welcome back"
- Email address field
- Password field (with show/hide toggle)
- "Remember me" checkbox
- "Forgot your password?" link (right-aligned, near password field)
- Primary CTA: "Sign in" button (full-width, brand blue)
- Divider: "or continue with"
- GitHub OAuth button (icon + "Continue with GitHub")
- Footer link: "No account? Self-host Nodyra →" (links to docs, not a signup flow — this is self-hosted software)
- Subtle: "Protected by Nodyra's Fernet-encrypted credential vault" or similar trust signal

**Visual identity (same as landing page):**
- Background: #090b10
- Surface card: #10141c with border #242c3a
- Accent blue: #4c9eff / #79b6ff
- Text: #e9eef6 primary, #98a4ba secondary
- Green: #57c98a (for success state on login)
- Amber: #ffd479 (error/warning states only if needed)
- Border radius: 11px cards, 7px inputs

**Typography:**
- Bricolage Grotesque 800 for the wordmark and page title
- Hanken Grotesk 400–600 for labels, inputs, CTAs
- IBM Plex Mono for any monospace hints (e.g., placeholders like "user@company.io")

**Layout options to explore:**
- Option A: Centered card on a dark background with a subtle blue radial gradient bloom behind the card. Card is ~400px wide, vertically centered on the page. Left side blank (or ambient) — simple and focused.
- Option B: Two-column split: left panel shows the Nodyra logo + a rotating testimonial/feature highlight (e.g., "Your AI built 3 workflows this week" with a mini node-graph visual); right panel is the form. Dark throughout.

Prefer **Option B** if you can make the left panel feel genuinely useful (a small live node-graph animation or a code snippet showing what an agent built), otherwise go with **Option A** for clarity.

**Error/validation states to show:**
- Field-level: red border + icon + message below ("Password must be at least 8 characters")
- Server-level: top-of-form banner "Invalid email or password. Please try again."
- Loading state: button shows a spinner, disabled

**Tone:** The form copy should be direct and literal. "Sign in" not "Log in." Labels are sentence case, not all caps. Placeholder text is real example format, not "Enter your email here."

**Aesthetic risk:** The GitHub button should feel native to the dark theme — a slightly lighter surface with the GitHub Octocat mark in white, not the stock white/black pill button you see everywhere. Make it belong.

**Constraints:**
- Accessible: visible focus rings, ARIA labels on inputs
- Mobile: stack to single column, card becomes full-width with 24px padding
- No illustrations, no stock art
- The overall feel: a premium developer tool you're glad you're logging into, not a corporate HR portal
```

---

## Logo Direction Notes for Claude Design

When referencing the Nodyra logo in either prompt, describe it as:

> "A monoline S-curve path (bezier arc) between two filled circles — one at bottom-left, one at top-right — drawn in accent blue (#4c9eff to #79b6ff gradient). The circles represent workflow nodes; the arc represents the data flow between them. Minimal, reads well at 20px."

Three mark concepts to reference or choose from:

- **Mark A (Sinuous N):** N letterform where the diagonal stroke is a bezier S-curve gradient; node circles at all four corners. Most literal to the product name.
- **Mark B (Pulse Arc):** Single flowing S-curve with a large input node, small output node, and an amber midpoint accent dot. Most expressive, most distinctive at favicon size.
- **Mark C (Graph N):** Four corner nodes connected by graph edges tracing the letter N, with a hollow center node on the diagonal. Most "technical graph" feeling.

Current production mark is closest to **Mark B** — use that as the default unless specifying otherwise.
