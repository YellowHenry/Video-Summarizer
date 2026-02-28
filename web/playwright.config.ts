import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";

const STORAGE_STATE_PATH = "./.auth/storage-state.json";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css"
    }
  },
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    viewport: { width: 1366, height: 900 },
    deviceScaleFactor: 1,
    timezoneId: "UTC",
    locale: "en-US",
    colorScheme: "light",
    reducedMotion: "reduce",
    storageState: existsSync(STORAGE_STATE_PATH) ? STORAGE_STATE_PATH : undefined
  },
  webServer: {
    command: "npm run preview:start",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 120_000
  }
});
