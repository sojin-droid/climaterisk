// The V0.2 non-expert flow: Models tab → pick municipalities → RUN → Download Excel.
import { expect, test } from "@playwright/test";

test("municipalities → hazards → run → Excel download", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Models" }).click();

  // Municipalities is the default target; narrow the list and take everything that matches.
  await page.getByLabel("Search municipality").fill("광주");
  await page.getByRole("button", { name: "Select all" }).click();
  await expect(page.getByLabel("selected municipalities")).not.toContainText(/\b0 /);

  await page.getByRole("button", { name: "RUN ASSESSMENT" }).click();

  // The fake worker answers at once; the UI polls every two seconds.
  const download = page.getByRole("link", { name: "Download Excel" });
  await expect(download).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel("representative point warning")).toBeVisible();

  const name = await download.getAttribute("download");
  expect(name).toMatch(/^Municipality_Physical_Risk_Report_\d+_Municipalities_\d{8}\.xlsx$/);

  const href = await download.getAttribute("href");
  expect(href).toMatch(/\/physical-risk-export\.xlsx$/);
  const resp = await page.request.get(href!);
  expect(resp.ok()).toBeTruthy();
  const body = await resp.body();
  expect(body.subarray(0, 2).toString("latin1")).toBe("PK"); // a real xlsx (zip) file
  expect(body.length).toBeGreaterThan(5_000);
});
