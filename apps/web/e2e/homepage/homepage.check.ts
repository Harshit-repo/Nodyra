import { test, expect } from '@playwright/test';
import axe from 'axe-core';

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('site root serves the product page and documentation links back home', async ({ page }) => {
  const heading = await page.getByRole('heading', { level: 1 }).textContent();
  await page.getByRole('link', { name: 'Docs', exact: true }).first().click();
  await expect(page).toHaveURL(/\/docs.html$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Nodyra documentation');
  await page.getByRole('link', { name: 'Nodyra home', exact: true }).click();
  await expect(page).toHaveURL(/\/index.html$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(heading!);
  await page.goto('/nodyra.html#how');
  await expect(page.locator('#how')).toBeInViewport();
  await page.goto('/#how');
  await expect(page.locator('#how')).toBeInViewport();
});

for (const width of [320, 390, 768, 1024, 1366]) {
  test(`content and all graph nodes fit at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const issues = await page.evaluate(() => {
      const viewport = document.documentElement.clientWidth;
      const panels = [...document.querySelectorAll('main > section, .wrap, .hero-canvas-wrap')];
      const problems = panels.filter(el => {
        const r = el.getBoundingClientRect();
        return r.left < -1 || r.right > viewport + 1;
      }).map(el => el.id || el.className);
      const stage = document.querySelector('.hero-canvas-wrap')!.getBoundingClientRect();
      document.querySelectorAll('.node-tile,.node-label').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.left < stage.left || r.right > stage.right || r.top < stage.top || r.bottom > stage.bottom) problems.push(el.parentElement!.id);
      });
      return problems;
    });
    expect(issues).toEqual([]);
    await expect(page.getByRole('button', { name: 'Open HTTP Request node' })).toBeVisible();
    await page.getByRole('button', { name: 'Open HTTP Request node' }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Close', exact: true })).toBeInViewport();
    const clippedTabs = await page.getByRole('dialog').evaluate(dialog => {
      const r = dialog.getBoundingClientRect();
      return [...dialog.querySelectorAll('[role="tab"]')].filter(el => {
        const tab = el.getBoundingClientRect();
        return tab.left < r.left || tab.right > r.right;
      }).map(el => el.textContent);
    });
    expect(clippedTabs).toEqual([]);
  });
}

test('email actions are honest and self-hosting opens the quickstart', async ({ page }) => {
  const links = page.getByRole('link', { name: 'Request early access', exact: true });
  await expect(links).toHaveCount(2);
  await expect(page.getByRole('link', { name: 'Request a license', exact: true })).toHaveAttribute('href', 'mailto:sharma.har97@gmail.com?subject=Nodyra%20license%20enquiry');
  for (const link of await links.all()) await expect(link).toHaveAttribute('href', 'mailto:sharma.har97@gmail.com?subject=Nodyra%20early%20access');
  await expect(page.locator('form')).toHaveCount(0);
  await page.getByRole('link', { name: 'Start self-hosting' }).first().click();
  await expect(page).toHaveURL(/docs.html#\/getting-started/);
  await expect(page.locator('main')).toContainText('Getting started');
});

test('graph and Python controls expose the visible state', async ({ page }) => {
  await page.getByRole('button', { name: 'Python', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Python', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: 'Open Fetch Data node' })).toHaveCount(0);
  await expect(page.locator('#python-pane')).toHaveAttribute('aria-hidden', 'false');
  await page.getByRole('button', { name: 'Graph', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Open Fetch Data node' })).toBeVisible();
});

test('inspector traps focus, switches tabs by keyboard, and restores focus', async ({ page }) => {
  const node = page.getByRole('button', { name: 'Open Fetch Data node' });
  await node.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
  await page.getByRole('tab', { name: 'Parameters', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Settings', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Escape');
  await expect(node).toBeFocused();
  await expect(page.locator('main')).not.toHaveAttribute('inert', '');
});

test('mobile navigation closes on Escape and section selection', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const toggle = page.getByRole('button', { name: 'Toggle navigation menu' });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  await page.locator('#nav-mobile').getByRole('link', { name: 'Pricing', exact: true }).click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page).toHaveURL(/#pricing$/);
});

test('input and output tree controls act on their own panel', async ({ page }) => {
  await page.getByRole('button', { name: 'Open Fetch Data node' }).click();
  const duplicateIds = await page.evaluate(() => {
    const ids = [...document.querySelectorAll('[id]')].map(el => el.id);
    return ids.filter((id, i) => ids.indexOf(id) !== i);
  });
  expect(duplicateIds).toEqual([]);
  const output = page.locator('#p-out-n2-schema');
  await output.getByRole('button', { name: 'Toggle rows', exact: true }).click();
  await expect(output.getByText('u_4821', { exact: true })).toBeHidden();
  await expect(page.locator('#p-in-n2-schema').getByText('u_4821', { exact: true })).toBeVisible();
});

test('clipboard failure offers selectable text without an unhandled error', async ({ page }) => {
  await page.context().grantPermissions(['clipboard-write']);
  await page.evaluate(() => {
    Object.defineProperty(navigator.clipboard, 'writeText', { value: async () => { throw new Error('Denied'); } });
  });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.getByRole('button', { name: 'Open Fetch Data node' }).click();
  await page.locator('#p-out-n2-toggle').getByRole('button', { name: 'Copy', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Select to copy' })).toBeVisible();
  await expect(page.locator('#p-out-n2-raw')).toBeVisible();
  expect(errors).toEqual([]);
});

test('page and inspector pass automated WCAG A/AA checks', async ({ page }) => {
  await page.addScriptTag({ content: axe.source });
  const violations = async () => page.evaluate(async () => (await (window as any).axe.run({ runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } })).violations.map((v: any) => ({ id: v.id, nodes: v.nodes.map((n: any) => ({ target: n.target, summary: n.failureSummary })) })));
  expect(await violations()).toEqual([]);
  await page.getByRole('button', { name: 'Open Fetch Data node' }).click();
  expect(await violations()).toEqual([]);
});

test('content and contact links remain usable without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:5187/nodyra.html');
  await expect(page.getByRole('heading', { name: 'Start free. Scale when you need to.' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Request early access' }).first()).toBeVisible();
  await context.close();
});
