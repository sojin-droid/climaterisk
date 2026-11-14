"""Flood depth at asset points from the 환경부 홍수위험지도 extent and a DEM (method tier T2).

The official river-flood maps (하천범람지도, 국가하천 100/200/500 y, 지방하천 100 y) give the
**extent** only — no depth. Depth at an asset inside the extent is estimated the FwDET way
(Cohen et al. 2018, *JAWRA* 54(4); 2019 *NHESS* 19): the water surface near the asset is
the ground elevation along the nearby flood edge, and depth is that surface minus the
ground at the asset.

Licence (공공누리 제4유형): the polygons are only **overlaid** on asset points. They are
never rasterised, clipped, simplified or written out, and nothing derived from them is
stored except the per-asset depth of the run (user decision 2026-11-13, nonprofit use).

Assumptions (also recorded on the Models-tab row):

* Copernicus GLO-30 is a surface model (buildings, trees): in dense urban areas the asset
  ground reads high and the depth reads **low** (conservative-low).
* The flood edge may follow a levee crest; the median of the edge samples damps this.
* An asset outside every mapped polygon gets 0 m for that return period — the maps cover
  the mapped reaches only, so unmapped small streams are not represented.
* 지방하천 maps exist at 100 y only; at 200 / 500 y their 100-y depth is carried as a lower
  bound (a larger flood is at least as deep).
"""

from __future__ import annotations

import json
import struct
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

#: Map families: folder name per (family, return period). 도시침수 (CFM) is pluvial and
#: belongs to a separate peril, so it is not used here.
FAMILIES: dict[str, dict[int, str]] = {
    "national": {rp: f"유역별_{rp}년_빈도_국가하천_하천범람지도" for rp in (100, 200, 500)},
    "local": {100: "유역별_100년_빈도_지방하천_하천범람지도"},
}
RETURN_PERIODS = (100, 200, 500)
MAP_CRS = 5186  # Korea 2000 / Central Belt 2010 — the maps' own CRS


@dataclass(frozen=True)
class DepthParams:
    """Edge sampling: radius around the asset, spacing along the edge, minimum samples."""

    radius_m: float = 500.0
    spacing_m: float = 30.0
    min_samples: int = 5


DEFAULT_PARAMS = DepthParams()


# --------------------------------------------------------------------------- #
# Pure geometry: water surface from the flood edge                              #
# --------------------------------------------------------------------------- #
def edge_points(polygon: Any, spacing_m: float) -> np.ndarray:
    """``(n, 2)`` points every ``spacing_m`` along every ring (exterior and holes)."""
    from shapely.geometry import Polygon

    polys = [polygon] if isinstance(polygon, Polygon) else list(getattr(polygon, "geoms", []))
    out: list[np.ndarray] = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            dense = ring.segmentize(spacing_m)
            out.append(np.asarray(dense.coords)[:, :2])
    return np.vstack(out) if out else np.empty((0, 2))


def depth_at_point(
    x: float,
    y: float,
    polygons: Iterable[Any],
    dem: Callable[[np.ndarray], np.ndarray],
    params: DepthParams = DEFAULT_PARAMS,
) -> tuple[float, bool]:
    """Depth (m) at ``(x, y)`` and whether the point lies inside any polygon.

    Algorithm:
        $$h = \\max\\left(0,\\ \\operatorname{median}_{e \\in E_R} z(e) - z(x, y)\\right)$$
        ASCII: h = max(0, median(z at flood-edge points within R) - z at the asset)

        where $E_R$ = edge points (every ``spacing_m`` metres along the rings of the
        polygon(s) containing the asset) within ``radius_m`` of the asset, widened to the
        ``min_samples`` nearest edge points when fewer fall inside the radius; $z$ =
        DEM elevation (m). Outside every polygon, $h = 0$.

    Args:
        x, y: Asset position in the map CRS (metres).
        polygons: Candidate flood polygons (map CRS).
        dem: Callable mapping an ``(n, 2)`` array of map-CRS points to elevations (m);
            NaN where the DEM has no data.
        params: Edge sampling parameters.

    Returns:
        ``(depth_m, inside)``. ``depth_m`` is NaN when inside but the DEM has no data.
    """
    from shapely.geometry import Point

    pt = Point(x, y)
    containing = [p for p in polygons if p is not None and p.covers(pt)]
    if not containing:
        return 0.0, False
    # Only the edge near the asset matters: clip each boundary to a window that holds every
    # point within the radius and at least ``min_samples`` spacings beyond the nearest edge
    # (a national-river outline is hundreds of km long; densifying all of it is wasteful).
    parts: list[np.ndarray] = []
    for p in containing:
        boundary = p.boundary
        window = max(params.radius_m, boundary.distance(pt) + params.min_samples * params.spacing_m)
        local = boundary.intersection(pt.buffer(window))
        for line in getattr(local, "geoms", [local]):
            if line.is_empty or line.geom_type not in ("LineString", "LinearRing"):
                continue
            parts.append(np.asarray(line.segmentize(params.spacing_m).coords)[:, :2])
    if not parts:  # degenerate geometry: fall back to the whole outline
        parts = [edge_points(p, params.spacing_m) for p in containing]
    edges = np.vstack(parts)
    d = np.hypot(edges[:, 0] - x, edges[:, 1] - y)
    sel = d <= params.radius_m
    if sel.sum() < params.min_samples:
        sel = np.zeros_like(sel)
        sel[np.argsort(d)[: params.min_samples]] = True
    z_edge = dem(edges[sel])
    z_pt = dem(np.array([[x, y]]))[0]
    z_edge = z_edge[np.isfinite(z_edge)]
    if not np.isfinite(z_pt) or z_edge.size == 0:
        return float("nan"), True
    return float(max(0.0, np.median(z_edge) - z_pt)), True


