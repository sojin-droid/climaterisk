#!/usr/bin/env python
"""Figure pack and literature check of the Gangnam station rain-flood twin (미검증).

Builds the R6 twin once with the fixed 95 mm/h sewer (as ``scripts/twin_fixed95.py``, no
calibration search), reruns its design storm at every 2 h total of the sweep in
``assets/libraries/case_gangnam_literature.json`` and at our central 10/30/100/500-year totals
(``derived/synthetic_city/rain_frequency.json``; no rain-gauge file is read) and writes into
``derived/case_twins/gangnam``: seven PNGs, ``figures_pack.json`` and a ``literature_check`` plus
assumptions A20 / A21 in ``summary.json`` (other keys kept). The check against Keum et al. (2022,
doi:10.3390/w14244076) is diagnostic only: nothing is tuned to it. No money, no flood trace.

Runs in the CLIMADA worker environment (offline)::

    ./.climada-env/bin/python scripts/case_gangnam_figures.py [--out DIR]

Algorithm:
    See :mod:`climaterisk_worker.twin.case_gangnam_figures` (log-interpolated frequency rain,
    exit depth $h_{exit}=\\max_{open,\\,r\\le 10\\,m} h$; ASCII: h_exit = max(h[open cells near]).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402
from climaterisk_worker.twin import case_gangnam_figures as cg  # noqa: E402

logger = logging.getLogger("case_gangnam_figures")
LIT = REPO / "assets" / "libraries" / "case_gangnam_literature.json"
CENTRAL_RPS = (10.0, 30.0, 100.0, 500.0)
SEWER_MM_H = 95.0


def read_exits(osm_file: Path, bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """Gangnam station exits (OSM points tagged ``railway=subway_entrance``) inside ``bbox``."""
    import re

    import geopandas as gpd

    pts = gpd.read_file(osm_file, layer="points", bbox=bbox, engine="pyogrio")
    out: list[dict[str, Any]] = []
    for geom, other in zip(pts.geometry, pts["other_tags"].fillna(""), strict=True):
        if '"railway"=>"subway_entrance"' not in other:
            continue
        m = re.search(r'"description"=>"강남역\s*(\d+)번', other)
        if m:
            out.append({"num": int(m.group(1)), "lon": float(geom.x), "lat": float(geom.y)})
    return sorted(out, key=lambda e: e["num"])


def main(argv: list[str] | None = None) -> int:
    """Run the storms, write figures, pack and the literature check."""
    from climaterisk_worker.twin import landcover, threshold_compare
    from climaterisk_worker.twin import real_rebuild as rr
    from climaterisk_worker.twin.spec import load_spec

    from climaterisk import paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=None, help="case folder")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    out: Path = args.out or paths.derived_dir("case_twins", "gangnam")
    out.mkdir(parents=True, exist_ok=True)

    lit = json.loads(LIT.read_text(encoding="utf-8"))
    rf = json.loads(
        (paths.derived_dir("synthetic_city") / "rain_frequency.json").read_text(encoding="utf-8")
    )
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))

    spec = load_spec()
    spec["calibration"]["sewer_mm_h"] = [SEWER_MM_H]  # as scripts/twin_fixed95.py
    spec95 = rr.with_sewer(spec, SEWER_MM_H)
    bbox = tuple(float(x) for x in spec["site"]["bbox"])
    cell_m = float(spec["site"]["cell_m"])
    flood_h = float(spec["validation"]["flooded_depth_m"])

    t0 = time.perf_counter()
    twin = rr.build_real_twin(spec)
    logger.info("twin built in %.1f s", time.perf_counter() - t0)
    site = twin["site"]
    grid, walls = site["grid"], site["mask"]
    open_cells = ~walls
    site_km2 = grid.nx * grid.ny * cell_m**2 / 1e6

    ex = lit["exit6"]
    row, col = cg.exit_cell(
        grid.west, grid.south, cell_m, grid.lon_m_per_deg, float(ex["lon"]), float(ex["lat"])
    )
    if not (0 <= row < grid.ny and 0 <= col < grid.nx):
        raise SystemExit(f"Exit 6 cell ({row}, {col}) outside the grid {grid.ny} x {grid.nx}")
    r_cells = math.ceil(float(ex["search_radius_m"]) / cell_m)
    logger.info("Exit 6 cell row %d (south=0) col %d, radius %d cells", row, col, r_cells)

    def run(mm: float) -> dict[str, Any]:
        t = time.perf_counter()
        res = rr.storm_for_total(twin, spec95, float(mm))
        h = res["run"]["h_max"]
        rec = {
            "rain_mm": float(mm),
            "depth_m": cg.max_near(h, open_cells, row, col, r_cells),
            "flooded_km2": float(((h > flood_h) & open_cells).sum()) * cell_m**2 / 1e6,
            "max_froude": float(res["max_froude"]),
            "seconds": time.perf_counter() - t,
            "h": h,
        }
        logger.info(
            "storm %.1f mm: exit depth %.3f m, flooded %.4f km2, %.0f s",
            mm,
            rec["depth_m"],
            rec["flooded_km2"],
            rec["seconds"],
        )
        return rec

    sweep_runs = [run(float(m)) for m in lit["sweep_rain_2h_mm"]]
    our_rps = [float(t) for t in rf["return_periods_years"]]
    central = [float(v) for v in rf["return_level_mm"]["central"]]
    ladder_runs = []
    for t in CENTRAL_RPS:
        rec = run(cg.log_interp_rp(our_rps, central, t))
        rec["return_period_years"] = t
        ladder_runs.append(rec)

    sweep = [{"rain_mm": r["rain_mm"], "depth_m": r["depth_m"]} for r in sweep_runs]
    onset = cg.onset_rain(
        [p["rain_mm"] for p in sweep],
        [p["depth_m"] for p in sweep],
        float(ex["stair_heights_m"][0]),
    )
    d_onset = next(p["depth_m"] for p in sweep if p["rain_mm"] == float(ex["onset_rain_2h_mm"]))
    rt = lit["rain_table_mm"]
    rows = cg.rain_rows(
        rt["return_periods_years"],
        rt["120"],
        our_rps,
        central,
        rf["return_level_mm"]["band_low"],
        rf["return_level_mm"]["band_high"],
        lit["judged_return_periods_years"],
    )
    ex_row = cg.exit6_row(d_onset, float(ex["depth_m"]))
    all_rows = [*rows, ex_row]
    n_ok = sum(r["ok"] for r in all_rows)
    check = {
        "source": lit["source"],
        "conditions_ko": lit["conditions_ko"],
        "rows": all_rows,
        "n_ok": int(n_ok),
        "n_rows": len(all_rows),
        "warning": bool(n_ok < len(all_rows)),
    }
    fa = lit["flooded_area_2h_km2"]
    ours = [
        {
            "return_period_years": r["return_period_years"],
            "rain_mm": r["rain_mm"],
            "flooded_km2": r["flooded_km2"],
            "site_km2": site_km2,
        }
        for r in ladder_runs
    ]

    # ---- inputs for the figures (no flood traces anywhere)
    osm_file = paths.osm_dir(spec["site"]["osm_file"])
    exits = read_exits(Path(osm_file), bbox)  # type: ignore[arg-type]
    exit6 = {"num": 6, "name_ko": ex["name_ko"], "lon": ex["lon"], "lat": ex["lat"]}
    uses = threshold_compare.known_use(site["buildings"], spec)
    green = landcover.read_green(osm_file, bbox, spec)  # type: ignore[arg-type]
    lon_m = float(grid.lon_m_per_deg)
    badge = cg.BADGE
    pos = {"lon": float(ex["lon"]), "lat": float(ex["lat"])}

    cg.plot_study_area(
        out / "fig_study_area.png",
        bbox,
        lon_m,
        site["buildings"],
        uses,
        green,
        exits,
        exit6,
        f"연구 지역: 강남역 일대 (OSM 건물·녹지, 강남역 출구) — {badge}",
    )
    cg.plot_rain_frequency(
        out / "fig_rain_frequency.png",
        our_rps,
        central,
        rf["return_level_mm"]["band_low"],
        rf["return_level_mm"]["band_high"],
        list(rf["annual_maxima_mm"].values()),
        rt["return_periods_years"],
        rt["120"],
        rows,
        f"2시간 빈도 강우: 우리 GEV vs 논문 표 1 (같은 관측소, 기간·방법 다름) — {badge}",
    )
    panels = [
        (f"{r['return_period_years']:g}년 빈도 (중앙) {r['rain_mm']:.0f} mm", r["h"])
        for r in ladder_runs
    ]
    d113 = next(r for r in sweep_runs if r["rain_mm"] == float(ex["onset_rain_2h_mm"]))
    panels.append((f"논문 6번 출구 문턱 {d113['rain_mm']:.0f} mm", d113["h"]))
    cg.plot_ladder(
        out / "fig_ladder_depth.png",
        panels,
        walls,
        bbox,
        lon_m,
        (pos["lon"], pos["lat"]),
        ex["name_ko"],
        f"2시간 설계 강우별 최대 수심 (하수 95 mm/h 가정) — {badge}",
    )
    cg.plot_exit6_curve(
        out / "fig_exit6_curve.png",
        sweep,
        [float(s) for s in ex["stair_heights_m"]],
        (float(ex["onset_rain_2h_mm"]), float(ex["depth_m"])),
        onset,
        {f"{r['return_period_years']:g}년": r["rain_mm"] for r in ladder_runs[:3]},
        f"강남역 6번 출구 앞 수심과 2시간 강우 총량 — {badge}",
    )
    paper_share = cg.flooded_share_pct(fa["area_km2"], fa["basin_area_km2"])
    reason = (
        "면적이 다르고(논문 7.39 km², 이 트윈 상자 약 1.1 km²) 논문의 침수 깊이 기준을 알 수 없음"
    )
    cg.plot_flooded_area(
        out / "fig_flooded_area.png",
        ours,
        fa["return_periods_years"],
        paper_share,
        reason,
        f"침수 면적 비율: 참고용 비교 — {badge}",
    )
    cg.plot_validation(
        out / "fig_validation.png",
        summary["check"],
        summary["spatial_checks"],
        f"건물 단위 점검: 트윈이 단순 규칙을 이기지 못함 — {badge}",
    )
    cg.plot_model_chain(out / "fig_model_chain.png", f"모델 흐름 (95 mm/h 하수 가정) — {badge}")

    th = f"{flood_h * 100:g} cm"
    caps = [
        (
            "강남역 일대 대상지(점선 상자)의 OSM 건물과 녹지, 강남역 출구 12곳. 건물은 용도를 아는 "
            "것만 색을 칠하고 용도 미상은 윤곽만 그림(평가 제외). 6번 출구 위치는 OSM 점 [A21].",
            "Keum et al. 2022 그림 1",
        ),
        (
            "2시간 빈도 강우: 우리 GEV 중앙값과 90 % 범위, 우리 연최대(점), 논문 표 1(마름모). "
            "같은 서울 관측소지만 기간(1961-2026 vs 1961-2015)과 방법이 달라 값이 다르며, "
            "10·30·100년에서 차이를 표시함 [A20].",
            "Keum et al. 2022 표 1",
        ),
        (
            "빈도 강우(10·30·100·500년 중앙값)와 113 mm의 최대 수심 지도. 하수 95 mm/h를 균일하게 "
            "뺀다는 가정과 10 m 격자 [A20]. 건물은 벽(회색), 별은 6번 출구.",
            "Keum et al. 2022 그림 3·6",
        ),
        (
            "2시간 강우 총량에 따른 6번 출구 10 m 안 최대 수심. 논문은 113 mm에서 11 cm 넘게 "
            "잠긴다고 하고 계단 높이는 11-12 cm. 우리 트윈이 11 cm에 이르는 강우를 표시함 [A21].",
            "Keum et al. 2022 4장 (6번 출구)",
        ),
        (
            f"침수 면적 비율(%). 우리는 대상지 상자(열린 칸 수심 > {th}, 가정), 논문은 7.39 km² "
            "배수구역(침수 깊이 기준 미상). 면적과 기준이 달라 참고만 하고 판정하지 않음.",
            "Keum et al. 2022 표 2",
        ),
        (
            "건물 단위 CSI·POD·FAR 요약: 2022년(관측 강우)·2011·2010년(면적 맞춤)에서 트윈과 "
            "'가장 낮은 건물이 잠긴다' 단순 규칙의 비교. 침수흔적 지도는 싣지 않음.",
            "Keum et al. 2022 3.3 (보정 점검)",
        ),
        (
            "모델 흐름: OSM 건물·녹지, GLO-30 지형, ASOS 2시간 빈도 강우, 하수 95 mm/h와 상류 유입 "
            "가정 → 설계 강우 → 2D 얕은물 계산 → 최대 수심 → 건물 앞 수심. 옆 상자는 진단용 점검.",
            "Keum et al. 2022 그림 2",
        ),
    ]
    pack = {
        "badge": badge,
        "exit6": {"row": ex_row, "sweep": sweep, "onset_rain_mm": onset},
        "rain_rows": rows,
        "flooded_area": {
            "ours": ours,
            "flooded_depth_threshold_m": flood_h,
            "paper": {
                "basin_km2": fa["basin_area_km2"],
                "return_periods_years": fa["return_periods_years"],
                "area_km2": fa["area_km2"],
                "share_pct": paper_share,
            },
            "note_ko": reason,
        },
        "not_compared": lit["not_compared"],
        "figures": [
            {"file": f, "caption_ko": c, "idea_from": i}
            for f, (c, i) in zip(cg.FIGURE_FILES, caps, strict=True)
        ],
    }
    (out / "figures_pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    keep = [a for a in summary["assumptions"] if not a.startswith(("A20 ", "A21 "))]
    summary["assumptions"] = [*keep, *lit["assumptions"]]
    summary["n_assumptions"] = len(summary["assumptions"])
    summary["literature_check"] = check
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for r in all_rows:
        print(r["id"], "observed", r["observed"], "model", round(r["model"], 3), "ok", r["ok"])
    print("exit6 sweep:", [(p["rain_mm"], round(p["depth_m"], 3)) for p in sweep])
    print("onset rain (11 cm):", onset)
    print("flooded:", [(o["rain_mm"], round(o["flooded_km2"], 4)) for o in ours], site_km2)
    print("seconds:", [round(r["seconds"]) for r in sweep_runs + ladder_runs])
    print(f"figures -> {out}  [{np.sum(n_ok)}/{len(all_rows)} ok]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
