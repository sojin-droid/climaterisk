// Visual board: layer picker -> return-period change -> map click -> side panel.
// The /api/board/* answers are fixtures served by Playwright (page.route): the board layer
// files live in the external data root, which the e2e backend does not have.
import { expect, test } from "@playwright/test";

const META = {
  layer: "river_flood_hist_kor",
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
  await expect(page.getByText("0 (침수 없음)").first()).toBeVisible();
  await expect(page.getByText("자료 없음 (null)")).toBeVisible();

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
