import { test, expect } from '@playwright/test';
import axe from 'axe-core';

test('documentation overview includes a local animated tour', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/docs.html');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Nodyra documentation');
  const tour = page.locator('.tour-animation');
  await expect(tour).toHaveAttribute('src', 'assets/nodyra-product-tour.gif');
  await expect.poll(() => tour.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1440);
  await expect(page.locator('.tour-still')).toBeHidden();
  await page.getByRole('button', { name: 'Pause product tour' }).click();
  await expect(tour).toBeHidden();
  await expect(page.locator('.tour-still')).toBeVisible();
  await page.getByRole('button', { name: 'Play product tour' }).click();
  await expect(tour).toBeVisible();
  await page.getByRole('link', { name: 'single-host installation guide', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Install on one host');
  await expect(page.locator('main')).toContainText('SESSION_COOKIE_SECURE=false');
  await expect(page.locator('main')).toContainText('SESSION_COOKIE_SECURE=true');
  await expect(page.getByRole('link', { name: 'Edit on GitHub' })).toHaveAttribute('href', 'https://github.com/Harshit-repo/Nodyra/blob/main/docs/deployment/self-hosted.md');
  expect(errors).toEqual([]);
});

test('search works by keyboard, clears stale matches, and recovers from no results', async ({ page }) => {
  await page.goto('/docs.html');
  const input = page.getByRole('combobox', { name: 'Search documentation' });
  await input.fill('backup');
  await input.press('ArrowDown');
  await expect(input).toHaveAttribute('aria-activedescendant', 'search-option-0');
  await input.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Backup and restore');
  await input.fill('nonsense-no-match-9381');
  await expect(page.locator('.dr-empty')).toContainText('No matching guides');
  await input.fill('x');
  await input.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Backup and restore');
  await expect(input).toHaveAttribute('aria-expanded', 'false');
});

test('direct section routes, reload, history, and missing pages work', async ({ page }) => {
  await page.goto('/docs.html#/getting-started/4-run-a-verified-starter-template');
  await expect(page.locator('#sec-4-run-a-verified-starter-template')).toBeInViewport();
  await page.reload();
  await expect(page.locator('#sec-4-run-a-verified-starter-template')).toBeInViewport();
  await page.locator('#ds-nav').getByRole('link', { name: 'Security', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Security');
  await page.goBack();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Getting started');
  await page.goto('/docs.html#/missing');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Page not found');
  await page.getByRole('link', { name: 'return to the documentation home' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Nodyra documentation');
});

test('copy failure selects the actual command and gives feedback', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: undefined }));
  await page.goto('/docs.html#/getting-started');
  await page.getByRole('button', { name: 'Copy', exact: true }).first().click();
  await expect(page.locator('#docs-status')).toContainText('Code selected');
  expect(await page.evaluate(() => window.getSelection()?.toString())).toContain('git clone --branch v1.0.5');
});

for (const width of [320, 390, 768, 1366]) {
  test(`docs fit at ${width}px and expose accessible navigation`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/docs.html');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width < 901) {
      const toggle = page.getByRole('button', { name: 'Toggle documentation navigation' });
      await toggle.click();
      await expect(page.locator('#ds-nav').getByRole('link', { name: 'Getting started', exact: true })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(toggle).toBeFocused();
      await toggle.click();
      await page.locator('#ds-nav').getByRole('link', { name: 'Getting started', exact: true }).click();
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    } else await page.goto('/docs.html#/getting-started');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Getting started');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('landing and installation meet automated WCAG A/AA checks', async ({ page }) => {
  for (const route of ['/docs.html', '/docs.html#/self-hosted']) {
    await page.goto(route);
    await page.addScriptTag({ content: axe.source });
    const violations = await page.evaluate(async () => (await (window as any).axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } })).violations);
    expect(violations.map((v: any) => ({ id: v.id, nodes: v.nodes.map((n: any) => n.target) }))).toEqual([]);
  }
});

test('reduced motion pauses the GIF and no-script still provides docs and tour', async ({ browser }) => {
  const reduced = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await reduced.newPage();
  await page.goto('http://127.0.0.1:5187/docs.html');
  await expect(page.getByRole('button', { name: 'Play product tour' })).toBeVisible();
  await expect(page.locator('.tour-animation')).toBeHidden();
  await reduced.close();
  const plain = await browser.newContext({ javaScriptEnabled: false });
  const doc = await plain.newPage();
  await doc.goto('http://127.0.0.1:5187/docs.html');
  await expect(doc.getByRole('heading', { level: 1 })).toHaveText('Nodyra documentation');
  await expect(doc.locator('.tour-animation')).toBeVisible();
  await expect(doc.getByRole('link', { name: 'GitHub documentation', exact: true })).toBeVisible();
  await plain.close();
});
