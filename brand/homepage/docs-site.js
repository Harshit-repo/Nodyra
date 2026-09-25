'use strict';
(() => {
  const pages = JSON.parse(document.getElementById('docs-data').textContent);
  const byId = new Map(pages.map(page => [page.id, page]));
  const body = document.getElementById('doc-body');
  const main = document.getElementById('doc-content');
  const nav = document.getElementById('ds-nav');
  const toc = document.getElementById('dtoc-links');
  const input = document.getElementById('dt-search-input');
  const results = document.getElementById('dt-results');
  const burger = document.getElementById('dt-burger');
  const sidebar = document.getElementById('dside');
  const status = document.getElementById('docs-status');
  const mobile = matchMedia('(max-width:900px)');
  const reduceMotion = matchMedia('(prefers-reduced-motion:reduce)');
  const repo = 'https://github.com/Harshit-repo/Nodyra';
  const escape = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  let selected = -1;
  let matches = [];
  let currentId = '';
  let tourPaused = reduceMotion.matches;
  const searchable = pages.map(page => {
    const document = new DOMParser().parseFromString(page.html, 'text/html');
    return { page, text: document.body.textContent.toLowerCase() };
  });

  function closeSearch() {
    results.classList.remove('open');
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    matches = [];
    selected = -1;
  }
  function closeNav() {
    document.body.classList.remove('nav-open');
    burger.setAttribute('aria-expanded', 'false');
    sidebar.inert = mobile.matches;
    main.inert = false;
  }
  function buildNav(active) {
    let group = '';
    nav.innerHTML = pages.map(page => {
      let heading = '';
      if (group !== page.group) {
        heading = `${group ? '</div>' : ''}<div class="ds-group"><div class="ds-group-label">${escape(page.group)}</div>`;
        group = page.group;
      }
      return `${heading}<a class="ds-link${page.id === active ? ' active' : ''}" href="#/${page.id}"${page.id === active ? ' aria-current="page"' : ''}><span class="ds-dot" aria-hidden="true"></span>${escape(page.title)}</a>`;
    }).join('') + '</div>';
  }
  function updateTour() {
    document.querySelectorAll('.product-tour').forEach(figure => {
      figure.classList.toggle('tour-paused', tourPaused);
      const button = figure.querySelector('.tour-control');
      button.textContent = tourPaused ? 'Play product tour' : 'Pause product tour';
      button.setAttribute('aria-pressed', String(tourPaused));
    });
  }
  function route(focus = false) {
    const raw = location.hash.replace(/^#\/?/, '');
    if (raw === 'doc-content') { main.focus(); return; }
    // Native section links in the prerendered, no-JavaScript landing page.
    if (raw.startsWith('sec-')) return;
    const [id = 'welcome', anchor] = raw.split('/');
    const page = byId.get(id || 'welcome');
    if (!page) {
      document.title = 'Page not found — Nodyra Docs';
      body.innerHTML = '<h1 class="dpage-title">Page not found</h1><div class="dsec"><p>This guide may have moved. Search the documentation or <a href="#/welcome">return to the documentation home</a>.</p></div>';
      toc.innerHTML = '';
      currentId = '';
      buildNav('');
    } else if (currentId !== page.id) {
      document.title = `${page.title} — Nodyra Docs`;
      body.innerHTML = `<h1 class="dpage-title">${escape(page.title)}</h1><article class="dsec">${page.html}</article>`;
      toc.innerHTML = page.headings.map(h => `<a href="#/${page.id}/${h.id}" data-section="sec-${h.id}">${escape(h.title)}</a>`).join('') +
        `<div class="dtoc-edit"><a href="${repo}/blob/main/${page.source}">Edit on GitHub</a><a href="${repo}/issues">Report an issue</a></div>`;
      const index = pages.indexOf(page);
      body.insertAdjacentHTML('beforeend', '<nav class="dpager" aria-label="Adjacent guides">' +
        (index > 0 ? `<a href="#/${pages[index - 1].id}"><span class="dp-dir">Previous</span><span class="dp-title">${escape(pages[index - 1].title)}</span></a>` : '<span></span>') +
        (index + 1 < pages.length ? `<a class="dp-next" href="#/${pages[index + 1].id}"><span class="dp-dir">Next</span><span class="dp-title">${escape(pages[index + 1].title)}</span></a>` : '') + '</nav>');
      currentId = page.id;
      buildNav(page.id);
      updateTour();
    }
    closeNav();
    closeSearch();
    if (focus) main.focus({ preventScroll: true });
    let target;
    try { target = anchor && document.getElementById(`sec-${decodeURIComponent(anchor)}`); } catch { /* Invalid fragment: show the guide from its start. */ }
    if (target) target.scrollIntoView({ behavior: 'instant' });
    else window.scrollTo({ top: 0, behavior: 'instant' });
  }
  input.addEventListener('input', () => {
    const query = input.value.trim().toLowerCase();
    closeSearch();
    if (query.length < 2) return;
    matches = searchable.filter(({ page, text }) => page.title.toLowerCase().includes(query) || text.includes(query))
      .sort((a, b) => Number(b.page.title.toLowerCase().includes(query)) - Number(a.page.title.toLowerCase().includes(query)))
      .slice(0, 8).map(hit => hit.page);
    results.innerHTML = matches.length ? matches.map((page, index) =>
      `<div class="dr-item" id="search-option-${index}" role="option" aria-selected="false" data-index="${index}"><span class="dr-title">${escape(page.title)}</span><span class="dr-sub">${escape(page.group)}</span></div>`).join('') : '<div class="dr-empty">No matching guides. Try “install”, “MCP”, or “backup”.</div>';
    results.classList.add('open');
    input.setAttribute('aria-expanded', 'true');
    status.textContent = matches.length ? `${matches.length} matching guides` : 'No matching guides';
  });
  function selectResult(index) {
    if (!matches[index]) return;
    const id = matches[index].id;
    input.value = '';
    closeSearch();
    if (location.hash === `#/${id}`) route(true);
    else location.hash = `#/${id}`;
  }
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { closeSearch(); return; }
    if (!matches.length) return;
    if (event.key === 'Enter') { event.preventDefault(); selectResult(Math.max(selected, 0)); }
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      selected = event.key === 'ArrowDown' ? (selected + 1) % matches.length : (selected <= 0 ? matches.length : selected) - 1;
      results.querySelectorAll('[role=option]').forEach((option, index) => {
        option.setAttribute('aria-selected', String(index === selected));
        option.classList.toggle('sel', index === selected);
      });
      input.setAttribute('aria-activedescendant', `search-option-${selected}`);
    }
  });
  results.addEventListener('click', event => {
    const option = event.target.closest('[data-index]');
    if (option) selectResult(Number(option.dataset.index));
  });
  document.addEventListener('click', async event => {
    if (!event.target.closest('.dt-search')) closeSearch();
    if (event.target.closest('.tour-control')) { tourPaused = !tourPaused; updateTour(); }
    const button = event.target.closest('.dcode-copy');
    if (!button) return;
    const pre = button.closest('.dcode').querySelector('pre');
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(pre.textContent);
      button.textContent = 'Copied';
      status.textContent = 'Code copied to clipboard';
    } catch {
      const range = document.createRange();
      range.selectNodeContents(pre);
      const selection = window.getSelection();
      selection.removeAllRanges(); selection.addRange(range);
      pre.focus();
      button.textContent = 'Code selected';
      status.textContent = 'Clipboard unavailable. Code selected; use your browser’s Copy command.';
    }
    setTimeout(() => { button.textContent = 'Copy'; }, 2500);
  });
  burger.addEventListener('click', () => {
    if (document.body.classList.contains('nav-open')) closeNav();
    else {
      document.body.classList.add('nav-open');
      burger.setAttribute('aria-expanded', 'true');
      sidebar.inert = false;
      main.inert = true;
      sidebar.querySelector('[aria-current=page],a')?.focus();
    }
  });
  document.getElementById('ds-overlay').addEventListener('click', closeNav);
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); closeNav(); input.focus(); input.select(); }
    if (event.key === 'Escape' && document.body.classList.contains('nav-open')) { closeNav(); burger.focus(); }
  });
  window.addEventListener('hashchange', () => route(true));
  mobile.addEventListener('change', closeNav);
  reduceMotion.addEventListener('change', event => { tourPaused = event.matches; updateTour(); });
  route();
})();
