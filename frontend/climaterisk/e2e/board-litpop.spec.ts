// Board step 7 gate (reviewer-written; agents may not edit it): LitPop exposure weighting.
// The layer file carries meta.litpop (copied by the build from visual_board.json "litpop":
// banner_ko, size_legend_usd, dataset, res_arcsec, check) and per piece exposure
// {litpop_value_usd, litpop_eal_usd}. Hazard colours never change; LitPop is drawn as
// HOLLOW circles (no fill) at piece centroids, area proportional to litpop_eal_usd.
//
// UI contract:
// - checkbox `litpop-toggle` labelled "노출 가중(LitPop)" exists only when meta.litpop exists;
//   unchecked by default (default off), and then no banner and no circles.
// - checked: `litpop-banner` shows meta.litpop.banner_ko verbatim; circles carry the class
//   "litpop-circle" (one per piece with litpop_eal_usd > 0, none for 0 or missing) and are
//   hollow (fill-opacity 0 or fill none); `litpop-legend` lists every size_legend_usd step.
// - the pick panel's exposure table shows "LitPop 노출 자산" and "LitPop 연평균 손실" rows.
// - the sub check warning (meta.litpop.check.warning) is visible as `litpop-check`.
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const PALETTES = JSON.parse(
  readFileSync(new URL("../../../assets/libraries/board_palettes.json", import.meta.url), "utf-8"),
);

const BANNER = "노출 가중(LitPop) 켜짐 — 테스트 문구 150″ · LitPop은 공개 모델 추정값(야간 조명 × 인구)";

const base = {
  peril: "river_flood",
  quantity: "water_depth_m",
  units: "m",
  requested_periods: [10, 100],
  periods_kept: [10, 100],
  max_supported: 460,
  record_years: 920,
  resolution_arcsec: 150,
  cell_km_ns: 4.64,
  cell_km_ew: 3.73,
  hazard_source: "fixture",
  boundary_source: "fixture",
  use_tier: "I",
  use_tier_reason: "상대 비교와 순위용",
};
const WITH = {
  ...base,
  layer: "river_flood_hist_kor",
  label: "하천 홍수 · 과거",
  litpop: {
    dataset: "LitPop_150arcsec_KOR v3",
    res_arcsec: 150,
    banner_ko: BANNER,
    size_legend_usd: [1e5, 1e6, 1e7],
    n_points: 3,
    n_assigned: 3,
    value_assigned_usd: 6e9,
    check: { n_compared: 2, disagree_share: 0.5, warning: true },
  },
};
const WITHOUT = { ...base, layer: "river_flood_rcp85_2010_2030_kor", label: "하천 홍수 · RCP8.5" };

const sq = (x: number, y: number, s: number) => [[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]];
const feat = (id: number, x: number, eal: number | null) => ({
  type: "Feature",
  geometry: { type: "Polygon", coordinates: sq(x, 36, 0.5) },
  properties: {
    id, centroid: id, code: "11220", name: "서초구", value: 1.0,
    exposure: eal === null ? null : { litpop_value_usd: 2e9, litpop_eal_usd: eal },
  },
});

test("board: LitPop toggle off by default → on shows banner, hollow circles, legend", async ({ page }) => {
  await page.route("**/api/board/palettes", (r) => r.fulfill({ json: PALETTES }));
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: [WITH, WITHOUT] }));
  await page.route("**/api/board/layers/*/geojson?*", (r) =>
    r.fulfill({
      json: { type: "FeatureCollection", features: [feat(0, 127.0, 5e6), feat(1, 127.6, 2e5), feat(2, 128.2, 0), feat(3, 128.8, null)] },
    }),
  );
  await page.route("**/api/board/layers/*/lookup?*", (r) =>
    r.fulfill({
      json: { id: 0, code: "11220", name: "서초구", centroid: 0, cell_km_ns: 4.64, cell_km_ew: 3.73,
        values: { "10": 0.5, "100": 1.0 }, exposure: { litpop_value_usd: 2e9, litpop_eal_usd: 5e6 } },
    }),
  );

  await page.goto("/?view=board");
  const toggle = page.getByTestId("litpop-toggle");
  await expect(toggle).toBeVisible();
  await expect(toggle).not.toBeChecked();
  await expect(page.getByTestId("litpop-banner")).toHaveCount(0);
  await expect(page.locator(".litpop-circle")).toHaveCount(0);

  await toggle.check();
  await expect(page.getByTestId("litpop-banner")).toHaveText(BANNER);
  const circles = page.locator(".litpop-circle");
  await expect(circles).toHaveCount(2);
  for (let i = 0; i < 2; i++) {
    const c = circles.nth(i);
    const fill = await c.getAttribute("fill");
    const op = await c.getAttribute("fill-opacity");
    expect(fill === "none" || op === "0").toBeTruthy();
  }
  const legend = page.getByTestId("litpop-legend");
  await expect(legend.locator("li")).toHaveCount(3);
  await expect(page.getByTestId("litpop-check")).toBeVisible();

  await page.locator(".leaflet-container").click({ position: { x: 400, y: 300 } });
  const panel = page.getByTestId("pick-panel");
  await expect(panel).toContainText("LitPop 노출 자산");
  await expect(panel).toContainText("LitPop 연평균 손실");

  // a layer without LitPop has no toggle
  await page.getByLabel("재해 층 · Layer").selectOption(WITHOUT.layer);
  await expect(page.getByTestId("litpop-toggle")).toHaveCount(0);
  await expect(page.locator(".litpop-circle")).toHaveCount(0);
});
