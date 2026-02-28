import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { setupMockApi } from "./support/mockApi";

const STORAGE_STATE_DIR = ".auth";
const STORAGE_STATE_PATH = `${STORAGE_STATE_DIR}/storage-state.json`;

test("@verify @request visual regression: job detail summary", async ({ page }) => {
  await setupMockApi(page);

  await page.goto("/");
  await page.getByLabel("YouTube URL").fill("https://www.youtube.com/watch?v=visual123");
  await page.getByRole("button", { name: "Submit" }).click();

  await expect(page.getByText("Mock Video Title")).toBeVisible();
  await expect(page.getByText("Mock summary")).toBeVisible();
  await expect(page.locator(".job-detail-card")).toHaveScreenshot("job-detail-summary.png");

  mkdirSync(STORAGE_STATE_DIR, { recursive: true });
  await page.context().storageState({ path: STORAGE_STATE_PATH });
});

test("@verify visual regression: job detail chat", async ({ page }) => {
  await setupMockApi(page);

  await page.goto("/");
  await page.getByLabel("YouTube URL").fill("https://www.youtube.com/watch?v=chat123");
  await page.getByRole("button", { name: "Submit" }).click();
  await page.getByRole("tab", { name: "Job Chat" }).click();
  await page.getByPlaceholder("Ask about this transcript...").fill("What happened?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Mock assistant answer")).toBeVisible();
  await expect(page.locator(".job-detail-card")).toHaveScreenshot("job-detail-chat.png");
});
