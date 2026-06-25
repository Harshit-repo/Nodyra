# Noodle App Shell and Settings — Production Plan

**Status:** Reviewed and approved for Phase 1 implementation on 2026-06-22  
**Frontend review:** Senior frontend architecture audit complete  
**Product review:** Senior product-management review complete; approved with role-scope separation and dirty-form protection as blockers

## 1. Outcome

Replace the crowded wrapping top navigation and oversized account dropdown with a scalable application shell while preserving all existing routes and backend authorization:

- persistent desktop navigation for non-editor application routes;
- compact sticky command bar;
- mobile bottom navigation with an accessible More drawer;
- compact profile and organization menus;
- settings information architecture that distinguishes Account, Workspace, and Instance scope;
- truthful controls only: no profile editing, notification inbox, cloud billing, MFA, or avatar upload until supporting APIs exist;
- editor, public chat, and authentication routes remain outside the shell.

The design direction remains Noodle's current graphite/blue visual system. The generic green recommendation from the design-system search is intentionally not adopted.

## 2. Information architecture

### Primary application navigation

1. Workflows
2. Deployments
3. Executions
4. Environments
5. Runners

### Resources

1. Credentials
2. Code Library

### Management

1. Workspace / organization management
2. Team access
3. Activity log
4. Settings

### Settings scopes

- **Account:** read-only identity and browser-local appearance preferences.
- **Workspace:** selected organization identity and links to members/quotas. Workspace membership role is distinct from instance role.
- **Instance:** runtime/retention limits, installation access, audit, and plan/license. Instance controls remain admin/owner only.

## 3. Shared navigation model

A typed registry is the single source for the sidebar, mobile navigation, More drawer, breadcrumb title, active route, and command menu. Each entry carries:

- stable identifier;
- label and description;
- path and match rule;
- icon;
- section and scope (`workspace`, `resource`, `instance`, `account`);
- instance-role requirement;
- mobile priority;
- command-menu visibility.

Backend checks remain authoritative. Client visibility prevents confusing dead ends but is not a security boundary.

## 4. Responsive contract

### Desktop (>= 1024px)

- 236–248px sticky sidebar.
- 64px sticky top bar.
- content area keeps the current page containers and gains larger usable width.
- organization switcher stays in the sidebar; the profile control stays in the top bar.

### Tablet and mobile (< 1024px)

- sidebar is not rendered as a duplicate focusable landmark.
- compact top bar remains visible.
- bottom navigation contains Workflows, Deployments, Executions, Environments, and More.
- More opens a modal drawer containing Runners, Credentials, Code Library, Settings, permitted management links, and Sign out.
- bottom padding includes `env(safe-area-inset-bottom)`.
- all controls are at least 44px and remain reachable at 320px width, landscape, 200% zoom, and with a software keyboard.

## 5. Interaction and accessibility contract

- Skip-to-content link and semantic `nav`, `header`, and `main` landmarks.
- `aria-current="page"` on the unique active navigation item.
- route-aware document titles and focus movement to main content after navigation.
- visible focus rings and reduced-motion support.
- profile, organization, command, and mobile menus close on Escape, outside/backdrop click, and route change; focus returns to the trigger.
- mobile drawer locks background scroll and contains focus.
- command menu supports Cmd/Ctrl+K, arrows, Home/End, Enter, Escape, empty results, and permission-filtered navigation. Copy says “Go to pages and commands”; it does not imply federated resource search.
- icon-only buttons have accessible names; statuses include text and are not color-only.

## 6. Settings behavior

### Profile

- Render static identity, company/email, and explicitly labelled instance role.
- Render selected workspace role separately when available.
- No disabled fake form, Save button, avatar upload, password, MFA, or session controls.

### Appearance

- Theme choices become accessible visual radio cards.
- Typeface remains functional and gains previews.
- Preferences apply immediately and are explicitly labelled “this browser.”
- Storage failures degrade without breaking the page.

### Instance runtime and retention

