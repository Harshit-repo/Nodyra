import { expect, test, type Page } from "@playwright/test";

const EMAIL = "owner@e2e.local";
const PASSWORD = "e2e-password-123";

// Serial suite sharing ONE page: the session token lives in localStorage, so a
// fresh context per test would log tests 2-3 out. The shared page carries the
// owner session created in test 1 through the whole critical path.
test.describe.configure({ mode: "serial" });

let page: Page;

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage();
});

test.afterAll(async () => {
  await page.close();
});

test("first-user setup creates the owner account", async () => {
  await page.goto("/");
  // Fresh DB → registration mode ("Create owner account").
  await page.getByPlaceholder("Full name").fill("E2E Owner");
  await page.getByPlaceholder("Company").fill("E2E Co");
  await page.getByPlaceholder("you@example.com").fill(EMAIL);
  await page.getByPlaceholder("password (min 8 chars)").fill(PASSWORD);
  await page.getByRole("button", { name: "Create owner" }).click();
  await expect(
    page.getByRole("button", { name: "New workflow" }).first(),
  ).toBeVisible();
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
  const search = page.getByPlaceholder("Search nodes…");
  await search.fill("Manual Trigger");
  await search.press("Enter");
  await expect(page.locator(".react-flow__node")).toHaveCount(1);

  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(
    page.getByRole("button", { name: "Save draft" }),
  ).toBeVisible();
});

test("run the workflow and observe node success", async () => {
  // First run on a fresh install may build the global environment; be patient.
  test.setTimeout(180_000);
  await page.getByRole("button", { name: "▶ Run", exact: true }).click();
  await expect(page.locator(".node-status.status-run-success").first()).toBeVisible(
    { timeout: 120_000 },
  );
});
