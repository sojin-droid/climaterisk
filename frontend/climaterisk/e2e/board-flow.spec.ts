// Visual board: layer picker -> return-period change -> map click -> side panel.
// The /api/board/* answers are fixtures served by Playwright (page.route): the board layer
// files live in the external data root, which the e2e backend does not have.
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// The real colour authority (assets/libraries/board_palettes.json), served as GET /api/board/palettes.
const PALETTES = JSON.parse(
  readFileSync(new URL("../../../assets/libraries/board_palettes.json", import.meta.url), "utf-8"),
);
const routePalettes = (page: Page) => page.route("**/api/board/palettes", (r) => r.fulfill({ json: PALETTES }));

const META = {
  layer: "river_flood_hist_kor",
  peril: "river_flood",
  quantity: "water_depth_m",
  units: "m",
  requested_periods: [2, 5, 10, 25, 50, 100, 250, 500],
  periods_kept: [2, 5, 10, 25, 50, 100, 250],
  max_supported: 460,
  record_years: 920,
  resolution_arcsec: 150,
  cell_km_ns: 4.64,
  cell_km_ew: 3.73,
  hazard_source: "CLIMADA Data API river_flood historical (fixture)",
  boundary_source: "SGIS 시군구 2025_2Q (fixture)",
  use_tier: "I",
  use_tier_reason: "상대 비교와 순위용",
};

const sq = (x: number, y: number, s: number) => [[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]];
const geojson = (rp: number) => ({
  type: "FeatureCollection",
  features: [
    { type: "Feature", geometry: { type: "Polygon", coordinates: sq(127.5, 36, 1) },
      properties: { id: 0, centroid: 0, code: "11220", name: "서초구", value: rp >= 100 ? 1.5 : 0 } },
    { type: "Feature", geometry: { type: "Polygon", coordinates: sq(128.6, 36, 1) },
      properties: { id: 1, centroid: 1, code: "11230", name: "강남구", value: null } },
  ],
});

test("board: picker → return period → click → panel", async ({ page }) => {
  const requested: string[] = [];
  await routePalettes(page);
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: [META] }));
  await page.route("**/api/board/layers/*/geojson?*", (r) => {
    const rp = Number(new URL(r.request().url()).searchParams.get("rp"));
    requested.push(String(rp));
    return r.fulfill({ json: geojson(rp) });
  });
  await page.route("**/api/board/layers/*/lookup?*", (r) =>
    r.fulfill({
      json: { id: 0, code: "11220", name: "서초구", centroid: 0, cell_km_ns: 4.64, cell_km_ew: 3.73,
        values: { "2": 0, "5": 0, "10": 0, "25": 0, "50": 0.4, "100": 1.5, "250": 2.2 } },
    }),
  );
  await page.route("**/api/board/layers/*/regions/*", (r) =>
    r.fulfill({
      json: { code: "11220", name: "서초구", n_pieces: 8,
        by_rp: { "2": { min: 0, max: 0 }, "100": { min: 0, max: 1.5 } } },
    }),
  );

  await page.goto("/?view=board");
  await expect(page.getByTestId("badge-resolution")).toContainText("재해 격자 150″ ≈ 4.6×3.7 km");
  await expect(page.getByTestId("badge-tier")).toContainText("I — 상대 비교와 순위용");
  await expect(page.getByTestId("source-line")).toContainText("SGIS");
  const legend = page.getByTestId("legend");
  await expect(legend).toContainText("자료 없음 · no data");
  await expect(legend).toContainText("0 · 영향 없음");
  // every class of the quantity is listed, from the fixed breaks (not from the data on screen)
  for (const label of ["≤ 0.25 m", "0.25 – 0.5 m", "0.5 – 1 m", "1 – 2 m", "2 – 4 m", "> 4 m"]) {
    await expect(legend).toContainText(label);
  }
  await expect(page.getByTestId("legend-title")).toContainText("침수심 (m)");
  await expect(page.getByTestId("legend-basis")).toContainText("JRC");

  // 500 y exceeds the 460 y cap: shown, disabled, with the reason as tooltip.
  const p500 = page.getByRole("button", { name: "500년" });
  await expect(p500).toBeDisabled();
  await expect(p500).toHaveAttribute("title", "계산 불가: 사건 기록 상한 460년");

  await expect(page.getByRole("button", { name: "100년", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "25년" }).click();
  await expect(page.getByRole("button", { name: "25년" })).toHaveAttribute("aria-pressed", "true");
  expect(requested).toContain("25");

  await page.locator(".leaflet-container").click({ position: { x: 400, y: 300 } });
  const panel = page.getByTestId("pick-panel");
  await expect(panel).toContainText("서초구");
  await expect(panel).toContainText("11220");
  await expect(panel).toContainText("250년");
  await expect(panel).toContainText("이 지점은 격자 한 칸(약 4.6×3.7 km) 값 하나를 읽습니다. 칸 안 어디를 찍어도 같은 값입니다.");

  await page.getByRole("button", { name: "서초구 시군구 요약" }).click();
  await expect(page.getByTestId("region-panel")).toContainText("조각 수 8개");
  await expect(page.getByTestId("region-panel")).not.toContainText(/mean|평균 *[0-9]/);

  await expect(page.getByText("사례 트윈: 강남역 (미검증)")).toBeVisible();
});

