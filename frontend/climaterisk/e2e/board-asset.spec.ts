// Board step 5 gate (reviewer-written; agents may not edit it): a clicked board point becomes a
// portfolio asset. The board does not price anything itself: the asset goes into the same
// portfolio the Map / Models tabs use, so the run and the Excel export are the existing engine
// (nearest hazard cell = the board lookup value, fixed by tests/test_board_api.py).
//
// UI contract:
// - pick-panel holds `board-add-asset` with inputs labelled "자산 이름", "자산 가치 (USD)" and a
//   select labelled "업종" (sectors from the libraries). Name defaults to "<시군구명> 지점".
// - "자산으로 추가" is disabled until the value is a number > 0 (no default value, no estimate).
// - The asset is the CLICKED coordinate (the lon/lat sent to the lookup), rounded to 5 decimals,
//   geographic_scale "point", currency "USD", properties.source "visual_board",
//   properties.board_layer = active layer, properties.board_code = piece 시군구 code.
// - After adding: `board-asset-added` says "자산 목록에 추가했습니다" and a button
//   "모델 탭에서 실행·엑셀 →" switches to the Models tab (where the run and the Excel export are).
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const PALETTES = JSON.parse(
  readFileSync(new URL("../../../assets/libraries/board_palettes.json", import.meta.url), "utf-8"),
);

const META = {
  layer: "river_flood_hist_kor",
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

const sq = (x: number, y: number, s: number) => [[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]];

test("board: clicked point → asset in the portfolio → Models tab", async ({ page }) => {
  let lookedUp: { lon: number; lat: number } | null = null;
  await page.route("**/api/board/palettes", (r) => r.fulfill({ json: PALETTES }));
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await page.route("**/api/board/layers", (r) => r.fulfill({ json: [META] }));
  await page.route("**/api/board/layers/*/geojson?*", (r) =>
    r.fulfill({
      json: {
        type: "FeatureCollection",
        features: [
          { type: "Feature", geometry: { type: "Polygon", coordinates: sq(124, 33, 8) },
            properties: { id: 0, centroid: 0, code: "11220", name: "서초구", value: 1.5 } },
        ],
      },
    }),
  );
  await page.route("**/api/board/layers/*/lookup?*", (r) => {
    const q = new URL(r.request().url()).searchParams;
    lookedUp = { lon: Number(q.get("lon")), lat: Number(q.get("lat")) };
    return r.fulfill({
      json: { id: 0, code: "11220", name: "서초구", centroid: 0, cell_km_ns: 4.64, cell_km_ew: 3.73,
        values: { "10": 0, "100": 1.5 } },
    });
  });

  await page.goto("/?view=board");
  await expect(page.locator(".topbar .meta")).toContainText("0 assets");
  await page.locator(".leaflet-container").click({ position: { x: 400, y: 300 } });
  const form = page.getByTestId("board-add-asset");
  await expect(form).toBeVisible();
  await expect(form.getByLabel("자산 이름")).toHaveValue("서초구 지점");
  const add = form.getByRole("button", { name: "자산으로 추가" });
  await expect(add).toBeDisabled();
  await form.getByLabel("자산 가치 (USD)").fill("0");
  await expect(add).toBeDisabled();
  await form.getByLabel("자산 가치 (USD)").fill("5000000");
  await expect(form.getByLabel("업종")).toBeVisible();
  await expect(add).toBeEnabled();

  const saved = page.waitForRequest(
    (req) => req.method() === "PUT" && /\/api\/session\/[^/]+$/.test(new URL(req.url()).pathname)
      && (req.postData() ?? "").includes("visual_board"),
  );
  await add.click();
  await expect(page.getByTestId("board-asset-added")).toContainText("자산 목록에 추가했습니다");
  await expect(page.locator(".topbar .meta")).toContainText("1 asset");

  const body = JSON.parse((await saved).postData() ?? "{}");
  const asset = body.assets.find((a: { properties?: { source?: string } }) => a.properties?.source === "visual_board");
  expect(asset).toBeTruthy();
  expect(lookedUp).not.toBeNull();
  const clicked = lookedUp as unknown as { lon: number; lat: number };
  expect(asset.lon).toBeCloseTo(clicked.lon, 5);
  expect(asset.lat).toBeCloseTo(clicked.lat, 5);
  expect(asset.value).toBe(5000000);
  expect(asset.currency).toBe("USD");
  expect(asset.geographic_scale).toBe("point");
  expect(asset.name).toBe("서초구 지점");
  expect(asset.properties.board_layer).toBe("river_flood_hist_kor");
  expect(asset.properties.board_code).toBe("11220");

  await page.getByRole("button", { name: "모델 탭에서 실행·엑셀 →" }).click();
  await expect(page.locator(".activitybar button.active")).toContainText("Models");
  await expect(page.getByTestId("board-add-asset")).toHaveCount(0);
});
