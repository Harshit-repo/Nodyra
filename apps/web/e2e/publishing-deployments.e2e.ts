import { expect, test } from "@playwright/test";

import {
  authenticateOwner,
  createDraftWorkflow,
  publishCurrentDraft,
  uniqueName,
} from "./helpers";

test.beforeEach(async ({ page }) => {
  await authenticateOwner(page);
});

test("publishes a saved draft as a live workflow version", async ({ page }) => {
  const workflowName = uniqueName("E2E Publishing");
  await createDraftWorkflow(page, workflowName);

  await expect(
    page.getByRole("button", { name: "Publish changes" }),
  ).toBeVisible();
  await publishCurrentDraft(page, "Validated by the publishing E2E test");

  await expect(page.getByLabel("Pause workflow")).toBeChecked();
  await page.goto("/");
  const workflowRow = page.locator(".wf-card", { hasText: workflowName });
  await expect(workflowRow).toContainText("published v2");
  await expect(workflowRow.locator(".wf-meta-draft")).toHaveCount(0);
});

test("creates a scheduled deployment for a published workflow", async ({
  page,
}) => {
  const workflowName = uniqueName("E2E Deployable");
  const deploymentName = uniqueName("E2E Deployment");
  await createDraftWorkflow(page, workflowName);
  await publishCurrentDraft(page);

  await page.goto("/deployments");
  await page.getByRole("button", { name: /New deployment/ }).first().click();
  const dialog = page.getByRole("dialog", { name: "New deployment" });
  await dialog.getByRole("combobox").first().selectOption({ label: workflowName });
  await dialog.getByPlaceholder("e.g. Daily 9am Sydney").fill(deploymentName);
  await dialog.getByPlaceholder("0 9 * * 1-5").fill("0 9 * * 1-5");
  await dialog.getByPlaceholder("Australia/Sydney").fill("Australia/Sydney");

  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/deployments",
  );
  await dialog.getByRole("button", { name: "Create deployment" }).click();
  expect((await created).ok()).toBe(true);
  await expect(dialog).toBeHidden();

  const deployment = page.locator(".deploy-item", { hasText: deploymentName });
  await expect(deployment).toContainText(workflowName);
  await expect(deployment).toContainText("cron: 0 9 * * 1-5 (Australia/Sydney)");
  await expect(deployment).toContainText("paused");
});
