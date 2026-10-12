// Browser (end-to-end) tests for the two flows a non-expert follows:
//   municipalities → hazards → RUN → Download Excel, and
//   import assets (template) → My Assets → RUN → Download Excel.
//
// Both servers are started here on their own ports (8199 / 5199) so a running dev stack is
// never touched. The backend is pointed at a *fake* worker (tests/e2e/fake_worker.py, run by
// the backend's own interpreter) — no conda env, no network, deterministic in milliseconds.
// The real CLIMADA path is covered by the worker-env regression tests, not here.
import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)); // package.json is "type": "module"
const REPO = path.resolve(HERE, "..", "..");
const BACKEND_PORT = 8199;
const FRONTEND_PORT = 5199;
const VENV_PYTHON = path.join(
  REPO,
  ".venv",
  ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]),
);
const PYTHONPATH = [path.join(REPO, "tests", "e2e"), path.join(REPO, "src")].join(path.delimiter);

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: [
    {
      command: `uv run uvicorn climaterisk.api.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: REPO,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        CLIMATERISK_DATA_DIR: "data/e2e",
        CLIMATERISK_BACKEND_PORT: String(BACKEND_PORT),
        CLIMATERISK_WORKER_PYTHON: VENV_PYTHON,
        CLIMATERISK_WORKER_MODULE: "fake_worker",
        PYTHONPATH,
        PYTHONUTF8: "1",
      },
    },
    {
      // --host 127.0.0.1: newer Node resolves "localhost" to ::1 only, which the IPv4 health
      // URL below would never reach.
      command: `npx vite --host 127.0.0.1 --port ${FRONTEND_PORT} --strictPort`,
      cwd: HERE,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { CLIMATERISK_BACKEND_PORT: String(BACKEND_PORT) },
    },
  ],
});
