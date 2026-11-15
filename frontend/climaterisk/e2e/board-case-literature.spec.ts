// Board gate (reviewer-written; agents may not edit it): the case page shows the twin's
// literature check (summary.literature_check) as a diagnostic table. It is a sub check:
// never a second result, never tuned to, and a disagreement is shown as a warning.
//
// UI contract:
// - `case-literature` exists only when summary.literature_check exists.
// - It shows the citation, "<n_rows>개 중 <n_ok>개 일치", and the sentence
//   "진단용 점검이며 결과를 관측에 맞추어 조정하지 않았습니다".
// - warning true → role="alert" and the text "문헌 점검 경고".
// - one row `case-literature-row-<id>` per row: label_ko, observed, model, verdict
//   ("일치" / "불일치"). depth_at_time values print with 2 decimals and " m";
//   overtopped_by values print as HH:MM (from the ISO string).
// - `case-literature-clock` shows clock_start as "2022-09-05 22:30" and the word "가정".
// - Figures are a list: every config figure is requested; clicking a figure opens the same
//   image URL in `case-figure-zoom` (a dialog), closed with the button "닫기".
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const PALETTES = JSON.parse(
  readFileSync(new URL("../../../assets/libraries/board_palettes.json", import.meta.url), "utf-8"),
);

const LIST = [
  { id: "pohang", name: "포항 냉천 · 태풍 힌남노 (2022-09-06)", peril: "pluvial_river_flood", lon: 129.4015, lat: 35.9936, cell_m: 30, badge: "미검증", available: true },
];
const LIT = {
  source: { citation: "Hwang, Kang, Jin, Lee (2024) J. Korea Water Resour. Assoc. 57(7), 451-460", doi: "10.3741/JKWRA.2024.57.7.451", license: "CC BY" },
  clock_start: "2022-09-05T22:30",
  rows: [
    { id: "indeok_overtopped", kind: "overtopped_by", label_ko: "인덕교 월류", unit: "시각", observed: "2022-09-06T06:20", model: "2022-09-06T05:33", diff: -0.78, ok: true },
    { id: "apt_0630", kind: "depth_at_time", label_ko: "아파트 입구 수심 06:30", unit: "m", observed: 0.3, model: 1.062, diff: 0.762, ok: false },
    { id: "apt_0645", kind: "depth_at_time", label_ko: "아파트 입구 수심 06:45", unit: "m", observed: 0.5, model: 1.056, diff: 0.556, ok: false },
  ],
  n_ok: 1,
  n_rows: 3,
  warning: true,
};
const base = {
  ...LIST[0],
  source_doc: "docs/reports/case_pohang_ko.md",
  figures: [
    { file: "max_depth_map.png", caption_ko: "최대 침수심" },
    { file: "fig_long_profile.png", caption_ko: "본류 종단면 (의심 셀 표시)" },
  ],
  highlights: [{ label_ko: "유역 면적", unit: "km²", value: 83.39 }],
  can_say: ["a"],
  cannot_say: ["b"],
  next_data: ["c"],
  assumptions: ["A1 가정"],
  n_assumptions: 1,
  assumptions_complete: true,
};
const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);

async function open(page: import("@playwright/test").Page, summary: Record<string, unknown>) {
  await page.route("**/api/board/palettes", (r) => r.fulfill({ json: PALETTES }));
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/board/cases", (r) => r.fulfill({ json: LIST }));
  await page.route("**/api/board/cases/pohang", (r) => r.fulfill({ json: { ...base, summary } }));
  await page.route("**/api/board/cases/*/figures/*", (r) => r.fulfill({ contentType: "image/png", body: PNG_1PX }));
  await page.goto("/?view=board");
  await page.getByTestId("case-open-pohang").click();
  await page.getByTestId("case-transition").getByRole("button", { name: "열기" }).click();
  await expect(page.getByTestId("case-page")).toBeVisible();
}

test("board case: literature check table with warning, figure zoom", async ({ page }) => {
  await open(page, { check: { warning: false }, literature_check: LIT });
  const lit = page.getByTestId("case-literature");
  await expect(lit).toBeVisible();
  await expect(lit).toHaveAttribute("role", "alert");
  await expect(lit).toContainText("문헌 점검 경고");
  await expect(lit).toContainText("Hwang, Kang, Jin, Lee (2024)");
  await expect(lit).toContainText("3개 중 1개 일치");
  await expect(lit).toContainText("진단용 점검이며 결과를 관측에 맞추어 조정하지 않았습니다");
  const clock = page.getByTestId("case-literature-clock");
  await expect(clock).toContainText("2022-09-05 22:30");
  await expect(clock).toContainText("가정");

  const r1 = page.getByTestId("case-literature-row-indeok_overtopped");
  await expect(r1).toContainText("인덕교 월류");
  await expect(r1).toContainText("06:20");
  await expect(r1).toContainText("05:33");
  await expect(r1).not.toContainText("불일치");
  await expect(r1).toContainText("일치");
  const r2 = page.getByTestId("case-literature-row-apt_0630");
  await expect(r2).toContainText("0.30 m");
  await expect(r2).toContainText("1.06 m");
  await expect(r2).toContainText("불일치");
  await expect(page.locator('[data-testid^="case-literature-row-"]')).toHaveCount(3);
  // the main/sub check line of the twin itself is still there and unchanged
  await expect(page.getByTestId("case-check")).toContainText("경고 없음");

  await expect(page.getByTestId("case-figure-zoom")).toHaveCount(0);
  await page.getByAltText("본류 종단면 (의심 셀 표시)").click();
  const zoom = page.getByTestId("case-figure-zoom");
  await expect(zoom).toBeVisible();
  await expect(zoom.locator("img")).toHaveAttribute("src", /\/api\/board\/cases\/pohang\/figures\/fig_long_profile\.png$/);
  await zoom.getByRole("button", { name: "닫기" }).click();
  await expect(page.getByTestId("case-figure-zoom")).toHaveCount(0);
});

test("board case: no literature check → no block; all rows agree → no alert", async ({ page }) => {
  await open(page, { check: { warning: false } });
  await expect(page.getByTestId("case-literature")).toHaveCount(0);
});

test("board case: literature check without warning is not an alert", async ({ page }) => {
  const ok = { ...LIT, rows: LIT.rows.map((r) => ({ ...r, ok: true })), n_ok: 3, warning: false };
  await open(page, { literature_check: ok });
  const lit = page.getByTestId("case-literature");
  await expect(lit).toContainText("3개 중 3개 일치");
  await expect(lit).not.toHaveAttribute("role", "alert");
  await expect(lit).not.toContainText("문헌 점검 경고");
});
