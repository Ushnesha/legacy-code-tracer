import { defineConfig, devices } from "@playwright/test";

// Needs data/tinyxml2.db:  make extract REPO=repos/tinyxml2 && make enrich REPO=repos/tinyxml2 && make load REPO=repos/tinyxml2
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 0,
  reporter: "list",
  // The speed test records a trace every time, so the frames can be inspected afterwards.
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  projects: [
    { name: "chromium", testIgnore: /speed\.spec/, use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    // Speed is measured with nothing else running, so it runs after everything else has finished. A frame is 16.7 ms,
    // so "under 100 ms" leaves room for only 5 slow ones: a retry covers a noisy machine, not a slow app.
    { name: "speed", testMatch: /speed\.spec/, dependencies: ["chromium"], fullyParallel: false, retries: 2, use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      command: "uv run uvicorn server.main:app --port 8000",
      cwd: "..",
      url: "http://localhost:8000/api/repos",
      reuseExistingServer: true,
      timeout: 60_000,
    },
    { command: "pnpm dev --strictPort", url: "http://localhost:5173", reuseExistingServer: true, timeout: 60_000 },
  ],
});
