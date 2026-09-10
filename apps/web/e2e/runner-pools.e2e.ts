import { expect, test } from "@playwright/test";

import { authenticateOwner, uniqueName } from "./helpers";

test.beforeEach(async ({ page }) => {
  await authenticateOwner(page);
});

test("creates an agent runner pool and mints a machine install token", async ({
  page,
}) => {
  const poolName = uniqueName("E2E Agent Pool");
  const machineName = uniqueName("e2e-worker");
  await page.goto("/runner-pools");
  await expect(page.getByRole("heading", { name: /^Runner Pools/ })).toBeVisible();
  // The default production install is Community; the development E2E
  // server supplies a throwaway Enterprise key. Verify both real outcomes.
  const licenseSettings = page.getByRole("link", { name: "View license settings" });
  if (await licenseSettings.isVisible()) {
    await expect(page.getByRole("button", { name: /New pool|Create runner pool/ })).toHaveCount(0);
    await expect(page.getByText(/Enterprise adds dedicated runner pools/)).toBeVisible();
    return;
  }
  await page.getByRole("button", { name: /New pool|Create runner pool/ }).first().click();

  const poolDialog = page.getByRole("dialog", { name: "New runner pool" });
  await poolDialog.getByPlaceholder("production-pool").fill(poolName);
  await poolDialog.getByRole("combobox").selectOption("agent");
  await poolDialog.locator('input[type="number"]').fill("3");

  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/runner-pools",
  );
  await poolDialog.getByRole("button", { name: "Create pool" }).click();
  const createdResponse = await created;
  expect(createdResponse.ok(), await createdResponse.text()).toBe(true);
  await expect(poolDialog).toBeHidden();

  const poolCard = page.locator(".pool-card", { hasText: poolName });
  await expect(poolCard).toContainText("agent");
  await expect(poolCard).toContainText("max 3 concurrent");
  await poolCard.getByRole("button", { name: "Runners" }).click();
  await poolCard.getByRole("button", { name: "+ Add machine" }).click();

  const machineDialog = page.getByRole("dialog", {
    name: "Add a machine to this pool",
  });
  await machineDialog.getByPlaceholder("ci-worker-3").fill(machineName);
  await machineDialog.locator('input[type="number"]').fill("2");
  await machineDialog.getByPlaceholder("region=eu\ngpu=a100").fill("region=au\nclass=e2e");

  const minted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/runner-pools\/[0-9a-f]+\/registration-tokens$/.test(
        new URL(response.url()).pathname,
      ),
  );
  await machineDialog.getByRole("button", { name: "Mint install token" }).click();
  expect((await minted).ok()).toBe(true);

  const installSnippet = machineDialog.locator(".runner-install pre");
  await expect(installSnippet).toContainText("nodyra-runner register");
  await expect(installSnippet).toContainText(`--name ${machineName}`);
  await expect(installSnippet).toContainText("--token");
});