const DROUGHT_META = {
  layer: "drought_spei3_hist_kor",
  peril: "drought",
  key_kind: "metric",
  default_key: "y2022",
  keys: [
    { key: "freq_ge1", label: "관심(SPEI-3 ≤ −1) 이상 발생 빈도 1991–2020", units: "연 비율", scale: "share", quantity: "share_of_years", comparable_across_regions: false },
    { key: "freq_ge2", label: "주의(SPEI-3 ≤ −1.5) 이상 발생 빈도 1991–2020", units: "연 비율", scale: "share", quantity: "share_of_years", comparable_across_regions: false },
    { key: "freq_ge3", label: "경계(SPEI-3 ≤ −2) 이상 발생 빈도 1991–2020", units: "연 비율", scale: "share", quantity: "share_of_years", comparable_across_regions: false },
    { key: "y2022", label: "2022년 5–9월 최소 SPEI-3", units: "-", scale: "spei", quantity: "spei_stage" },
  ],
  units: "-",
  resolution_arcsec: 150,
  cell_km_ns: 4.64,
  cell_km_ew: 3.76,
  hazard_source: "TerraClimate v1.1 (fixture)",
  boundary_source: "SGIS 시군구 2025_2Q (fixture)",
  use_tier: "S",
  use_tier_reason: "위험도 지표만, 피해함수 없음",
  notes: ["논(관개)은 SPEI로 평가하지 않음 — 저수율 지표 필요", "가뭄지수와 노출 농지 면적이며 손실(피해액)이 아님", "빈도는 1991–2020 같은 기간으로 맞추고 센 값(in-sample)", "장기 빈도는 격자마다 표준화한 지수의 같은 기간 빈도라 어디서나 비슷하게 나옵니다 — 지역 비교용이 아닙니다. 지역 비교는 연도별 지도로 하세요."],
};

