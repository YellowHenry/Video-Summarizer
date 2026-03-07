import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { setupMockApi } from "./support/mockApi";

const STORAGE_STATE_DIR = ".auth";
const STORAGE_STATE_PATH = `${STORAGE_STATE_DIR}/storage-state.json`;

test("@verify @request submit job flow + persisted job chat rendering", async ({ page }) => {
  await setupMockApi(page);

  await page.goto("/");
  await page.getByLabel("YouTube URL").fill("https://www.youtube.com/watch?v=e2e123");
  await page.getByRole("button", { name: "Submit" }).click();

  await expect(page.locator(".jobs-list-card")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Hide Jobs" }).first()).toBeVisible();
  const firstRowTitle = page.locator(".job-table tbody tr").first().locator(".job-title");
  await expect(firstRowTitle).toHaveText("Mock Video Title");
  await expect(firstRowTitle).not.toContainText("youtube.com");
  await page.locator(".job-table tbody tr").first().click();
  await expect(page.locator(".jobs-list-card")).toHaveCount(1);
  await expect(page.locator(".detail-title", { hasText: "Mock Video Title" })).toBeVisible();
  await page.getByRole("button", { name: "Hide Jobs" }).first().click();
  await expect(page.locator(".jobs-list-card")).toHaveCount(0);
  await expect(page.locator(".job-detail-card")).toHaveClass(/summary-spotlight/);
  await page.getByRole("button", { name: "Show Jobs" }).first().click();
  await expect(page.locator(".jobs-list-card")).toHaveCount(1);
  await expect(page.getByText("Mock summary")).toBeVisible();

  await page.getByRole("tab", { name: "Job Chat" }).click();
  await page.getByPlaceholder("Ask about this transcript...").fill("What happened?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Mock assistant answer")).toBeVisible();

  await page.getByRole("button", { name: "Global Search" }).click();
  await page.getByLabel("Question").fill("What topics were discussed?");
  await page.getByRole("button", { name: "Run Search" }).click();
  await expect(page.getByText("Mock global search answer")).toBeVisible();

  mkdirSync(STORAGE_STATE_DIR, { recursive: true });
  await page.context().storageState({ path: STORAGE_STATE_PATH });
});

test("@verify @request jobs list toggle + summary spotlight", async ({ page }) => {
  await setupMockApi(page);

  await page.goto("/");
  await page.getByLabel("YouTube URL").fill("https://www.youtube.com/watch?v=toggle123");
  await page.getByRole("button", { name: "Submit" }).click();

  await expect(page.locator(".jobs-list-card")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Hide Jobs" }).first()).toBeVisible();
  await expect(page.locator(".detail-title", { hasText: "Mock Video Title" })).toBeVisible();
  const summaryBeforeMetaVisible = await page.evaluate(() => {
    const summarySection = document.querySelector(".job-detail-tab-panel[aria-label='Summary']");
    const metadataSection = document.querySelector(".job-meta-section");
    if (!summarySection || !metadataSection) {
      return false;
    }
    return Boolean(summarySection.compareDocumentPosition(metadataSection) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(summaryBeforeMetaVisible).toBeTruthy();
  const hierarchyStyles = await page.evaluate(() => {
    const summaryPanel = document.querySelector(".job-detail-tab-panel[aria-label='Summary'] .summary-panel");
    const metadataCard = document.querySelector(".job-meta-section .detail-grid > div");
    if (!summaryPanel || !metadataCard) {
      return null;
    }
    const summaryComputed = window.getComputedStyle(summaryPanel);
    const metadataComputed = window.getComputedStyle(metadataCard);
    return {
      summaryBorderColor: summaryComputed.borderColor,
      metadataBorderColor: metadataComputed.borderColor,
      summaryBoxShadow: summaryComputed.boxShadow,
      metadataBoxShadow: metadataComputed.boxShadow,
    };
  });
  expect(hierarchyStyles).not.toBeNull();
  if (hierarchyStyles) {
    expect(hierarchyStyles.summaryBorderColor).not.toBe(hierarchyStyles.metadataBorderColor);
    expect(hierarchyStyles.summaryBoxShadow).not.toBe("none");
    expect(hierarchyStyles.metadataBoxShadow).toBe("none");
  }
  const visibleSummaryPanel = page.locator(".job-detail-tab-panel[aria-label='Summary'] .summary-panel");
  await expect(visibleSummaryPanel).toBeVisible();
  const visibleSummaryBox = await visibleSummaryPanel.boundingBox();
  expect(visibleSummaryBox).not.toBeNull();
  const initialViewportHeight = page.viewportSize()?.height ?? 900;
  if (visibleSummaryBox) {
    expect(visibleSummaryBox.y).toBeGreaterThanOrEqual(0);
    expect(visibleSummaryBox.y).toBeLessThan(initialViewportHeight - 120);
  }
  await page.locator(".job-table tbody tr").first().click();
  await expect(page.locator(".jobs-list-card")).toHaveCount(1);
  await expect(page.locator(".job-table tbody tr").first().locator(".job-title")).toHaveText("Mock Video Title");
  const scrollBeforeHide = await page.evaluate(() => window.scrollY);
  await page.getByRole("button", { name: "Hide Jobs" }).first().click();
  const scrollAfterHide = await page.evaluate(() => window.scrollY);
  await expect(page.locator(".jobs-list-card")).toHaveCount(0);
  expect(Math.abs(scrollAfterHide - scrollBeforeHide)).toBeLessThanOrEqual(2);
  const headerBox = await page.locator(".header").boundingBox();
  expect(headerBox).not.toBeNull();
  const viewportHeight = page.viewportSize()?.height ?? 900;
  if (headerBox) {
    expect(headerBox.y).toBeGreaterThanOrEqual(0);
    expect(headerBox.y + headerBox.height).toBeLessThanOrEqual(viewportHeight);
  }
  await expect(page.locator(".job-detail-card")).toHaveClass(/summary-spotlight/);
  const hiddenSummaryPanel = page.locator(".job-detail-tab-panel[aria-label='Summary'] .summary-panel");
  await expect(hiddenSummaryPanel).toBeVisible();
  const hiddenSummaryBox = await hiddenSummaryPanel.boundingBox();
  expect(hiddenSummaryBox).not.toBeNull();
  if (hiddenSummaryBox) {
    expect(hiddenSummaryBox.y).toBeGreaterThanOrEqual(0);
    expect(hiddenSummaryBox.y).toBeLessThan(viewportHeight - 120);
  }
  await page.getByRole("button", { name: "Show Jobs" }).first().click();
  await expect(page.locator(".jobs-list-card")).toHaveCount(1);
  await expect(page.locator(".job-detail-card")).toHaveScreenshot("job-detail-summary-spotlight.png");
});
