"""Building-level validation of the twin against the Seoul flood-trace maps (step R4).

The trace polygons (서울시 침수흔적도, 공공누리 제1유형) are only QUERIED: a building is
observed-flooded when its footprint, buffered by ``spec["validation"]["buffer_m"]``, touches a
trace polygon. They are never rasterised or written out. The 2022 rain comes from the Seoul
rain gauges (공공누리 제2유형) and only drives the model; it is not stored.

Algorithm:
    Observed rain on the model's design-storm interface: for per-step depths $p_k$ (mm),
    $k = 1..N$, step $\\Delta$ (min):
    $$P = \\sum_k p_k,\\quad D = N\\Delta,\\quad F_0 = 0,\\quad
    F_k = \\frac{1}{P}\\sum_{i\\le k} p_i$$
    Gauge mean per step $\\bar p_k = \\frac{1}{G}\\sum_g p_{g,k}$ (a missing reading counts 0).
    Contingency table over buildings (h hits, f false alarms, m misses):
    $$POD = \\frac{h}{h+m},\; FAR = \\frac{f}{h+f},\; CSI = \\frac{h}{h+f+m},\;
    B = \\frac{h+f}{h+m}$$
    ASCII: total = sum(p); duration = N*step; cum_fraction = [0, cumsum(p)/total];
    POD = h/(h+m); FAR = f/(h+f); CSI = h/(h+f+m); bias = (h+f)/(h+m).

    $p_k$ rain depth in step $k$ (mm), $P$ storm total (mm), $D$ duration (min), $F_k$
    cumulative fraction (-), $G$ number of gauges.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

TRACE_FALLBACK_CRS = "EPSG:5179"


def observed_rain_spec(spec: dict[str, Any], rain_mm: Any, step_min: float) -> dict[str, Any]:
    """Deep copy of ``spec`` whose ``rain`` reproduces an observed rain series.

    Args:
        spec: City spec (not modified).
        rain_mm: Rain depth per step (mm), length N.
        step_min: Step length (min).

    Returns:
        Spec copy with ``rain.total_mm``, ``duration_min`` and ``cumulative_fraction``
        (length N + 1) set so ``pluvial.hyetograph`` returns the observed cumulative rain.

    Raises:
        ValueError: If the total is not positive.
    """
    p = np.asarray(rain_mm, dtype=float)
    total = float(p.sum())
    if not total > 0.0:
        raise ValueError("observed rain series has zero total")
    out = copy.deepcopy(spec)
    out["rain"]["total_mm"] = total
    out["rain"]["duration_min"] = float(p.size) * float(step_min)
    out["rain"]["cumulative_fraction"] = [0.0, *(np.cumsum(p) / total).tolist()]
    return out


def gauge_series(
    df: pd.DataFrame,
    codes: Any,
    start: pd.Timestamp,
    end: pd.Timestamp,
    step_min: float,
) -> pd.Series:
    """Mean rain depth per step (mm) over the gauges ``codes`` on the grid ``[start, end)``.

    A reading belongs to the step its time floors to; duplicate readings of a gauge in one
    step keep the maximum; a gauge without a reading in a step counts as 0.

    Args:
        df: Seoul gauge table with ``강우량계 코드``, ``10분우량``, ``자료수집 시각``.
        codes: Gauge codes to average.
        start: First step start (inclusive).
        end: End of the grid (exclusive).
        step_min: Step (min).

    Returns:
        Series indexed by step start time.
    """
    freq = pd.Timedelta(minutes=float(step_min))
    index = pd.date_range(start, end, freq=freq, inclusive="left")
    code_list = list(codes)
    sel = df[df["강우량계 코드"].isin(code_list)]
    t = pd.to_datetime(sel["자료수집 시각"], format="mixed").dt.floor(freq)
    vals = pd.to_numeric(sel["10분우량"], errors="coerce").fillna(0.0)
    tab = (
        pd.DataFrame(
            {"g": sel["강우량계 코드"].to_numpy(), "t": t.to_numpy(), "v": vals.to_numpy()}
        )
        .groupby(["g", "t"])["v"]
        .max()
        .unstack("g")
    )
    tab = tab.reindex(index=index, columns=code_list).fillna(0.0)
    return tab.mean(axis=1).rename("rain_mm")


def trace_flags(buildings: gpd.GeoDataFrame, traces: gpd.GeoDataFrame, buffer_m: float) -> Any:
    """Whether each building, buffered by ``buffer_m``, intersects any trace polygon.

    Buildings are projected to the CRS of the traces (EPSG:5179 when the traces have none)
    before the metric buffer. Polygons are only queried through a spatial index.

    Args:
        buildings: Footprints with a CRS.
        traces: Trace polygons.
        buffer_m: Buffer distance (m).

    Returns:
        Boolean array, one per building.
    """
    crs = traces.crs if traces.crs is not None else TRACE_FALLBACK_CRS
    geoms = buildings.to_crs(crs).geometry.buffer(float(buffer_m))
    flags = np.zeros(len(buildings), dtype=bool)
    if len(traces) == 0:
        return flags
    hit, _ = traces.geometry.sindex.query(geoms.to_numpy(), predicate="intersects")
    flags[np.unique(hit)] = True
    return flags


def model_flags(depth_m: Any, threshold_m: float) -> Any:
    """Modelled flooded flag: ``depth_m > threshold_m``."""
    return np.asarray(depth_m, dtype=float) > float(threshold_m)


def contingency(pred: Any, obs: Any) -> dict[str, Any]:
    """Contingency counts and POD, FAR, CSI, bias (NaN where a denominator is 0)."""
    p = np.asarray(pred, dtype=bool)
    o = np.asarray(obs, dtype=bool)
    h = int((p & o).sum())
    fa = int((p & ~o).sum())
    m = int((~p & o).sum())
    cn = int((~p & ~o).sum())

    def ratio(a: int, b: int) -> float:
        return a / b if b > 0 else math.nan

    return {
        "hits": h,
        "false_alarms": fa,
        "misses": m,
        "correct_negatives": cn,
        "n": int(p.size),
        "pod": ratio(h, h + m),
        "far": ratio(fa, h + fa),
        "csi": ratio(h, h + fa + m),
        "bias": ratio(h + fa, h + m),
    }


def read_traces(year: str, bbox_lonlat: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    """Read the trace polygons of ``year`` that fall in a lon/lat box (geometry only).

    Args:
        year: Folder name under ``external/seoul_flood_traces``.
        bbox_lonlat: West, south, east, north (degrees) of the area of interest.

    Returns:
        GeoDataFrame in the layer's own CRS (EPSG:5179) with geometry only.
    """
    from climaterisk import paths

    folder = paths.external_dir("seoul_flood_traces", year)
    shp = sorted(Path(folder).glob("*.shp"))[0]
    enc = "cp949" if year in ("2010", "2011") else None
    box = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries.from_xy(
            [bbox_lonlat[0], bbox_lonlat[2]], [bbox_lonlat[1], bbox_lonlat[3]], crs="EPSG:4326"
        )
    ).to_crs(TRACE_FALLBACK_CRS)
    x0, y0, x1, y1 = box.total_bounds
    pad = 500.0
    g = gpd.read_file(shp, encoding=enc, bbox=(x0 - pad, y0 - pad, x1 + pad, y1 + pad))
    return gpd.GeoDataFrame(geometry=g.geometry.reset_index(drop=True), crs=g.crs)


def read_gauge_file(year: int, month: int) -> pd.DataFrame:
    """Seoul rain-gauge CSV (cp949) of one month."""
    from climaterisk import paths

    f = paths.external_dir(
        "seoul_rain_gauges", str(year), f"서울시_강우량_정보_{year}년{month:02d}월.csv"
    )
    return pd.read_csv(f, encoding="cp949")


__all__ = [
    "contingency",
    "gauge_series",
    "model_flags",
    "observed_rain_spec",
    "read_gauge_file",
    "read_traces",
    "trace_flags",
]
