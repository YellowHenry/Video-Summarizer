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

  await expect(page.locator(".jobs-list-card")).toHaveCount(1);
  await expect(page.locator(".detail-title", { hasText: "Mock Video Title" })).toBeVisible();
  const summaryBeforeMeta = await page.evaluate(() => {
    const summarySection = document.querySelector(".job-detail-tab-panel[aria-label='Summary']");
    const metadataSection = document.querySelector(".job-meta-section");
    if (!summarySection || !metadataSection) {
      return false;
    }
    return Boolean(summarySection.compareDocumentPosition(metadataSection) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(summaryBeforeMeta).toBeTruthy();
  await expect(page.getByText("Job details")).toBeVisible();
  await expect(page.getByText("Mock summary")).toBeVisible();
  await expect(page.locator(".page")).toHaveScreenshot("jobs-page-visible.png");
  await page.getByRole("button", { name: "Hide Jobs" }).first().click();
  await expect(page.locator(".jobs-list-card")).toHaveCount(0);
  await expect(page.locator(".page")).toHaveScreenshot("jobs-page-hidden-no-scroll-jump.png");
  await expect(page.locator(".job-detail-card")).toHaveScreenshot("job-detail-summary.png");

  mkdirSync(STORAGE_STATE_DIR, { recursive: true });
  await page.context().storageState({ path: STORAGE_STATE_PATH });
});

test("@verify @request visual regression: job detail chat", async ({ page }) => {
  await setupMockApi(page);

  await page.goto("/");
  await page.getByLabel("YouTube URL").fill("https://www.youtube.com/watch?v=chat123");
  await page.getByRole("button", { name: "Submit" }).click();
  await page.getByRole("button", { name: "Hide Jobs" }).first().click();
  await expect(page.locator(".jobs-list-card")).toHaveCount(0);
  await page.getByRole("tab", { name: "Job Chat" }).click();
  await page.getByPlaceholder("Ask about this transcript...").fill("What happened?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Mock assistant answer")).toBeVisible();
  await expect(page.locator(".job-detail-card")).toHaveScreenshot("job-detail-chat.png");
});
