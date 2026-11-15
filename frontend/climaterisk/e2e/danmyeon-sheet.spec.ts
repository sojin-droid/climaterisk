// Gate for the 단면 asset sheet (written before the view). The bundled sheet DM-12 (성수동1가)
// is served by GET /api/sheets/DM-12; the view draws plan, section, frequency and detail as
// labelled SVGs, with the 결재란 (stamps) and the title block. Geographer rules: north arrow,
// scale bar, graticule labels, legend and sources must be on the sheet.
import { expect, test } from "@playwright/test";

test("단면 sheet: 성수동 river-flood 삼면도 from the bundled sheet", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "도면" }).click();
  await page.getByRole("button", { name: /DM-12/ }).click();

  const sheet = page.getByRole("region", { name: "자산 도면 DM-12" });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("heading", { level: 1 })).toContainText("2–5 m");

  // the four drawings
  for (const name of ["평면", "단면", "곡선", "상세"]) {
    await expect(sheet.getByRole("img", { name: new RegExp(`^${name}`) })).toBeVisible();
  }
  // map furniture on the plan
  const plan = sheet.getByRole("img", { name: /^평면/ });
  await expect(plan.getByText("N", { exact: true })).toBeVisible();
  await expect(plan.getByText("1 km")).toBeVisible();
  await expect(plan.getByText(/127\.0\d°E/).first()).toBeVisible();
  // section: vertical exaggeration and the cut ends
  const section = sheet.getByRole("img", { name: /^단면/ });
  await expect(section.getByText("A", { exact: true }).first()).toBeVisible();
  await expect(section.getByText("A′", { exact: true }).first()).toBeVisible();

  // 결재란: author T2, review 2/3, approve empty
  const stamps = sheet.getByRole("group", { name: "결재란" });
  await expect(stamps).toContainText("T2");
  await expect(stamps).toContainText("2/3");
  await expect(stamps).toContainText("빈칸");

  // title block, legend, sources
  await expect(sheet).toContainText("DM-12");
  await expect(sheet).toContainText("수직 과장 ×46");
  await expect(sheet.getByRole("list", { name: "범례" })).toContainText("5 m 이상");
  await expect(sheet).toContainText("공공누리 제4유형");
});
