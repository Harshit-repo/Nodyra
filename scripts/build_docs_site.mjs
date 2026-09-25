/** Build the static documentation from the same Markdown GitHub displays. */
import { readFile, writeFile, mkdir, copyFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'apps/web/package.json'));
const { marked } = require('marked');
const { JSDOM } = require('jsdom');
const purify = require('dompurify')(new JSDOM('').window);
const sourceDir = path.join(root, 'brand/homepage');
const args = process.argv.slice(2);
if (args.some((arg, i) => arg !== '--check' && arg !== '--out' && args[i - 1] !== '--out') || (args.includes('--out') && !args[args.indexOf('--out') + 1])) {
  throw new Error('Usage: node scripts/build_docs_site.mjs [--check] [--out DIRECTORY]');
}
const check = args.includes('--check');
const out = args.includes('--out') ? path.resolve(root, args[args.indexOf('--out') + 1]) : sourceDir;
const repo = 'https://github.com/Harshit-repo/Nodyra';
// Only public product guides belong in the website. Internal launch notes and
// operator evidence are deliberately not discovered or copied recursively.
const guides = [
  ['welcome', 'Nodyra documentation', 'Start here', 'docs/README.md'],
  ['getting-started', 'Getting started', 'Start here', 'docs/getting-started.md'],
  ['self-hosted', 'Install on one host', 'Start here', 'docs/deployment/self-hosted.md'],
  ['release-1-0-5', 'Release 1.0.5', 'Start here', 'docs/releases/1.0.5.md'],
  ['mcp-quickstart', 'MCP quickstart', 'Build workflows', 'docs/mcp-quickstart.md'],
  ['nodes', 'Writing nodes', 'Build workflows', 'docs/nodes.md'],
  ['datasetref', 'Working with datasets', 'Build workflows', 'docs/datasetref.md'],
  ['workflow-templates', 'Workflow templates', 'Build workflows', 'docs/workflow-templates.md'],
  ['recipes', 'Recipes', 'Build workflows', 'docs/recipes.md'],
  ['academy', 'Nodyra Academy', 'Build workflows', 'docs/academy.md'],
  ['export-modes', 'Export workflows', 'Build workflows', 'docs/export-modes.md'],
  ['gitops', 'GitOps', 'Build workflows', 'docs/gitops.md'],
  ['mcp', 'MCP reference', 'Build workflows', 'docs/mcp.md'],
  ['migration', 'Import and migration', 'Build workflows', 'docs/migration.md'],
  ['operations', 'Operations guide', 'Operate Nodyra', 'docs/operations/README.md'],
  ['deployment', 'Deployment reference', 'Operate Nodyra', 'docs/deployment.md'],
  ['backup-restore', 'Backup and restore', 'Operate Nodyra', 'docs/backup-restore.md'],
  ['workers', 'Workers and scaling', 'Operate Nodyra', 'docs/deployment/workers.md'],
  ['mcp-gateway', 'MCP gateway', 'Operate Nodyra', 'docs/mcp-gateway.md'],
  ['connect-mcp', 'Connect an MCP client', 'Build workflows', 'docs/connect-mcp.md'],
  ['security', 'Security', 'Operate Nodyra', 'SECURITY.md'],
  ['architecture-chooser', 'Choose a topology', 'Project reference', 'docs/architecture-chooser.md'],
  ['architecture', 'Architecture', 'Project reference', 'docs/architecture.md'],
  ['status-matrix', 'Feature status', 'Project reference', 'docs/status-matrix.md'],
  ['licensing', 'Licensing', 'Project reference', 'docs/licensing.md'],
  ['subscriptions', 'Paid licenses and renewal', 'Project reference', 'docs/subscriptions.md'],
  ['release-policy', 'Release policy', 'Project reference', 'docs/release-policy.md'],
  ['documentation-site', 'Publish this website', 'Project reference', 'docs/documentation-site.md'],
];
const bySource = new Map(guides.map(([id, , , source]) => [source, id]));
const esc = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
const slug = value => value.toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/g, '-');
const pages = [];
const anchorSets = new Map();
const routeLinks = [];
for (const [id, title, group, source] of guides) {
  const markdown = (await readFile(path.join(root, source), 'utf8')).replace(/^# .+\r?\n/, '');
  const renderer = new marked.Renderer();
  renderer.code = ({ text, lang }) => `<div class="dcode"><div class="dcode-head"><span class="dcode-lang">${esc(lang || 'text')}</span><button class="dcode-copy" type="button">Copy</button></div><pre tabindex="0"><code>${esc(text)}</code></pre></div>`;
  const document = new JSDOM(purify.sanitize(marked.parse(markdown, { renderer }), { ADD_ATTR: ['tabindex'] })).window.document;
  const headings = [];
  const used = new Map();
  for (const heading of document.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
    const base = slug(heading.textContent);
    const count = used.get(base) || 0;
    used.set(base, count + 1);
    const anchor = base + (count ? `-${count}` : '');
    heading.id = `sec-${anchor}`;
    if (heading.tagName === 'H2') headings.push({ id: anchor, title: heading.textContent });
  }
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(href)) continue;
    const [relative, hash] = href.split('#');
    const resolved = relative ? path.posix.normalize(path.posix.join(path.posix.dirname(source), decodeURIComponent(relative))) : source;
    const target = bySource.get(resolved);
    a.setAttribute('href', target ? `#/${target}${hash ? `/${hash}` : ''}` : `${repo}/blob/main/${resolved}${hash ? `#${hash}` : ''}`);
    if (target) routeLinks.push({ source, href: a.getAttribute('href') });
  }
  for (const img of document.querySelectorAll('img')) {
    const src = img.getAttribute('src');
    if (src === 'nodyra-product-tour.gif') {
      img.setAttribute('src', 'assets/nodyra-product-tour.gif');
      img.setAttribute('width', '1440'); img.setAttribute('height', '720');
      img.className = 'tour-animation';
      const figure = document.createElement('figure');
      figure.className = 'product-tour';
      img.replaceWith(figure);
      figure.append(img);
      const still = img.cloneNode();
      still.className = 'tour-still'; still.src = 'assets/nodyra-product-tour.png';
      figure.append(still);
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'tour-control'; button.textContent = 'Pause product tour';
      button.setAttribute('aria-pressed', 'false');
      figure.append(button);
    } else if (src && !/^(?:https?:|data:)/i.test(src)) {
      img.setAttribute('src', `${repo}/raw/main/${path.posix.normalize(path.posix.join(path.posix.dirname(source), src))}`);
    }
  }
  pages.push({ id, title, group, source, headings, html: document.body.innerHTML });
  anchorSets.set(id, new Set([...document.querySelectorAll('[id]')].map(el => el.id)));
  document.defaultView.close();
}
// Broken local routes or section fragments are build failures, not silent fallbacks.
for (const { source, href } of routeLinks) {
  const [, id, anchor] = href.split('/');
  const target = anchorSets.get(id);
  if (!target || (anchor && !target.has(`sec-${decodeURIComponent(anchor)}`))) {
    throw new Error(`Broken documentation link in ${source}: ${href}`);
  }
}
const first = pages[0];
const nav = pages.map(p => `<a class="ds-link" href="${repo}/blob/main/${p.source}">${esc(p.title)}</a>`).join('\n');
const fallbackDocument = new JSDOM(first.html).window.document;
for (const a of fallbackDocument.querySelectorAll('a[href^="#/"]')) {
  const [, id, hash] = a.getAttribute('href').split('/');
  a.href = `${repo}/blob/main/${pages.find(p => p.id === id).source}${hash ? `#${hash}` : ''}`;
}
const landing = `<h1 class="dpage-title">${esc(first.title)}</h1><article class="dsec">${fallbackDocument.body.innerHTML}</article>`;
fallbackDocument.defaultView.close();
const template = (await readFile(path.join(sourceDir, 'docs.template.html'), 'utf8')).replace(/\r\n/g, '\n');
const html = '<!-- Generated by scripts/build_docs_site.mjs. Edit Markdown, docs.template.html, or docs-site.js. -->\n' + template
  .replace('{{LANDING}}', () => landing)
  .replace('{{NAV}}', () => nav)
  .replace('{{TOC}}', () => first.headings.map(h => `<a href="#sec-${h.id}">${esc(h.title)}</a>`).join(''))
  .replace('{{PAGES}}', () => JSON.stringify(pages).replace(/</g, '\\u003c'));
