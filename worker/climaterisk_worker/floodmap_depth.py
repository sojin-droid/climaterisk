"""River-flood depth at asset points from the 환경부 홍수위험지도 (method tier T2).

The official river-flood maps (하천범람지도, 국가하천 100/200/500 y, 지방하천 100 y) hold one
(Multi)Polygon per **depth class** (``SEG_CODE`` N330–N334) and no depth value.

* **Main method** (:func:`class_depths`): the deepest official class whose polygon covers
  the asset, read as its published depth band (≤0.5, 0.5–1, 1–2, 2–5, ≥5 m) and priced at
  a representative depth (band midpoint by default). No DEM is needed.
* **Sub check** (:func:`edge_check`, diagnostic only): the FwDET depth (Cohen et al. 2018,
  *JAWRA* 54(4)) — median ground elevation of the nearby flood edge minus the asset ground.
  It never changes the hazard; it reports how often it lands inside the official band.
  A bare-earth DEM (국토지리정보원 공개 DEM) is used where it covers the point and its edge
  samples; otherwise Copernicus GLO-30, a surface model that fails in high-rise areas
  (2026-11-15: 28 % in band in Seoul vs 45 % rural).

The code ↔ band order (N330 shallowest … N334 deepest) is **inferred**, not officially
confirmed: edge-depth class medians are monotonic in that order (rural GLO-30; east Seoul
bare earth 0.14 / 0.32 / 0.99 / 1.67 / 5.58 m).

Licence (공공누리 제4유형): the polygons are only **overlaid** on asset points, in memory.
They are never rasterised or written out, and nothing derived from them is stored except the
per-asset class of the run (user decision 2026-11-13, nonprofit use).

Assumptions (also recorded on the Models-tab row):

* An asset outside every mapped polygon gets 0 m for that return period — the maps cover
  the mapped reaches only, so unmapped small streams are not represented.
* A national return-period file that does not exist for a basin the asset hit gives NaN
  (``None`` class) for that return period, with the reason ``no_file_for_rp:<rp>``; no other
  return period's value is borrowed.
* 지방하천 maps exist at 100 y only (by design, never "missing"); at 200 / 500 y their 100-y
  class is used as a lower bound (a larger flood is at least as deep), flagged ``local_only``.
* The maps assume levee failure or overtopping (design flood), present day only.
"""

from __future__ import annotations

import json
import logging
import re
import struct
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

#: River map families: folder name per (family, return period). 도시침수 (CFM) is pluvial and
#: is read by the separate urban-pluvial adapter through :data:`URBAN_FAMILIES`
#: (``KoreaLocalPluvialAdapter``), never mixed into these.
FAMILIES: dict[str, dict[int, str]] = {
    "national": {rp: f"유역별_{rp}년_빈도_국가하천_하천범람지도" for rp in (100, 200, 500)},
    "local": {100: "유역별_100년_빈도_지방하천_하천범람지도"},
}
#: 도시침수지도 (CFM_SBSN_<basin>_100.zip, 100 y only). Same ``SEG_CODE`` classes as the river
#: maps; their code ↔ band order is taken from the river maps (inferred, not confirmed).
URBAN_FAMILIES: dict[str, dict[int, str]] = {"urban": {100: "유역별_100년_빈도_도시침수지도"}}
RETURN_PERIODS = (100, 200, 500)
MAP_CRS = 5186  # Korea 2000 / Central Belt 2010 — the maps' own CRS


@dataclass(frozen=True)
class DepthParams:
    """Edge sampling: radius around the asset, spacing along the edge, minimum samples."""

    radius_m: float = 500.0
    spacing_m: float = 30.0
    min_samples: int = 5


DEFAULT_PARAMS = DepthParams()

#: Published depth band (m) per class code; ``None`` = open upper bound. Order inferred.
BANDS: dict[str, tuple[float, float | None]] = {
    "N330": (0.0, 0.5),
    "N331": (0.5, 1.0),
    "N332": (1.0, 2.0),
    "N333": (2.0, 5.0),
    "N334": (5.0, None),
}
REPRESENTATIVE = ("lower", "mid", "upper")