def depth_for_return_period(rp: int, per_family: dict[str, dict[int, float]]) -> float:
    """Depth at ``rp`` across families; a family without ``rp`` lends its largest RP ≤ ``rp``.

    ``per_family[family][rp]`` is the depth from that family's map. NaN (DEM gap inside a
    flood) propagates only if no family gives a finite value.
    """
    vals: list[float] = []
    for depths in per_family.values():
        usable = [r for r in depths if r <= rp]
        if usable:
            vals.append(depths[max(usable)])
    finite = [v for v in vals if np.isfinite(v)]
    if finite:
        return float(max(finite))
    return float("nan") if vals else 0.0


# --------------------------------------------------------------------------- #
# Map files: a bounds index read from the shapefile headers inside the zips      #
# --------------------------------------------------------------------------- #
def _shp_bounds(zpath: Path) -> tuple[float, float, float, float] | None:
    """Bounding box from the .shp header (bytes 36–68) without reading the geometry."""
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".shp")]
        if not names:
            return None
        with z.open(names[0]) as f:
            head = f.read(100)
    xmin, ymin, xmax, ymax = struct.unpack("<4d", head[36:68])
    return xmin, ymin, xmax, ymax


def map_index(root: Path, cache: Path | None = None) -> list[dict[str, Any]]:
    """``[{path, family, rp, bounds}]`` for every map zip under ``root`` (cached as JSON)."""
    if cache is not None and cache.exists():
        rows: list[dict[str, Any]] = json.loads(cache.read_text(encoding="utf-8"))
        if all(Path(r["path"]).exists() for r in rows):
            return rows
    rows = []
    for family, folders in FAMILIES.items():
        for rp, folder in folders.items():
            for zp in sorted((root / folder).glob("*.zip")):
                b = _shp_bounds(zp)
                if b is not None:
                    rows.append({"path": str(zp), "family": family, "rp": rp, "bounds": b})
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def _read_polygons(zpath: str) -> tuple[list[Any], list[tuple[str, Any]]]:
    """``(extent, classes)`` of one map file.

    Each file holds one (Multi)Polygon per depth class (``SEG_CODE`` N330–N334; the
    code ↔ depth-band mapping is not published). The flood **edge** is the outline of their
    union, so ``extent`` is the union split into its parts; ``classes`` keeps the original
    class polygons for the cross-check only (in memory, never written).
    """
    import geopandas as gpd
    from shapely.ops import unary_union

    stem = Path(zpath).stem
    gdf = gpd.read_file(f"zip://{zpath}!{stem}.shp")
    if gdf.crs is None:
        gdf = gdf.set_crs(MAP_CRS)
    elif gdf.crs.to_epsg() != MAP_CRS:
        gdf = gdf.to_crs(MAP_CRS)
    gdf = gdf[gdf.geometry.notna()]
    union = unary_union(list(gdf.geometry.buffer(0)))
    extent = list(getattr(union, "geoms", [union]))
    codes = gdf["SEG_CODE"] if "SEG_CODE" in gdf else [""] * len(gdf)
    return extent, list(zip((str(c) for c in codes), gdf.geometry, strict=True))


