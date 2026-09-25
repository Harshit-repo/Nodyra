import { expect, type Page } from "@playwright/test";

export const EMAIL = process.env.E2E_EMAIL ?? "owner@e2e.local";
export const PASSWORD = process.env.E2E_PASSWORD ?? "e2e-password-123";

/**
 * Authenticate through the real login UI. The first test to reach a freshly
 * reset E2E database creates the owner; later isolated browser contexts sign
 * in as that same owner. Each isolated context also completes the first-run
 * checklist collapse before returning so callers start from a stable canvas.
 */
export async function authenticateOwner(page: Page): Promise<void> {
  await page.goto("/");

  const createOwner = page.getByRole("button", { name: "Create owner" });
  const signIn = page.getByRole("button", { name: "Sign in", exact: true });
  await expect(createOwner.or(signIn)).toBeVisible();

  if (await createOwner.isVisible()) {
    await page.getByPlaceholder("Full name").fill("E2E Owner");
    await page.getByPlaceholder("Company").fill("E2E Co");
    await page.getByPlaceholder("you@example.com").fill(EMAIL);
    await page.getByRole("textbox", { name: "Password" }).fill(PASSWORD);
    await createOwner.click();
  } else {
    await page.getByPlaceholder("you@example.com").fill(EMAIL);
    await page.getByRole("textbox", { name: "Password" }).fill(PASSWORD);
    await signIn.click();
  }

  const collapseChecklist = page.getByRole("button", {
    name: "Collapse activation checklist",
  });
  const openChecklist = page.getByRole("button", {
    name: /Open activation checklist/,
  });
  await expect(collapseChecklist.or(openChecklist)).toBeVisible();
  if (await collapseChecklist.isVisible()) {
    await collapseChecklist.click();
    await expect(openChecklist).toBeVisible();
  }

  // The editor tour deliberately captures keyboard input. Keep general
  // workflow E2E tests deterministic; the tour itself has dedicated tests.
  await page.evaluate(() => {
    window.localStorage.setItem("nodyra-editor-tour-v1", "true");
  });

  await expect(
    page.getByRole("button", { name: "New workflow" }).first(),
  ).toBeVisible();
}

export function uniqueName(prefix: string): string {
  return `${prefix} ${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

/** Create an editable workflow containing a Manual Trigger and persist it. */
export async function createDraftWorkflow(
  page: Page,
  name: string,
): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: "New workflow" }).first().click();

  const dialog = page.getByRole("dialog", { name: "New workflow" });
  const nameInput = dialog.locator("input.field-input");
  await nameInput.fill(name);
  await nameInput.press("Enter");
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f]+/);

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
}

/** Publish the current editor draft through the review dialog. */
export async function publishCurrentDraft(
  page: Page,
  notes = "E2E release",
): Promise<void> {
  await page
    .getByRole("button", { name: /^(Publish|Publish changes|Republish)$/ })
    .click();

  const dialog = page.getByRole("dialog", { name: "Review release" });
  await expect(dialog).toBeVisible();
  await dialog.getByPlaceholder("What changed in this version?").fill(notes);

  const published = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/workflows\/[0-9a-f]+\/publish$/.test(
        new URL(response.url()).pathname,
      ),
  );
  await dialog.getByRole("button", { name: "Publish release" }).click();
  expect((await published).ok()).toBe(true);
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "Published" })).toBeVisible();
}