def representative_depth(code: str, mode: str = "mid", open_class_depth_m: float = 5.0) -> float:
    """Depth (m) priced for an official class.

    Algorithm:
        $$h = \\begin{cases} a & \\text{lower} \\\\ (a+b)/2 & \\text{mid} \\\\ b & \\text{upper}
        \\end{cases}, \\quad h_{N334} = h_{open}$$
        ASCII: band [a, b]; lower = a, mid = (a + b) / 2, upper = b; the open class
        (N334, >= 5 m, no b) is always h_open (default 5.0 m) whatever the mode.

    Args:
        code: ``SEG_CODE`` (N330–N334); empty string = outside every polygon.
        mode: ``lower`` | ``mid`` | ``upper``.
        open_class_depth_m: Depth used for the open class.

    Returns:
        Depth in metres; 0.0 for an empty code.

    Raises:
        ValueError: unknown mode or code.
    """
    if mode not in REPRESENTATIVE:
        raise ValueError(f"unknown representative mode {mode!r}")
    if not code:
        return 0.0
    if code not in BANDS:
        raise ValueError(f"unknown depth class {code!r}")
    lo, hi = BANDS[code]
    if hi is None:
        return float(open_class_depth_m)
    return float({"lower": lo, "mid": (lo + hi) / 2, "upper": hi}[mode])


def in_band(code: str, depth_m: float) -> bool:
    """Whether ``depth_m`` lies inside the official band of ``code`` (bounds inclusive)."""
    lo, hi = BANDS[code]
    return depth_m >= lo and (hi is None or depth_m <= hi)


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
    border: Any | None = None,
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
        border: Artificial outline to ignore (the clip window of :func:`local_extent`);
            only real flood edges are sampled.

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
        boundary = p.boundary if border is None else p.boundary.difference(border)
        if boundary.is_empty:
            continue
        window = max(params.radius_m, boundary.distance(pt) + params.min_samples * params.spacing_m)
        local = boundary.intersection(pt.buffer(window))
        for line in getattr(local, "geoms", [local]):
            if line.is_empty or line.geom_type not in ("LineString", "LinearRing"):
                continue
            parts.append(np.asarray(line.segmentize(params.spacing_m).coords)[:, :2])
    if not parts:
        if border is not None:  # no real edge inside the window: caller widens it
            return float("nan"), True
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


def map_index(
    root: Path, cache: Path | None = None, families: dict[str, dict[int, str]] | None = None
) -> list[dict[str, Any]]:
    """``[{path, family, rp, bounds}]`` for every map zip under ``root`` (cached as JSON).

    ``families`` defaults to the river maps (:data:`FAMILIES`); pass :data:`URBAN_FAMILIES`
    for the 도시침수지도. Use a separate ``cache`` file per family set.
    """
    families = FAMILIES if families is None else families
    if cache is not None and cache.exists():
        rows: list[dict[str, Any]] = json.loads(cache.read_text(encoding="utf-8"))
        if all(Path(r["path"]).exists() for r in rows):
            return rows
    rows = []
    for family, folders in families.items():
        for rp, folder in folders.items():
            for zp in sorted((root / folder).glob("*.zip")):
                b = _shp_bounds(zp)
                if b is not None:
                    rows.append({"path": str(zp), "family": family, "rp": rp, "bounds": b})
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def _read_polygons(zpath: str) -> list[tuple[str, Any]]:
    """``[(SEG_CODE, geometry)]`` of one map file, in the map CRS.

    Each file holds one (Multi)Polygon per depth class (``SEG_CODE`` N330–N334; the code ↔
    depth-band order is not published). The flood **edge** is the outline of their union,
    which :func:`local_extent` builds around each asset only — the full outlines of a
    national river are huge and are never merged or written.
    """
    import geopandas as gpd

    stem = Path(zpath).stem
    gdf = gpd.read_file(f"zip://{zpath}!{stem}.shp")
    if gdf.crs is None:
        gdf = gdf.set_crs(MAP_CRS)
    elif gdf.crs.to_epsg() != MAP_CRS:
        gdf = gdf.to_crs(MAP_CRS)
    gdf = gdf[gdf.geometry.notna()]
    codes = gdf["SEG_CODE"] if "SEG_CODE" in gdf else [""] * len(gdf)
    return list(zip((str(c) for c in codes), gdf.geometry, strict=True))