- Admin/owner only.
- Include every backend field, including worker RSS soft budget.
- Keep numeric drafts as strings so blank input is not silently coerced to zero.
- Show units, minimum rules, helper text, restart-required state, dirty Reset/Save actions, validation, double-submit protection, and section-local error recovery.
- Guard dirty drafts on browser close, SPA navigation, and organization switch.

### Plan and license

- Use “Plan & license,” not Billing.
- Keep existing edition, expiry, limits, apply, and remove capabilities.
- Never store or log license keys.
- Remove remains visually destructive with consequence-specific confirmation.

## 7. Organization and permission safeguards

- Instance role comes from the authenticated user.
- Workspace membership role comes from `OrgInfo.role` for the selected organization.
- Labels state the scope of each role.
- Phase 1 retains full reload after organization selection because current React Query keys are not all organization-scoped. Switching returns to `/` to avoid opening an unauthorized resource in the new organization.
- Switching shows a busy state, prevents double selection, and checks the shared dirty-settings guard before reload.
- Failed organization loading is visible and retryable; zero, one, many, long, removed, and suspended organization cases are handled.
- Seamless organization switching is deferred until every organization-scoped query key includes organization identity or caches are atomically cancelled/removed.

## 8. Production states and edge cases

- Auth required/signed out, auth disabled/no user, session expiry, slow bootstrap, and sign-out failure.
- Single tenant and multi-tenant with zero/one/many organizations.
- Owner/admin/editor/viewer, plus differing workspace and instance roles.
- Community/Pro/Enterprise license states, expired/invalid license, locked entitlements.
- Loading, empty, partial failure, retry, dirty, saving, saved, and offline settings states.
- Long names/emails/organization labels, localization expansion, browser text scaling.
- Pointer, touch, keyboard-only, reduced motion, high contrast, screen reader, 200% zoom.
- Light, dark, and system themes.
- Editor, login, and public chat remain unaffected.

## 9. Delivery phases

### Phase 1 — Shell and truthful settings foundation (this implementation)

- shared registry;
- desktop sidebar and compact top bar;
- mobile bottom navigation and accessible More drawer;
- navigation-only command menu;
- compact profile/organization menus;
- settings scope labels, read-only profile, appearance cards, hardened instance settings, plan/license treatment;
- role-scope separation and dirty organization-switch protection;
- component and integration tests.

### Phase 2 — Reactive session/capability foundation

- replace direct cached-user reads with SessionContext;
- explicit workspace capability resolver;
- purposeful denied-route UI;
- organization query hook, error/retry states, storage-event policy;
- reversible runtime/build feature flag and telemetry.

### Phase 3 — Backend-integrated product features

- profile update and verified email change;
- change password and sign out all devices;
- tokenized invitations;
- persisted notification preferences and inbox before adding a bell;
- federated search across workflows/executions/deployments;
- server-synced appearance preferences.

### Phase 4 — Enterprise and cloud surfaces

- subscription/payment/invoice UI only for cloud commerce deployments;
- SSO/SAML/OIDC/SCIM, MFA, recovery codes, session inventory;
- operational notification channels, routing, quiet hours, and delivery history.

## 10. Verification gates

- `npm run typecheck`
- `npm test`
- `npm run build`
- shell/settings Playwright smoke paths at 320, 375, 768, 1024, and 1440 widths
- dark/light/system visual checks
- keyboard pass for command menu, profile menu, organization menu, and mobile drawer
- reduced-motion and 200% zoom checks
- no route regression for `/`, `/deployments`, `/executions`, `/environments`, `/runner-pools`, `/credentials`, `/code-library`, `/activity`, `/organization`, `/security`, and `/settings`
- no console errors and no content hidden beneath sticky navigation.

## 11. Deferred feature acceptance requirements

Unsupported mockup controls are intentionally excluded until their complete contracts exist:

- notification bell requires persisted inbox/read state, pagination, deduplication, retention, deep links, and mark-read APIs;
- editable profile requires validated update APIs, uniqueness/error handling, audit events, and reactive auth refresh;
- security requires password verification/recovery and session lifecycle behavior;
- cloud billing requires deployment capability detection, subscription, invoices, tax, payment, and seat-proration models;
- federated command search must never expose credential secrets or sensitive query content.

