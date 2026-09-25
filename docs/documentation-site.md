# Documentation website

The website opens directly into the documentation. Its guides are generated
from the repository Markdown, so GitHub and the website share the same source.
The product tour GIF appears in both the repository README and the docs landing
page. The original interactive product page remains at `nodyra.html`.

## Build and preview

From the repository root, with Node.js 22 and Python installed:

```sh
npm ci --prefix apps/web
node scripts/build_docs_site.mjs
node scripts/build_docs_site.mjs --check
python -m http.server 5187 --bind 127.0.0.1 --directory brand/homepage
```

Open <http://localhost:5187>. No API, account, database, or Docker service is
needed to read the site. Search and guide navigation run in the browser; the
landing page and GitHub guide links remain available without JavaScript.

Edit the Markdown guides, `brand/homepage/docs.template.html`, or
`brand/homepage/docs-site.js`, then rebuild. Commit the generated `index.html`
and `docs.html` along with their sources. The latter preserves older guide links.
The build checks internal page links and section anchors for the included guides.
Guides outside the website's curated list link to their GitHub source.

## Export to a static host

```sh
node scripts/build_docs_site.mjs --out .tmp/nodyra-docs-site
```

Upload the **contents** of that output folder as the website root. This export
contains only the static HTML, browser script, and tour assets. Never serve the
repository root or a folder containing `deploy/.env`. Relative assets work at
both a domain root and a project prefix such as `/Nodyra/`.

## GitHub Pages

The `Documentation Pages` workflow builds and checks the site on relevant pull
requests. After the repository is public, enable **Settings → Pages → Build and
deployment → Source: GitHub Actions**, then run the workflow on `main`.
The workflow deliberately skips deployment while the repository is private.
It does not change repository visibility or configure a custom domain.

The expected project URL is <https://harshit-repo.github.io/Nodyra/> once Pages
has been enabled and a deployment succeeds. A prepared workflow does not mean
the site is already live. If GitHub Actions is unavailable because of account
billing, publish the locally exported folder on a static host or resolve the
account issue before using that workflow.

## Verify changes

```sh
node scripts/build_docs_site.mjs --check
cd apps/web
npx playwright test --config e2e/homepage/playwright.config.ts
```

These browser checks cover documentation routes, the tour asset, search,
clipboard fallback, mobile layout, accessibility, and the product page.
