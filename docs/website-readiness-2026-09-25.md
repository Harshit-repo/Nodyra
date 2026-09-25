# Nodyra web launch review — September 25, 2026

Reviewed the web application in this repository, starting at commit `5a5da419`,
using an isolated local API and disposable browser-test database. No public
website URL was provided. This review does not certify a public deployment.

## Findings addressed

1. **P1 — Python editor blocked in production.** The default
   `@monaco-editor/react` loader fetched executable code from jsDelivr, while
   the shipped nginx policy allows scripts only from the application origin.
   The Python editor and worker are now bundled with the application and loaded
   when the full editor opens. Monaco receives its accessible label through its
   supported `ariaLabel` option. The icon button that opens the full editor also
   now has a descriptive accessible name and sits above the highlighted
   textarea, which previously intercepted mouse clicks on the control.
   Monaco uses its stable textarea input so canvas shortcuts no longer consume
   spaces or the letter `d` while typing code.
2. **P1 — Page errors survived navigation.** React can reuse the same error
   boundary for sibling routes, leaving the fallback visible even after clicking
   “Back to workflows.” Page boundaries now reset on navigation; regression
   coverage exercises the recovery link and subsequent sidebar navigation,
   while preserving healthy form state during query-string navigation.
3. **P2 — Invalid public-chat URLs rendered an empty page.** Nested or malformed
   chat paths now show the existing not-found screen and a route back to the
   workspace, including when signed out.
4. **Release coverage gap.** CI now builds the web app before running browser
   tests against Vite's production preview. New regression coverage applies the
   CSP read directly from `nginx.conf`, blocks external scripts, edits Python,
   and checks that Monaco is absent until requested.

Bundling Monaco exposes a download that was previously outside the bundle
report. Its approximately 924 kB gzip runtime and 86 kB worker are now counted.
The Monaco and total-download limits are 1 MB and 2.1 MB; the other limits are
unchanged. A shared Vite preload helper has its own chunk so eager routes cannot
accidentally pull Monaco into startup. The build gate also rejects Monaco or
Plotly scripts/styles referenced by the initial HTML.

## Verification

- Initial frontend suite: **548 passed, 1 skipped**, across 98 files.
- Final focused error-boundary, authentication, and node-details regression
  suite: **11 passed**.
- Existing development-server browser suite: **11 passed**.
- Existing browser journeys against production assets: **11 passed**.
- Final production regression scenarios: **2 passed**, covering full Python
  editor interaction under the shipped CSP and unknown public-chat recovery.
  Earlier failed iterations exposed the editor control's click obstruction and
  canvas shortcuts consuming text; both are fixed in the passing final run.
- Production build includes TypeScript checking and enforced gzip budgets.
- Full npm audit after the dependency change: **0 reported vulnerabilities**.
- The browser suite checks 11 workspace pages at 1440px and 390px in light and
  dark themes, page overflow, uncaught page errors, and serious/critical axe
  findings. This is automated coverage, not a full WCAG certification.
- Visual inspection covered sign-in, workflow desktop/mobile layouts, execution
  status, and settings. The established interface was preserved.
- Impeccable's detector reported no findings in the changed UI files.

Local build/audit logs are in `.tmp/website-build.log` and
`.tmp/website-npm-audit.json`. Unit regression results are in
`.tmp/website-regressions.log`. Browser logs are in
`.tmp/website-production-e2e.log` and `.tmp/website-resilience-e2e.log`;
Playwright keeps page captures and failure
traces under `apps/web/test-results`. These local artifacts are not committed.

Run the production browser lane locally after `npm run build`:

```powershell
cd apps/web
$env:E2E_PRODUCTION = '1'
npm run test:e2e
```

The preview lane exercises compiled assets with a real local API. It does not
validate nginx proxy behavior, public TLS, PostgreSQL/Redis topology, or cloud
storage. The existing infrastructure release gates still apply.

## Before public launch

Provide the intended public URL and qualify its actual deployment: HTTPS,
trusted proxy settings, authentication/session behavior, production attestation,
backup/restore, sandbox execution, monitoring, and rollback. Re-run release CI
on the chosen source revision and retain its image identities and evidence.
See [the prior launch report](launch-readiness-2026-09-05.md) and
[the hosting runbook](deployment/launch-hosting.md). Earlier results in those
documents have not been re-certified by this web-only review.

Optional maintenance: Impeccable reported older product metadata and an unset
design build preference; its `init` workflow owns that refresh. Those unrelated
project files were left unchanged.

Other release and container files changed concurrently during this review.
Those changes were preserved and are outside this web review's verification.
