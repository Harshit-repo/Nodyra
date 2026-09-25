import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

import { authenticateOwner, createDraftWorkflow, uniqueName } from "./helpers";

// Exercise the exact shipped policy against the compiled app in the release
// lane. Vite's development HTML requires inline refresh code and is unsuitable
// for asserting production script restrictions.
const nginx = readFileSync(new URL("../nginx.conf", import.meta.url), "utf8");
const productionCsp = nginx.match(/set \$nodyra_csp "([^"]+)";/)?.[1];
if (!productionCsp) throw new Error("Cannot read the production CSP from nginx.conf");
test.use({ actionTimeout: 10_000 });

test.beforeEach(async ({ page }) => {
  if (process.env.E2E_PRODUCTION !== "1") return;
  await page.route("**/*", async (route) => {
    if (route.request().resourceType() !== "document") return route.continue();
    const response = await route.fetch();
    await route.fulfill({
      response,
      headers: { ...response.headers(), "content-security-policy": productionCsp },
    });
  });
});

test("Python editor loads and accepts input without external scripts", async ({ page }, testInfo) => {
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await authenticateOwner(page);
  const origin = new URL(page.url()).origin;
  const externalScripts: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && /Content Security Policy|Refused to/.test(message.text())) {
      errors.push(message.text());
    }
  });
  page.on("request", (request) => {
    if (request.resourceType() === "script" && new URL(request.url()).origin !== origin) {
      externalScripts.push(request.url());
    }
  });
  // Block CDN scripts even in the dev lane; a cached/downloadable CDN must not
  // conceal an accidental return to the default Monaco loader.
  await page.route(/^https?:\/\//, (route) => {
    if (route.request().resourceType() === "script" && new URL(route.request().url()).origin !== origin) {
      return route.abort();
    }
    return route.fallback();
  });
  await createDraftWorkflow(page, uniqueName("Self-hosted editor"));
  await page.locator(".react-flow__node").dblclick();
  const nodeDialog = page.getByRole("dialog", { name: "Manual Trigger", exact: true });
  await nodeDialog.getByRole("tab", { name: "Python", exact: true }).click();
  expect(requests.filter((url) => /vendor-monaco|MonacoRuntime|editor\.worker/.test(url))).toEqual([]);
  await nodeDialog.getByRole("button", { name: "Open full editor with input data" }).click();
  const dialog = page.getByRole("dialog", { name: /Editing code/ });
  const editor = dialog.getByRole("textbox", { name: "Python code editor", exact: true });
  await expect(editor).toBeVisible();
  await editor.press("Control+End");
  await editor.press("Enter");
  await editor.pressSequentially("# Local editor acceptance");
  await expect(dialog.locator(".view-lines")).toContainText("# Local editor acceptance");
  expect(externalScripts).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("python-editor.png") });
});

test("unknown public chat URLs offer a way back to the workspace", async ({ page }) => {
  await page.goto("/chat/missing/extra");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await page.getByRole("link", { name: "Back to workflows" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: /Sign in to Nodyra|Create your Nodyra workspace/ })).toBeVisible();
});
