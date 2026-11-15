// Board gate (reviewer-written; agents may not edit it): the case page shows a literature check
// that has no clock (heat and design-rain cases). Same diagnostic block as Pohang
// (board-case-literature.spec.ts), generalised:
//
// UI contract:
// - no clock_start → no `case-literature-clock`.
// - conditions_ko, when present, is shown verbatim in `case-literature-conditions`.
// - row values by unit: numbers with unit "°C" or "mm" print with 1 decimal + " " + unit;
//   unit "m" with 2 decimals + " m"; a string value prints as-is followed by " " + unit
//   (e.g. "> 0 °C"). Existing Pohang kinds keep their format.
// - a row with `band` [lo, hi] shows it in its model cell as "(범위 lo–hi unit)" with the same
//   decimals.
// - a row with note_ko shows it in `case-literature-note-<id>`.
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const PALETTES = JSON.parse(
  readFileSync(new URL("../../../assets/libraries/board_palettes.json", import.meta.url), "utf-8"),
);

const LIST = [
  { id: "yeongdeungpo", name: "영등포 쪽방촌 · 폭염", peril: "heat", lon: 126.909, lat: 37.517, cell_m: 5, badge: "미검증", available: true },
];
const COND = "논문은 2018-06-19 10시, 이 사례는 8월 오후";
const LIT = {
  source: { citation: "Kim, Yun, Park, Heo, Lee (2022) Remote Sens. 14(2), 260", doi: "10.3390/rs14020260", license: "CC BY 4.0" },
  conditions_ko: COND,
  rows: [
    { id: "mrt_svf_contrast", kind: "contrast_c", label_ko: "MRT 차이 SVF", unit: "°C", observed: "> 0", model: 7.234, ok: true },
    { id: "mrt_sunlit_open", kind: "above_c", label_ko: "햇빛 개방 칸 MRT", unit: "°C", observed: 59.4, model: 66.06, ok: true, note_ko: "더운 날이라 유리한 비교" },
    { id: "rain_2h_T10", kind: "rain_mm", label_ko: "2시간 10년", unit: "mm", observed: 111.2, model: 101.47, band: [92.34, 109.93], ok: false },
    { id: "exit6_depth_at_113mm", kind: "depth_at_rain", label_ko: "6번 출구", unit: "m", observed: 0.11, model: 0.074, ok: false },
  ],
  n_ok: 2,
  n_rows: 4,
  warning: true,
};
const base = {
  ...LIST[0],
  source_doc: "docs/reports/case_yeongdeungpo_ko.md",
  figures: [{ file: "utci_map.png", caption_ko: "UTCI 지도" }],
  highlights: [{ label_ko: "대상지 평균 UTCI", unit: "°C", value: 47.3 }],
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

test("board case: literature check without a clock (units, band, note, conditions)", async ({ page }) => {
  await page.route("**/api/board/palettes", (r) => r.fulfill({ json: PALETTES }));
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/board/cases", (r) => r.fulfill({ json: LIST }));
  await page.route("**/api/board/cases/yeongdeungpo", (r) =>
    r.fulfill({ json: { ...base, summary: { check: { warning: false }, literature_check: LIT } } }),
  );
  await page.route("**/api/board/cases/*/figures/*", (r) => r.fulfill({ contentType: "image/png", body: PNG_1PX }));
  await page.goto("/?view=board");
  await page.getByTestId("case-open-yeongdeungpo").click();
  await page.getByTestId("case-transition").getByRole("button", { name: "열기" }).click();
  await expect(page.getByTestId("case-page")).toBeVisible();

  const lit = page.getByTestId("case-literature");
  await expect(lit).toHaveAttribute("role", "alert");
  await expect(lit).toContainText("4개 중 2개 일치");
  await expect(lit).toContainText("진단용 점검이며 결과를 관측에 맞추어 조정하지 않았습니다");
  await expect(page.getByTestId("case-literature-clock")).toHaveCount(0);
  await expect(page.getByTestId("case-literature-conditions")).toHaveText(COND);

  const c = page.getByTestId("case-literature-row-mrt_svf_contrast");
  await expect(c).toContainText("> 0 °C");
  await expect(c).toContainText("7.2 °C");
  const a = page.getByTestId("case-literature-row-mrt_sunlit_open");
  await expect(a).toContainText("59.4 °C");
  await expect(a).toContainText("66.1 °C");
  await expect(page.getByTestId("case-literature-note-mrt_sunlit_open")).toContainText("더운 날이라 유리한 비교");
  const r = page.getByTestId("case-literature-row-rain_2h_T10");
  await expect(r).toContainText("111.2 mm");
  await expect(r).toContainText("101.5 mm");
  await expect(r).toContainText("(범위 92.3–109.9 mm)");
  await expect(r).toContainText("불일치");
  const e = page.getByTestId("case-literature-row-exit6_depth_at_113mm");
  await expect(e).toContainText("0.11 m");
  await expect(e).toContainText("0.07 m");
  await expect(page.locator('[data-testid^="case-literature-row-"]')).toHaveCount(4);
});
