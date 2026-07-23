import { expect, test, type Page } from "@playwright/test";

import { authenticateOwner } from "./helpers";

// Serial suite sharing one page so the cookie session established in test 1
// carries through the whole critical path.
test.describe.configure({ mode: "serial" });

let page: Page;

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage();
});

test.afterAll(async () => {
  await page.close();
});

test("owner setup or sign-in opens the workspace", async () => {
  await authenticateOwner(page);
});

test("create a workflow and add a Manual Trigger from the palette", async () => {
  await page.getByRole("button", { name: "New workflow" }).first().click();
  const dialog = page.getByRole("dialog", { name: "New workflow" });
  const nameInput = dialog.locator("input.field-input");
  await nameInput.fill("E2E Smoke");
  // Enter submits the modal (the Create button can sit below the fold of the
  // template picker in small viewports, so the keyboard path is more robust).
  await nameInput.press("Enter");
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f]+/);

  // Palette search + Enter drops the top match onto the canvas.
  const search = page.getByRole("textbox", { name: "Search nodes" });
  await search.fill("Manual Trigger");
  await search.press("Enter");
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  const [saved] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        /\/api\/workflows\/[0-9a-f]+$/.test(new URL(response.url()).pathname),
    ),
    page.keyboard.press("Control+S"),
  ]);
  expect(saved.ok()).toBe(true);
});

test("run the workflow and observe node success", async () => {
  // First run on a fresh install may build the global environment; be patient.
  test.setTimeout(180_000);
  await page
    .getByRole("button", { name: "▶ Execute Workflow", exact: true })
    .click();
  await expect(page.locator(".node-status.status-run-success").first()).toBeVisible(
    { timeout: 120_000 },
  );
});