def local_extent(
    classes: list[tuple[str, Any]], x: float, y: float, half_width_m: float
) -> tuple[list[Any], Any]:
    """Flood extent (union of the class polygons) inside a square window around ``(x, y)``.

    Returns ``(parts, border)``: the polygon parts of the clipped union and the window
    outline (buffered 0.5 m) that :func:`depth_at_point` must not treat as a flood edge.
    In memory only.
    """
    import shapely
    from shapely.geometry import box
    from shapely.ops import unary_union

    w = (x - half_width_m, y - half_width_m, x + half_width_m, y + half_width_m)
    pieces = []
    for _, g in classes:
        if g is None or not g.intersects(box(*w)):
            continue
        c = shapely.make_valid(shapely.clip_by_rect(g, *w))
        pieces.extend(
            p for p in getattr(c, "geoms", [c]) if p.geom_type in ("Polygon", "MultiPolygon")
        )
    if not pieces:
        return [], None
    u = unary_union(pieces)
    return list(getattr(u, "geoms", [u])), box(*w).exterior.buffer(0.5)


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
# Main method: official class at each point (no DEM)                            #
# --------------------------------------------------------------------------- #
class ClassIndex:
    """Per map file, an in-memory STRtree over the exploded class-polygon parts.

    Built lazily per file. Whole national-river files are huge, so nothing is merged,
    repaired or prepared as a whole; parts are queried by bounding box and tested one by
    one. Nothing is written to disk.
    """

    def __init__(self) -> None:
        self._trees: dict[str, tuple[Any, np.ndarray, np.ndarray]] = {}

    def tree(self, path: str) -> tuple[Any, np.ndarray, np.ndarray]:
        if path not in self._trees:
            import shapely

            parts: list[np.ndarray] = []
            codes: list[str] = []
            for code, g in _read_polygons(path):
                if g is None:
                    continue
                p = shapely.get_parts(g)
                p = p[shapely.get_type_id(p) == 3]  # polygons only
                parts.append(p)
                codes += [code] * len(p)
            arr = np.concatenate(parts) if parts else np.array([], dtype=object)
            self._trees[path] = (shapely.STRtree(arr), arr, np.array(codes, dtype=object))
        return self._trees[path]

    def code_at(self, path: str, x: float, y: float) -> str:
        """Deepest class code of ``path`` covering map-CRS point ``(x, y)``; '' if none."""
        import shapely

        tree, parts, codes = self.tree(path)
        pt = shapely.Point(x, y)
        best = ""
        for j in tree.query(pt):
            try:
                if shapely.intersects(parts[j], pt):
                    best = max(best, str(codes[j]))
            except Exception:  # an invalid part: skip it rather than fail the run
                continue
        return best


def basin_of(path: str) -> str | None:
    """The 4-digit basin of a map zip named ``..._<basin>_<rp>.zip``, or None."""
    m = re.search(r"_(\d{4})_\d+\.zip$", str(path))
    return m.group(1) if m else None


def available_files(index: list[dict[str, Any]]) -> set[tuple[str, str, int]]:
    """The ``(family, basin, rp)`` of every map file in the index."""
    out: set[tuple[str, str, int]] = set()
    for row in index:
        basin = basin_of(row["path"])
        if basin is not None:
            out.add((row["family"], basin, int(row["rp"])))
    return out


def class_for_return_period(rp: int, per_family: dict[str, dict[int, str]]) -> str:
    """Deepest class at ``rp`` over the families' hits at return periods ≤ ``rp``.

    This is the envelope over the files that exist (map inconsistency is absorbed).
    A national file that does *not* exist is handled by :func:`classes_at_points` (NaN), not here.
    """
    vals: list[str] = []
    for codes in per_family.values():
        usable = [r for r in codes if r <= rp]
        if usable:
            vals.append(codes[max(usable)])
    return max(vals) if vals else ""


