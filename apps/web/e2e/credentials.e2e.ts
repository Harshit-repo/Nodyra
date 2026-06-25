import { expect, test } from "@playwright/test";

import { authenticateOwner, uniqueName } from "./helpers";

test.beforeEach(async ({ page }) => {
  await authenticateOwner(page);
});

test("creates, filters, and deletes an encrypted credential", async ({ page }) => {
  const credentialName = uniqueName("E2E OpenAI");
  await page.goto("/credentials");
  await page.getByRole("button", { name: "New credential" }).first().click();

  const createDialog = page.getByRole("dialog", { name: "New credential" });
  await createDialog.getByPlaceholder("Search credential type").fill("OpenAI");
  await createDialog
    .getByRole("button", { name: /OpenAI API Key/i })
    .click();
  await createDialog.getByPlaceholder("Credential name").fill(credentialName);
  await createDialog.getByPlaceholder("Paste API key").fill("sk-e2e-not-a-real-key");

  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/credentials",
  );
  await createDialog.getByRole("button", { name: "Create", exact: true }).click();
  expect((await created).ok()).toBe(true);
  await expect(createDialog).toBeHidden();

  const credentialCard = page.locator(".env-card", { hasText: credentialName });
  await expect(credentialCard).toContainText("OpenAI API Key");
  await expect(credentialCard).toContainText("api_key");
  await expect(credentialCard).toContainText("••••");
  await expect(credentialCard).not.toContainText("sk-e2e-not-a-real-key");

  await page.getByPlaceholder("Search credentials").fill(credentialName);
  await expect(page.locator(".env-card")).toHaveCount(1);
  await credentialCard.getByRole("button", { name: "Delete" }).click();
  const confirm = page.getByRole("dialog", { name: "Delete credential?" });
  await expect(confirm).toContainText(credentialName);

  const deleted = page.waitForResponse(
    (response) =>
      response.request().method() === "DELETE" &&
      /\/api\/credentials\/[0-9a-f]+$/.test(new URL(response.url()).pathname),
  );
  await confirm.getByRole("button", { name: "Delete" }).click();
  expect((await deleted).ok()).toBe(true);
  await expect(credentialCard).toHaveCount(0);
});
