// Import a portfolio from the coordinates template, then assess it on the Models tab.
import { expect, test } from "@playwright/test";

const CSV = [
  "facility_id,facility_name,lat,lon,asset_value_usd,property_type",
  "KR-001,Seoul office,37.5665,126.978,100000000,office",
  "KR-002,Busan warehouse,35.1796,129.0756,60000000,warehouse",
  "",
].join("\n");

test("import assets (csv template) → My Assets → run → Excel download", async ({ page }) => {
  await page.goto("/"); // the Map tab, where the import panel lives

  await page.locator('input[type="file"]').setInputFiles({
    name: "portfolio.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(CSV, "utf8"),
  });
  await expect(page.getByText(/portfolio\.csv: 2 rows — 2 added, 0 replaced/)).toBeVisible();

  await page.getByRole("button", { name: "Models" }).click();
  await page.getByText("My Assets").click();
  await page.getByRole("button", { name: "Select all" }).click();
  await page.getByRole("button", { name: "RUN ASSESSMENT" }).click();

  const download = page.getByRole("link", { name: "Download Excel" });
  await expect(download).toBeVisible({ timeout: 60_000 });
  expect(await download.getAttribute("download")).toMatch(/^Physical_Risk_Report_2_Assets_\d{8}\.xlsx$/);

  const resp = await page.request.get((await download.getAttribute("href"))!);
  expect(resp.ok()).toBeTruthy();
  expect((await resp.body()).subarray(0, 2).toString("latin1")).toBe("PK");
});

test("a bad row refuses the whole file and says which line", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "bad.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("facility_id,lat,lon,value\nA,37.5,127,1\nB,999,127,1\n", "utf8"),
  });
  await expect(page.getByText(/file refused/)).toBeVisible();
  await expect(page.getByText(/line 3/)).toBeVisible();
});