test("board: drought layer → key selector, badges, exposure", async ({ page }) => {
  const keys: string[] = [];
  await routePalettes(page);
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: [META, DROUGHT_META] }));
  await page.route("**/api/board/layers/*/geojson?*", (r) => {
    const q = new URL(r.request().url()).searchParams;
    keys.push(q.get("key") ?? `rp${q.get("rp")}`);
    return r.fulfill({
      json: {
        type: "FeatureCollection",
        features: [
          { type: "Feature", geometry: { type: "Polygon", coordinates: sq(127.5, 36, 1) },
            properties: { id: 0, centroid: 0, code: "11220", name: "서초구", value: 0.2, exposure: null } },
        ],
      },
    });
  });
  await page.route("**/api/board/layers/*/lookup?*", (r) =>
    r.fulfill({
      json: { id: 0, code: "11220", name: "서초구", centroid: 0, cell_km_ns: 4.64, cell_km_ew: 3.76,
        key_kind: "metric",
        values: { freq_ge1: 0.3, freq_ge2: 0.2, freq_ge3: 0, y2022: -1.7 },
        exposure: { upland_ha: 10, orchard_ha: 2, rice_ha: 5, greenhouse_ha: 1 } },
    }),
  );
  await page.route("**/api/board/layers/*/regions/*", (r) =>
    r.fulfill({
      json: { code: "11220", name: "서초구", n_pieces: 8, by_rp: {},
        by_key: { freq_ge1: { min: 0.1, max: 0.3 }, freq_ge2: { min: 0, max: 0.2 }, freq_ge3: null, y2022: { min: -1.7, max: -0.3 } },
        exposure_sum: { upland_ha: 11.5, orchard_ha: 2, rice_ha: 5, greenhouse_ha: 1 } },
    }),
  );

  await page.goto("/?view=board");
  await page.getByLabel("재해 층 · Layer").selectOption("drought_spei3_hist_kor");
  await expect(page.getByTestId("badge-resolution")).toContainText("기상 격자 1/24° ≈ 4.6×3.8 km");
  await expect(page.getByTestId("badge-tier")).toContainText("S — 위험도 지표만");
  await expect(page.getByTestId("badge-notes")).toContainText("논(관개)은 SPEI로 평가하지 않음");
  await expect(page.getByTestId("badge-notes")).toContainText("손실(피해액)이 아님");
  await expect(page.getByTestId("badge-notes")).toContainText("in-sample");
  // default key = meta.default_key (latest severe year), shown with the SPEI stage legend
  await expect.poll(() => keys).toContain("y2022");
  expect(keys).not.toContain("freq_ge2");
  await expect(page.getByLabel("연도별 · Single year")).toHaveValue("y2022");
  await expect(page.getByTestId("legend")).toContainText("경계 이상: SPEI-3 ≤ −2");
  await expect(page.getByTestId("legend")).toContainText("없음: SPEI-3 > −1");
  await expect(page.getByTestId("legend")).toContainText("자료 없음 · no data");
  await expect(page.getByTestId("freq-hint")).toHaveCount(0);

  // long-run frequency buttons carry the "not for region comparison" tag and hint
  await expect(page.getByTestId("not-comparable-tag")).toHaveCount(3);
  await expect(page.getByTestId("not-comparable-tag").first()).toContainText("지역 비교용 아님");
  await page.getByRole("button", { name: /주의\(SPEI-3 ≤ −1.5\)/ }).click();
  await expect(page.getByRole("button", { name: /주의\(SPEI-3 ≤ −1.5\)/ })).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => keys).toContain("freq_ge2");
  await expect(page.getByTestId("freq-hint")).toContainText("지역 비교는 연도별 지도로 하세요");
  await expect(page.getByTestId("legend")).toContainText("0.1 – 0.2 연 비율");
  await expect(page.getByTestId("legend-basis")).toContainText("지역 비교용이 아님");

  await page.getByLabel("연도별 · Single year").selectOption("y2022");
  await expect(page.getByTestId("legend")).toContainText("경계 이상: SPEI-3 ≤ −2");

  await page.locator(".leaflet-container").click({ position: { x: 400, y: 300 } });
  const panel = page.getByTestId("pick-panel");
  await expect(panel).toContainText("2022년 5–9월 최소 SPEI-3");
  await expect(panel).toContainText("-1.70 · 주의");
  await expect(panel.getByTestId("exposure")).toContainText("논");
  await expect(panel.getByTestId("exposure")).toContainText("미평가(면적만)");

  await page.getByRole("button", { name: "서초구 시군구 요약" }).click();
  const region = page.getByTestId("region-panel");
  await expect(region.getByTestId("exposure-sum")).toContainText("밭 합계");
  await expect(region.getByTestId("exposure-sum")).toContainText("11.5 ha");
  await expect(region).not.toContainText(/mean|평균 *[0-9]/);
  await expect(page.getByText("사례 트윈: 강남역 (미검증)")).toBeVisible();
});