if (!check) await mkdir(path.join(out, 'assets'), { recursive: true });
// The product page is the public entry point; documentation has its own URL.
const productHtml = (await readFile(path.join(sourceDir, 'nodyra.html'), 'utf8')).replace(/\r\n/g, '\n');
for (const [name, content] of [['index.html', productHtml], ['docs.html', html]]) {
  const target = path.join(out, name);
  if (check) {
    if (await readFile(target, 'utf8') !== content) throw new Error(`${target} is stale; run node scripts/build_docs_site.mjs`);
  } else await writeFile(target, content);
}
const assets = [
  ['docs/nodyra-product-tour.gif', 'assets/nodyra-product-tour.gif'],
  ['docs/nodyra-product-tour.png', 'assets/nodyra-product-tour.png'],
  ['brand/homepage/docs-site.js', 'docs-site.js'],
  ['brand/homepage/nodyra.html', 'nodyra.html'],
];
for (const [source, target] of assets) {
  if (check) {
    if (!(await readFile(path.join(root, source))).equals(await readFile(path.join(out, target)))) throw new Error(`${target} is stale`);
  } else if (path.resolve(root, source) !== path.resolve(out, target)) await copyFile(path.join(root, source), path.join(out, target));
}
if (!check) await writeFile(path.join(out, '.nojekyll'), '');
console.log(`${check ? 'Verified' : 'Built'} ${pages.length} documentation pages and local tour assets in ${out}`);
