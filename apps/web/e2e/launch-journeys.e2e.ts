import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";
import axe from "axe-core";
import { authenticateOwner, EMAIL, PASSWORD } from "./helpers";

for (const colorScheme of ["light", "dark"] as const) {
test(`primary workspace pages remain usable on desktop and mobile in ${colorScheme} mode`, async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
  await authenticateOwner(page);
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ["/", "/deployments", "/executions", "/artifacts", "/environments", "/runner-pools", "/credentials", "/code-library", "/security", "/activity", "/settings"]) {
      await page.goto(route);
      await expect(page.locator("main h1")).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", colorScheme);
      if (route === "/executions") {
        await expect(page.locator(".ops-dash")).toBeVisible();
        const operations = await page.locator(".ops-dash").boundingBox();
        expect(operations?.height, "Operations panel should leave room for the execution list")
          .toBeLessThan(width < 600 ? 700 : 450);
      }
      await expect(page.getByText("Something went wrong", { exact: true })).toBeHidden();
      // Audit the settled page rather than intermediate fade-in colors.
      await page.evaluate(() => Promise.all(document.getAnimations()
        .filter((animation) => animation.effect?.getTiming().iterations !== Infinity)
        .map((animation) => animation.finished.catch(() => {}))));
      // Tables may scroll within their own region; the whole page must fit.
      expect.soft(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${route} overflows at ${width}px`).toBe(true);
      await page.evaluate(axe.source);
      const violations = await page.evaluate(async () => {
        const result = await window.axe.run(document, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
        });
        return result.violations.filter((item) => item.impact === "critical" || item.impact === "serious")
          .map((item) => ({ id: item.id, nodes: item.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })) }));
      });
      expect.soft(violations, `${route} accessibility at ${width}px`).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`${route.replaceAll("/", "_") || "home"}-${width}.png`), fullPage: true });
    }
    await page.screenshot({ path: testInfo.outputPath(`settings-${width}.png`), fullPage: true });
  }
  expect(pageErrors).toEqual([]);
});
}

test("an in-progress execution stays readable in both themes", async ({ page, request }) => {
  await authenticateOwner(page);
  const login = await request.post("/api/auth/login", { data: { email: EMAIL, password: PASSWORD } });
  expect(login.ok()).toBe(true);
  const { token } = await login.json();
  const headers = { Authorization: `Bearer ${token}` };
  const created = await request.post("/api/workflows", {
    headers, data: { name: `QA · Running status visibility · ${Date.now()}` },
  });
  expect(created.ok()).toBe(true);
  const { id } = await created.json();
  const updated = await request.put(`/api/workflows/${id}`, {
    headers,
    data: { graph: {
      nodes: [
        { id: "start", type: "manual_trigger", params: {}, position: { x: 0, y: 0 } },
        { id: "wait", type: "code", params: { code: "import time\ntime.sleep(120)\noutput = input" }, position: { x: 280, y: 0 } },
      ],
      edges: [{ source: "start", target: "wait" }],
    } },
  });
  expect(updated.ok()).toBe(true);
  const started = await request.post(`/api/workflows/${id}/run`, { headers, data: {} });
  expect(started.ok()).toBe(true);
  const { run_id } = await started.json();
  try {
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
      await page.goto("/executions");
      await expect(page.locator(".status-run-running").first()).toBeVisible({ timeout: 30_000 });
      await page.evaluate(axe.source);
      const violations = await page.evaluate(async () => (await window.axe.run(".status-run-running", {
        runOnly: { type: "rule", values: ["color-contrast"] },
      })).violations);
      expect(violations, `${colorScheme} running status contrast`).toEqual([]);
    }
  } finally {
    const cancelled = await request.post(`/api/runs/${run_id}/cancel`, { headers });
    expect(cancelled.ok()).toBe(true);
  }
});

test("artifact uploads larger than 1 MiB pass through the public web proxy", async ({ request }) => {
  const login = await request.post("/api/auth/login", { data: { email: EMAIL, password: PASSWORD } });
  expect(login.ok()).toBe(true);
  const { token } = await login.json();
  const payload = Buffer.alloc(2 * 1024 * 1024, "nodyra-launch-upload\n");
  const upload = await request.post("/api/artifacts/upload", {
    headers: { Authorization: `Bearer ${token}` },
    multipart: { file: { name: "launch-upload.txt", mimeType: "text/plain", buffer: payload } },
  });
  expect(upload.status(), await upload.text()).toBe(200);
  const artifact = await upload.json();
  expect(artifact.size_bytes).toBe(payload.length);
  expect(artifact.checksum_sha256).toBe(createHash("sha256").update(payload).digest("hex"));
  const download = await request.get(`/api/artifacts/${artifact.id}/download`, {
    headers: { Authorization: `Bearer ${token}` }, maxRedirects: 0,
  });
  if (download.status() === 307) {
    const stored = await request.get(download.headers().location);
    expect(stored.ok()).toBe(true);
    expect(await stored.body()).toEqual(payload);
  } else {
    expect(download.ok()).toBe(true);
    expect(await download.body()).toEqual(payload);
  }
});