# --------------------------------------------------------------------------- #
# DEM sampling (Copernicus GLO-30 tiles, kept under DATA_ROOT)                  #
# --------------------------------------------------------------------------- #
class Glo30Sampler:
    """Sample GLO-30 elevations at map-CRS points; tiles are fetched once into ``tile_dir``."""

    def __init__(self, tile_dir: Path, allow_download: bool) -> None:
        from pyproj import Transformer

        self.tile_dir, self.allow_download = tile_dir, allow_download
        self._to_ll = Transformer.from_crs(MAP_CRS, 4326, always_xy=True)
        self._open: dict[str, Any] = {}

    def _tile(self, lat: float, lon: float) -> Any | None:
        import math
        import urllib.request

        import rasterio

        from climaterisk_worker.ingest import _copdem_tile_url

        url = _copdem_tile_url(math.floor(lat), math.floor(lon))
        name = url.rsplit("/", 1)[-1]
        if name in self._open:
            return self._open[name]
        local = self.tile_dir / name
        if not local.exists():
            if not self.allow_download:
                self._open[name] = None
                return None
            local.parent.mkdir(parents=True, exist_ok=True)
            tmp = local.with_suffix(".part")
            try:
                urllib.request.urlretrieve(url, tmp)
            except Exception:  # sea tile (404) or network failure
                tmp.unlink(missing_ok=True)
                self._open[name] = None
                return None
            tmp.rename(local)
        self._open[name] = rasterio.open(local)
        return self._open[name]

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        lon, lat = self._to_ll.transform(xy[:, 0], xy[:, 1])
        out = np.full(len(xy), np.nan)
        for i, (la, lo) in enumerate(zip(lat, lon, strict=True)):
            ds = self._tile(la, lo)
            if ds is None:
                continue
            v = next(ds.sample([(lo, la)]))[0]
            if ds.nodata is None or v != ds.nodata:
                out[i] = float(v)
        return out

    def close(self) -> None:
        for ds in self._open.values():
            if ds is not None:
                ds.close()


# --------------------------------------------------------------------------- #
# Points → depth table                                                           #
# --------------------------------------------------------------------------- #
def depths_at_points(
    points_lonlat: list[tuple[float, float]],
    index: list[dict[str, Any]],
    dem: Callable[[np.ndarray], np.ndarray],
    params: DepthParams = DEFAULT_PARAMS,
) -> dict[str, Any]:
    """Depth per point and return period, plus diagnostics.

    Returns:
        ``{"depth": (n_rp, n_pt), "rps", "inside_dry", "dem_gap", "class_check"}``.
        ``inside_dry`` counts (point, map) pairs inside the flood extent with 0 m estimated
        depth; ``dem_gap`` counts pairs inside where the DEM had no data (depth NaN, set to
        0 by the caller only after counting). ``class_check`` lists
        ``(seg_code, depth_m)`` for every inside pair — the cross-check of the edge depth
        against the map's own (unlabelled) depth classes.
    """
    from pyproj import Transformer
    from shapely.geometry import Point

    to_map = Transformer.from_crs(4326, MAP_CRS, always_xy=True)
    xs, ys = to_map.transform([p[0] for p in points_lonlat], [p[1] for p in points_lonlat])
    per: list[dict[str, dict[int, float]]] = [{} for _ in points_lonlat]
    inside_dry = dem_gap = 0
    class_check: list[tuple[str, float]] = []
    for row in index:
        x0, y0, x1, y1 = row["bounds"]
        hit = [
            i
            for i, (x, y) in enumerate(zip(xs, ys, strict=True))
            if x0 <= x <= x1 and y0 <= y <= y1
        ]
        if not hit:
            continue
        extent, classes = _read_polygons(row["path"])
        for i in hit:
            h, inside = depth_at_point(xs[i], ys[i], extent, dem, params)
            if not inside:
                continue
            if not np.isfinite(h):
                dem_gap += 1
            elif h == 0.0:
                inside_dry += 1
            pt = Point(xs[i], ys[i])
            code = next((c for c, g in classes if g is not None and g.covers(pt)), "")
            class_check.append((code, h))
            fam = per[i].setdefault(row["family"], {})
            prev = fam.get(row["rp"], float("nan"))
            fam[row["rp"]] = float(np.nanmax([prev, h])) if np.isfinite([prev, h]).any() else h
    depth = np.array(
        [[depth_for_return_period(rp, per[i]) for i in range(len(per))] for rp in RETURN_PERIODS]
    )
    return {
        "depth": depth,
        "rps": list(RETURN_PERIODS),
        "inside_dry": inside_dry,
        "dem_gap": dem_gap,
        "class_check": class_check,
    }


def class_monotonic(class_check: list[tuple[str, float]], min_n: int = 5) -> dict[str, Any]:
    """Median edge depth per depth class; ``monotonic`` if it rises with the class code.

    The map's five classes are banded depths (≤0.5, 0.5–1, 1–2, 2–5, ≥5 m) whose code
    order is unpublished. If the edge depths sort the same way as the codes (either
    direction), the two estimates agree on *ordering*; otherwise the cross-check warns.
    Classes with fewer than ``min_n`` samples are left out.
    """
    by: dict[str, list[float]] = {}
    for code, h in class_check:
        if code and np.isfinite(h):
            by.setdefault(code, []).append(h)
    med = {c: float(np.median(v)) for c, v in sorted(by.items()) if len(v) >= min_n}
    vals = list(med.values())
    up = all(a <= b for a, b in pairwise(vals))
    down = all(a >= b for a, b in pairwise(vals))
    return {"median_by_class": med, "monotonic": (up or down) if len(vals) >= 3 else None}