def classes_at_points(
    points_lonlat: list[tuple[float, float]], index: list[dict[str, Any]], cidx: ClassIndex
) -> dict[str, Any]:
    """Official class per point and return period; a missing national file is unknown.

    Rule: the class at return period ``r`` is the deepest code over families and over the
    *existing* files with return period ``r' <= r`` (the envelope, which absorbs map
    inconsistency). Exception: if the point hit a national file of basin ``b`` and the file
    ``(national, b, r)`` does not exist, the class at ``r`` is unknown (``None``, NaN depth)
    with reason ``no_file_for_rp:<r>`` — another return period's value is never borrowed.
    The 지방하천 family is 100-y by design and is never missing at 200 / 500 y; a point
    under local maps only is flagged ``local_only`` (its 100-y class is a lower bound).
    A point outside every polygon keeps ``""`` (0 m).

    Algorithm:
        $$c_r(p) = \\max_{f}\\ \\max_{r' \\le r,\\ (f,b,r')\\ \\text{exists}}
        \\text{code}_{f,r'}(p)$$
        ASCII: c_r(p) = max over families f and existing files (f,b,r'), r' <= r,
        of code[f,r'](p); None when (national,b,r) is absent for a basin b hit.
        ``code`` is the ordinal SEG_CODE N330..N334 (dimensionless).

    Returns:
        ``{"codes": [[code or None per point] per RP], "rps", "file100", "xy",
        "missing": [[bool per point] per RP], "reason": [str | None per point],
        "local_only": [bool per point]}``.
    """
    from pyproj import Transformer

    to_map = Transformer.from_crs(4326, MAP_CRS, always_xy=True)
    xs, ys = to_map.transform([p[0] for p in points_lonlat], [p[1] for p in points_lonlat])
    n = len(points_lonlat)
    have = available_files(index)
    per: list[dict[str, dict[int, str]]] = [{} for _ in range(n)]
    nat_basins: list[set[str]] = [set() for _ in range(n)]
    file100: list[str | None] = [None] * n
    for row in index:
        x0, y0, x1, y1 = row["bounds"]
        for i, (x, y) in enumerate(zip(xs, ys, strict=True)):
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            code = cidx.code_at(row["path"], x, y)
            if not code:
                continue
            fam = per[i].setdefault(row["family"], {})
            fam[row["rp"]] = max(fam.get(row["rp"], ""), code)
            basin = basin_of(row["path"])
            if row["family"] == "national" and basin is not None:
                nat_basins[i].add(basin)
            if row["rp"] == 100 and (file100[i] is None or row["family"] == "national"):
                file100[i] = row["path"]
    codes: list[list[str | None]] = []
    missing: list[list[bool]] = []
    reasons: list[list[str]] = [[] for _ in range(n)]
    for rp in RETURN_PERIODS:
        row_codes: list[str | None] = []
        row_missing: list[bool] = []
        for i in range(n):
            gap = any(("national", b, rp) not in have for b in nat_basins[i])
            row_missing.append(gap)
            if gap:
                row_codes.append(None)
                reasons[i].append(f"no_file_for_rp:{rp}")
            else:
                row_codes.append(class_for_return_period(rp, per[i]))
        codes.append(row_codes)
        missing.append(row_missing)
    local_only = [bool(per[i]) and set(per[i]) == {"local"} for i in range(n)]
    logging.getLogger(__name__).debug(
        "classes_at_points: points=%d missing_points=%d local_only=%d",
        n,
        sum(1 for r in reasons if r),
        sum(local_only),
    )
    return {
        "codes": codes,
        "rps": list(RETURN_PERIODS),
        "file100": file100,
        "xy": (xs, ys),
        "missing": missing,
        "reason": [";".join(r) if r else None for r in reasons],
        "local_only": local_only,
    }