const CHANGE_META = {
  layer: "river_flood_change_rcp85_kor",
  label: "하천 홍수 변화 · RCP8.5 2030–2050 ÷ 2010–2030",
  peril: "river_flood",
  quantity: "change",
  base_layer: "river_flood_rcp85_2010_2030_kor",
  future_layer: "river_flood_rcp85_2030_2050_kor",
  units: "ratio",
  periods_kept: [10, 100],
  max_supported: 240,
  record_years: 480,
  resolution_arcsec: 150,
  cell_km_ns: 4.64,
  cell_km_ew: 3.73,
  hazard_source: "ratio of two CLIMADA Data API river_flood sets (fixture)",
  boundary_source: "SGIS 시군구 2025_2Q (fixture)",
  use_tier: "I",
  use_tier_reason: "시나리오 차이(같은 모형 사슬)",
};

test("board: change layer → grouped selector, ratio legend, status pieces, ×ratio lookup", async ({ page }) => {
  await routePalettes(page);
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: [CHANGE_META, META] }));
  await page.route("**/api/board/layers/*/geojson?*", (r) =>
    r.fulfill({
      json: {
        type: "FeatureCollection",
        features: [
          { type: "Feature", geometry: { type: "Polygon", coordinates: sq(127.5, 36, 1) },
            properties: { id: 0, centroid: 0, code: "11220", name: "서초구", value: 1.4, status: null } },
          { type: "Feature", geometry: { type: "Polygon", coordinates: sq(128.6, 36, 1) },
            properties: { id: 1, centroid: 1, code: "11230", name: "강남구", value: null, status: "base_zero" } },
          { type: "Feature", geometry: { type: "Polygon", coordinates: sq(129.7, 36, 1) },
            properties: { id: 2, centroid: 2, code: "11240", name: "송파구", value: null, status: "both_zero" } },
        ],
      },
    }),
  );
  await page.route("**/api/board/layers/*/lookup?*", (r) =>
    r.fulfill({
      json: { id: 0, code: "11220", name: "서초구", centroid: 0, cell_km_ns: 4.64, cell_km_ew: 3.73,
        values: { "10": null, "100": 1.4 }, status: { "10": "base_zero" } },
    }),
  );

  await page.goto("/?view=board");
  // the selector opens on an absolute layer; absolute and change layers are separate groups
  await expect(page.getByLabel("재해 층 · Layer")).toHaveValue("river_flood_hist_kor");
  await expect(page.locator("#board-layer optgroup")).toHaveCount(2);
  await expect(page.locator('#board-layer optgroup[label^="변화"] option')).toHaveCount(1);
  await page.getByLabel("재해 층 · Layer").selectOption("river_flood_change_rcp85_kor");

  const legend = page.getByTestId("legend");
  await expect(page.getByTestId("legend-title")).toContainText(PALETTES.change.label);
  for (const label of [...PALETTES.change.labels, PALETTES.shared.base_zero.label, PALETTES.shared.zero.label, PALETTES.shared.null.label]) {
    await expect(legend).toContainText(label);
  }
  await expect(page.getByTestId("legend-basis")).toContainText("PiYG");
  await expect(page.getByRole("alert")).toHaveCount(0);

  await page.locator(".leaflet-container").click({ position: { x: 400, y: 300 } });
  const panel = page.getByTestId("pick-panel");
  await expect(panel).toContainText("서초구");
  await expect(panel).toContainText("×1.40");
  await expect(panel).toContainText(PALETTES.shared.base_zero.label);
  await expect(panel).not.toContainText("Infinity");
});

const HEAT_META = {
  layer: "heat_days_kor",
  label: "폭염일수 (일 최고 ≥33 °C, 5–9월)",
  peril: "heatwave",
  key_kind: "metric",
  default_key: "obs_2000_2019",
  keys: [
    { key: "obs_2000_2019", label: "폭염일수 · 관측 2000–2019", units: "일/년", scale: "level", quantity: "heat_days_per_year" },
    { key: "ssp585_2051_2060_delta", label: "폭염일수 · SSP5-8.5 2051–2060 · 관측 + 모델 30년 변화량", units: "일/년", scale: "level", quantity: "heat_days_per_year" },
    { key: "ssp585_ratio", label: "폭염일수 변화 · SSP5-8.5 ÷ 관측", units: "배(미래 ÷ 기준)", scale: "ratio", quantity: "change" },
  ],
  units: "일/년",
  resolution_arcsec: 180,
  cell_km_ns: 5.57,
  cell_km_ew: 4.5,
  hazard_source: "기상청 남한상세 TAMAX (fixture)",
  boundary_source: "SGIS 시군구 2025_2Q (fixture)",
  use_tier: "S",
  use_tier_reason: "폭염 하자드, 기온 기준",
  notes: ["미래 값은 관측 일자료에 모델의 월별 변화량을 더한 델타법입니다."],
};

