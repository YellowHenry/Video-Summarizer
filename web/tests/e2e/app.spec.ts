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

  await expect(page.getByText("Mock Video Title")).toBeVisible();
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