def class_depths(
    codes: list[list[str | None]], mode: str = "mid", open_class_depth_m: float = 5.0
) -> dict[str, np.ndarray]:
    """Representative, lower and upper depth arrays ``(n_rp, n_pt)`` for class codes.

    ``upper`` is NaN for the open class (no published upper bound); outside (``""``) = 0.
    A ``None`` code (no map file for that return period) is NaN in all three arrays —
    unknown, never 0.
    """
    nan = float("nan")
    rep = np.array(
        [
            [nan if c is None else representative_depth(c, mode, open_class_depth_m) for c in row]
            for row in codes
        ]
    )
    lo = np.array(
        [[nan if c is None else (BANDS[c][0] if c else 0.0) for c in row] for row in codes]
    )
    hi = np.array(
        [
            [
                nan if c is None else ((nan if BANDS[c][1] is None else BANDS[c][1]) if c else 0.0)
                for c in row
            ]
            for row in codes
        ]
    )
    return {"depth": rep, "lower": lo, "upper": hi}


# --------------------------------------------------------------------------- #
# Sub check: DEM edge depth against the official band (diagnostic only)         #
# --------------------------------------------------------------------------- #
class StrictSampler:
    """Wraps a sampler: if any requested point has no value, every value is NaN.

    Used for the bare-earth DEM so that a depth is computed from it only when it covers
    the asset *and* all its edge samples; otherwise the check falls back to GLO-30.
    """

    def __init__(self, inner: Callable[[np.ndarray], np.ndarray]) -> None:
        self.inner = inner

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        v = self.inner(xy)
        return v if np.isfinite(v).all() else np.full(len(xy), np.nan)


class RasterSampler:
    """Nearest-cell values of local rasters (any CRS) at map-CRS points; NaN outside."""

    def __init__(self, paths_: list[Path]) -> None:
        import rasterio
        from pyproj import Transformer

        self.ds = [rasterio.open(p) for p in paths_]
        self.tf = [
            Transformer.from_crs(MAP_CRS, d.crs if d.crs else MAP_CRS, always_xy=True)
            for d in self.ds
        ]

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, dtype=float)
        out = np.full(len(xy), np.nan)
        for d, tf in zip(self.ds, self.tf, strict=True):
            X, Y = tf.transform(xy[:, 0], xy[:, 1])
            b = d.bounds
            ok = (b.left <= X) & (b.right >= X) & (b.bottom <= Y) & (b.top >= Y) & ~np.isfinite(out)
            if ok.any():
                v = np.array(
                    [s[0] for s in d.sample(list(zip(X[ok], Y[ok], strict=True)))], dtype=float
                )
                if d.nodata is not None:
                    v[v == d.nodata] = np.nan
                v[v < -100] = np.nan
                out[ok] = v
        return out

    def close(self) -> None:
        for d in self.ds:
            d.close()


def edge_depth(
    x: float,
    y: float,
    classes: list[tuple[str, Any]],
    dem: Callable[[np.ndarray], np.ndarray],
    params: DepthParams = DEFAULT_PARAMS,
) -> float:
    """FwDET depth at a map-CRS point inside one map file's extent; NaN if not computable."""
    half = 4 * params.radius_m
    while half <= 64_000:
        parts, border = local_extent(classes, x, y, half)
        h, inside = depth_at_point(x, y, parts, dem, params, border)
        if not inside or np.isfinite(h) or border is None:
            return h if inside else float("nan")
        half *= 2
    return float("nan")