test("board: metric layer with level + ratio keys, peril-grouped selector, persons exposure", async ({ page }) => {
  const keys: string[] = [];
  await routePalettes(page);
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: [META, HEAT_META] }));
  await page.route("**/api/board/layers/*/geojson?*", (r) => {
    const q = new URL(r.request().url()).searchParams;
    keys.push(q.get("key") ?? `rp${q.get("rp")}`);
    const ratio = q.get("key") === "ssp585_ratio";
    return r.fulfill({
      json: {
        type: "FeatureCollection",
        features: [
          { type: "Feature", geometry: { type: "Polygon", coordinates: sq(127.5, 36, 1) },
            properties: { id: 0, centroid: 0, code: "11220", name: "서초구", value: ratio ? 1.8 : 12, status: null } },
          { type: "Feature", geometry: { type: "Polygon", coordinates: sq(128.6, 36, 1) },
            properties: { id: 1, centroid: 1, code: "11230", name: "강남구", value: null, status: ratio ? "base_zero" : null } },
        ],
      },
    });
  });
  await page.route("**/api/board/layers/*/lookup?*", (r) =>
    r.fulfill({
      json: { id: 0, code: "11220", name: "서초구", centroid: 0, cell_km_ns: 5.57, cell_km_ew: 4.5, key_kind: "metric",
        values: { obs_2000_2019: 12.3, ssp585_2051_2060_delta: 30.1, ssp585_ratio: null },
        status: { ssp585_ratio: "base_zero" },
        exposure: { pop_total: 123456, pop_65plus: 23456 } },
    }),
  );

  await page.goto("/?view=board");
  // selector: absolute layers grouped by peril
  await expect(page.locator('#board-layer optgroup[label="절대값 · Absolute — 하천 홍수"]')).toHaveCount(1);
  await expect(page.locator('#board-layer optgroup[label="절대값 · Absolute — 폭염"] option')).toHaveCount(1);
  await page.getByLabel("재해 층 · Layer").selectOption("heat_days_kor");
  await expect(page.getByTestId("badge-notes")).toContainText("델타법");
  await expect.poll(() => keys).toContain("obs_2000_2019");
  await expect(page.getByTestId("legend-title")).toContainText("폭염일수");
  // level keys and the ratio key are all buttons; the ratio key switches to the change legend
  await page.getByRole("button", { name: /변화 · SSP5-8.5|변화 · SSP5/ }).click();
  await expect.poll(() => keys).toContain("ssp585_ratio");
  await expect(page.getByTestId("legend-title")).toContainText(PALETTES.change.label);
  await expect(page.getByTestId("legend")).toContainText(PALETTES.shared.base_zero.label);

  await page.locator(".leaflet-container").click({ position: { x: 400, y: 300 } });
  const panel = page.getByTestId("pick-panel");
  await expect(panel.getByTestId("metric-values")).toContainText("12.3 일/년");
  await expect(panel.getByTestId("metric-values")).toContainText(PALETTES.shared.base_zero.label);
  await expect(panel.getByTestId("exposure")).toContainText("123,456 명");
  await expect(panel.getByTestId("exposure")).toContainText("65세 이상 인구");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

// ---- 시군구 panel + resolution ladder, case twins (fixtures; no board data on the e2e backend) ----
const LADDER = JSON.parse(
  readFileSync(new URL("../../../assets/libraries/visual_board.json", import.meta.url), "utf-8"),
).resolution_ladder;

const routeBoardBase = async (page: Page, layers: unknown[]) => {
  await routePalettes(page);
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: layers }));
  await page.route("**/api/board/ladder", (r) => r.fulfill({ json: LADDER }));
  await page.route("**/api/board/layers/*/geojson?*", (r) => r.fulfill({ json: geojson(100) }));
};

