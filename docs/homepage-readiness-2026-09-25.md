# Nodyra standalone homepage review

Scope: `brand/homepage/nodyra.html`. Local improvements only; no deployment or outbound email was performed.

## Changes

- Rebalanced the hero around the value proposition and a complete, responsive workflow example; simplified navigation and improved typography, spacing, action contrast, and mobile touch targets.
- Replaced the simulated waitlist success with explicit email links to `sharma.har97@gmail.com`, as requested. Self-hosting actions open the included getting-started documentation. No email address is collected or stored by the page.
- Made all four graph nodes visible immediately and redrew connections when the layout changes. Graph/Python selection now exposes the correct state to assistive technology.
- Added modal focus containment, Escape dismissal, focus restoration, arrow-key tab navigation, named fields and tree controls, and accurate selected/expanded states.
- Fixed duplicate IDs shared by input/output trees and handled clipboard denial by showing selectable raw text.
- Labeled static environment and runner previews and disabled their nonfunctional controls. Removed the unused resize affordance and fake signup handler.
- Kept section content visible without JavaScript or animation support. Removed the incorrect zero-dollar Enterprise offer from structured data; custom pricing now matches the visible pricing card.

## Validation

From `apps/web`:

```powershell
npx playwright test --config e2e/homepage/playwright.config.ts
```

13 browser checks passed: layouts at 320, 390, 768, 1024, and 1366px; complete graph visibility; inspector tab bounds; documentation navigation; email destinations; view toggles; keyboard focus; mobile menu; independent data trees; clipboard denial; and JavaScript-disabled content. Automated axe checks reported no WCAG A/AA violations on the default page and open Fetch Data inspector. These checks supplement, rather than replace, manual accessibility review.

Desktop and mobile browser inspection covered the hero, graph, inspector, and lower-page content. `git diff --check` passed. The Impeccable source detector still flags existing stylistic conventions in the product previews; those are not a certification of accessibility or launch readiness.

## Remaining launch checks

- Verify the actual deployed origin, TLS, compression, caching, security headers, and that the hosting entry point serves this page rather than the separate `index.html`.
- Confirm commercial and feature claims before publication. Existing copy was preserved: in particular, the Enterprise section says SSO and audit logs ship with every installation, while pricing presents SSO/audit export as Enterprise features and shows them excluded from Pro. The included feature/status docs should be the source of truth.
- Email requests require a configured mail client; the address is also printed so visitors can copy it. There is no automatic mailing list subscription.
- This pass did not validate every external GitHub URL or production service integration.