def edge_check(
    xs: Any,
    ys: Any,
    codes100: list[str | None],
    file100: list[str | None],
    dems: list[tuple[str, Callable[[np.ndarray], np.ndarray]]],
    max_points: int = 200,
    seed: int = 0,
    params: DepthParams = DEFAULT_PARAMS,
) -> list[tuple[str, float, str]]:
    """``(class, edge depth, dem name)`` for up to ``max_points`` assets inside at 100 y.

    ``dems`` is tried in order per point (e.g. strict bare earth, then GLO-30); the first
    finite depth wins. Points are drawn with a seeded generator when there are more.
    """
    inside = [i for i, c in enumerate(codes100) if c and file100[i]]
    if len(inside) > max_points:
        rng = np.random.default_rng(seed)
        inside = sorted(rng.choice(inside, size=max_points, replace=False).tolist())
    out: list[tuple[str, float, str]] = []
    cache: dict[str, list[tuple[str, Any]]] = {}
    for i in inside:
        path = str(file100[i])
        if path not in cache:
            cache[path] = _read_polygons(path)
        for name, dem in dems:
            h = edge_depth(float(xs[i]), float(ys[i]), cache[path], dem, params)
            if np.isfinite(h):
                out.append((codes100[i], float(h), name))
                break
        else:
            out.append((codes100[i], float("nan"), ""))
    return out


def check_summary(
    pairs: list[tuple[str, float, str]], min_n: int = 20, min_share: float = 0.35
) -> dict[str, Any]:
    """Agreement of the edge depth with the official band; ``warn`` per the documented rule.

    Warn when at least ``min_n`` points have a finite check depth and either the share
    inside the band is below ``min_share`` or the class medians are not monotonic.
    """
    ok = [(c, h, d) for c, h, d in pairs if c and np.isfinite(h)]
    n = len(ok)
    share = float(np.mean([in_band(c, h) for c, h, _ in ok])) if n else None
    mono = class_monotonic([(c, h) for c, h, _ in ok], rising_only=True)
    dem_counts: dict[str, int] = {}
    for _, _, d in ok:
        dem_counts[d] = dem_counts.get(d, 0) + 1
    reasons = []
    if n >= min_n and share is not None and share < min_share:
        reasons.append(f"only {share:.0%} of check depths fall inside the official band")
    if n >= min_n and mono["monotonic"] is False:
        reasons.append("check-depth class medians are not monotonic")
    return {
        "method": "FwDET edge depth vs official depth band (diagnostic only)",
        "n_points": n,
        "n_not_computable": len(pairs) - n,
        "share_in_band": None if share is None else round(share, 3),
        "median_by_class": {k: round(v, 2) for k, v in mono["median_by_class"].items()},
        "monotonic": mono["monotonic"],
        "dem_used": dem_counts,
        "warn": bool(reasons),
        "warn_reason": "; ".join(reasons) or None,
        "rule": f"warn if n >= {min_n} and (share < {min_share} or medians not monotonic)",
    }


# --------------------------------------------------------------------------- #
# Points → edge-depth table (the former main method, kept for analysis)          #
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
        classes = _read_polygons(row["path"])
        for i in hit:
            # Widen the window until a real flood edge lies inside it (max 64 km).
            half, h, inside = 4 * params.radius_m, 0.0, False
            while half <= 64_000:
                parts, border = local_extent(classes, xs[i], ys[i], half)
                h, inside = depth_at_point(xs[i], ys[i], parts, dem, params, border)
                if not inside or np.isfinite(h) or border is None:
                    break
                half *= 2
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


def class_monotonic(
    class_check: list[tuple[str, float]], min_n: int = 5, rising_only: bool = False
) -> dict[str, Any]:
    """Median edge depth per depth class; ``monotonic`` if it rises with the class code.

    The map's five classes are banded depths (≤0.5, 0.5–1, 1–2, 2–5, ≥5 m) whose code
    order is unpublished. If the edge depths sort the same way as the codes (either
    direction, unless ``rising_only``: the order N330 shallowest … N334 deepest), the two
    estimates agree on *ordering*; otherwise the cross-check warns. Classes with fewer than
    ``min_n`` samples are left out.
    """
    by: dict[str, list[float]] = {}
    for code, h in class_check:
        if code and np.isfinite(h):
            by.setdefault(code, []).append(h)
    med = {c: float(np.median(v)) for c, v in sorted(by.items()) if len(v) >= min_n}
    vals = list(med.values())
    up = all(a <= b for a, b in pairwise(vals))
    down = all(a >= b for a, b in pairwise(vals))
    ok = up if rising_only else (up or down)
    return {"median_by_class": med, "monotonic": ok if len(vals) >= 3 else None}