test("board: 시군구 search → panel rows in returned order, null vs 0, ladder", async ({ page }) => {
  const FLOOD = { ...META, label: "하천 홍수 테스트" };
  await routeBoardBase(page, [FLOOD, DROUGHT_META]);
  await page.route("**/api/board/cases", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/board/regions", (r) =>
    r.fulfill({ json: [{ code: "11220", name: "서초구" }, { code: "11230", name: "강남구" }] }),
  );
  await page.route("**/api/board/regions/*", (r) =>
    r.fulfill({
      json: {
        code: "11220",
        name: "서초구",
        layers: [
          // returned order is the display order: the 0 row comes first although it is the smallest
          { layer: "river_flood_hist_kor", label: "하천 홍수 테스트", peril: "river_flood", quantity: "water_depth_m",
            headline_key: "100", n_pieces: 8, headline: { min: 0, max: 0 } },
          { layer: "drought_spei3_hist_kor", label: "가뭄 테스트", peril: "drought", quantity: "spei_stage",
            headline_key: "y2022", n_pieces: 5, headline: null },
        ],
      },
    }),
  );

  await page.goto("/?view=board");
  await expect(page.getByTestId("ladder-national")).toHaveAttribute("aria-current", "step");
  await expect(page.getByTestId("ladder-national")).toContainText("4.6×3.7 km");
  await expect(page.getByTestId("resolution-ladder").locator("li")).toHaveCount(4);

  await page.getByTestId("region-search").fill("서초구");
  const panel = page.getByTestId("sigungu-panel");
  await expect(panel).toContainText("서초구 · 재해별 조각 최소–최대 (평균·합산·순위 없음)");
  const rows = panel.locator('[data-testid^="sigungu-row-"]');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toHaveAttribute("data-testid", "sigungu-row-river_flood_hist_kor");
  await expect(rows.nth(0)).toContainText("100년 빈도");
  await expect(rows.nth(0)).toContainText("조각 8개");
  await expect(rows.nth(0)).toContainText("0 – 0 m");
  await expect(rows.nth(0)).not.toContainText("자료 없음");
  await expect(rows.nth(1)).toContainText("2022년 5–9월 최소 SPEI-3");
  await expect(rows.nth(1)).toContainText("자료 없음");
  await expect(panel).not.toContainText(/평균 *[0-9]|mean/);
  await expect(page.getByTestId("ladder-sigungu")).toHaveAttribute("aria-current", "step");
  await expect(page.getByTestId("ladder-national")).not.toHaveAttribute("aria-current", "step");

  // a row makes its layer active
  await rows.nth(1).click();
  await expect(page.locator("#board-layer")).toHaveValue("drought_spei3_hist_kor");
  await expect(rows.nth(1)).toHaveAttribute("aria-pressed", "true");

  // a clicked point closes the panel and moves the ladder to "point"
  await page.route("**/api/board/layers/*/lookup?*", (r) =>
    r.fulfill({
      json: { id: 0, code: "11220", name: "서초구", centroid: 0, cell_km_ns: 4.64, cell_km_ew: 3.73, key_kind: "metric",
        values: { freq_ge1: 0.2, freq_ge2: 0.1, freq_ge3: 0.05, y2022: -1.2 } },
    }),
  );
  await page.locator(".leaflet-container").click({ position: { x: 400, y: 300 } });
  await expect(page.getByTestId("ladder-point")).toHaveAttribute("aria-current", "step");
  await expect(page.getByTestId("sigungu-panel")).toHaveCount(0);
});

const CASE_LIST = [
  { id: "pohang", name: "포항 냉천 · 태풍 힌남노 (2022-09-06)", peril: "pluvial_river_flood", lon: 129.4015, lat: 35.9936, cell_m: 30, badge: "미검증", available: true },
  { id: "yeongdeungpo", name: "영등포 쪽방촌 · 골목 폭염 (2026년 여름 최고 5일)", peril: "heat", lon: 126.90875, lat: 37.517, cell_m: 5, badge: "미검증", available: false },
];
const POHANG = {
  ...CASE_LIST[0],
  source_doc: "docs/reports/case_pohang_ko.md (feat/city-digital-twin)",
  figures: [{ file: "max_depth_map.png", caption_ko: "최대 침수심 (30 m 격자, 지표면 기준)" }],
  highlights: [
    { label_ko: "유역 면적 (논문 79.4)", unit: "km²", value: 83.39 },
    { label_ko: "인덕교 월류 시각 (강우 시작 후)", unit: "h", value: null },
  ],
  can_say: ["냉천 유역은 지형으로 다시 잡혀 논문 값과 맞는다"],
  cannot_say: ["실제 침수 범위와의 일치"],
  next_data: ["고해상도 하천 단면(측량)"],
  assumptions: ["A1 비는 공간적으로 균일하다", "A2 폭풍은 교대 블록 강우이다"],
  n_assumptions: 3,
  assumptions_complete: false,
  summary: { check: { warning: true } },
};
const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);

test("board: case twins → transition notice → cancel keeps map → open page → back", async ({ page }) => {
  const figureRequests: string[] = [];
  await routeBoardBase(page, [META]);
  await page.route("**/api/board/regions", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/board/cases", (r) => r.fulfill({ json: CASE_LIST }));
  await page.route("**/api/board/cases/pohang", (r) => r.fulfill({ json: POHANG }));
  await page.route("**/api/board/cases/*/figures/*", (r) => {
    figureRequests.push(new URL(r.request().url()).pathname);
    return r.fulfill({ body: PNG_1PX, contentType: "image/png" });
  });

  await page.goto("/?view=board");
  await expect(page.getByTestId("case-list")).toBeVisible();
  await expect(page.getByTestId("case-open-yeongdeungpo")).toBeDisabled();
  await expect(page.getByRole("button", { name: "사례 트윈: 포항 냉천 · 태풍 힌남노 (2022-09-06)" })).toBeVisible();

  // no automatic switch: the notice comes first, 취소 keeps the map
  await page.getByTestId("case-open-pohang").click();
  const notice = page.getByTestId("case-transition");
  await expect(notice).toContainText("사례 트윈으로 전환합니다. 해상도와 방법이 달라 전국 결과와 합산·비교할 수 없습니다.");
  await expect(page.getByTestId("case-page")).toHaveCount(0);
  await notice.getByRole("button", { name: "취소" }).click();
  await expect(notice).toHaveCount(0);
  await expect(page.getByTestId("case-page")).toHaveCount(0);
  await expect(page.locator(".leaflet-container")).toBeVisible();

  // the map pin goes through the same notice
  await page.getByRole("button", { name: "사례 트윈: 포항 냉천 · 태풍 힌남노 (2022-09-06)" }).click();
  await expect(page.getByTestId("case-transition")).toBeVisible();
  await page.getByTestId("case-transition").getByRole("button", { name: "열기" }).click();

  const cp = page.getByTestId("case-page");
  await expect(cp).toBeVisible();
  await expect(page.getByTestId("case-badge")).toHaveText("미검증");
  await expect(cp).toContainText("약 4.6×3.7 km vs 사례 30 m");
  await expect(page.getByTestId("ladder-case")).toHaveAttribute("aria-current", "step");
  const hl = page.getByTestId("case-highlights");
  await expect(hl).toContainText("83.4 km²");
  await expect(hl).toContainText("자료 없음");
  await expect(page.getByTestId("case-assumptions")).toContainText("가정 3개");
  await expect(page.getByTestId("case-assumptions")).toContainText("A2 폭풍은 교대 블록 강우이다");
  await expect(page.getByTestId("case-assumptions")).toContainText("불완전");
  await expect(page.getByTestId("case-check")).toContainText("보조 점검 경고");
  await expect(cp).toContainText("말할 수 있는 것");
  await expect(cp).toContainText("말할 수 없는 것");
  await expect(cp).toContainText("다음에 필요한 자료");
  await expect(cp).toContainText("docs/reports/case_pohang_ko.md");
  await expect.poll(() => figureRequests).toContain("/api/board/cases/pohang/figures/max_depth_map.png");

  await page.getByRole("button", { name: "← 전국 지도로" }).click();
  await expect(page.getByTestId("case-page")).toHaveCount(0);
  await expect(page.getByTestId("ladder-national")).toHaveAttribute("aria-current", "step");
});
