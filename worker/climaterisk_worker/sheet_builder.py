"""Build a 단면 asset sheet (``danmyeon.sheet/1``, see ``docs/SHEET_SCHEMA.md``).

The sheet carries class codes and heights only. The official 홍수위험지도 polygons are read
in memory to find the class at each sample and are never written (공공누리 제4유형).

Pure parts (:func:`cut_line`, :func:`review_stamp`, :func:`assemble_sheet`) need numpy and
pyproj only; :func:`build_sheet` reads the real maps, DEMs and CLIMADA hazard files.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import date as _date
from pathlib import Path
from typing import Any

import numpy as np

from climaterisk_worker import floodmap_depth as fd

RP_KEYS = ("rp100", "rp200", "rp500")
SCHEMA = "danmyeon.sheet/1"
DEFAULT_USE = "자산끼리 순위 비교, 범위와 함께 인용. 공시 본문과 가격 산정에는 쓰지 않습니다."
GLOBAL_SOURCE = "CLIMADA Data API river_flood 150 arcsec (ISIMIP)"
#: Width : height of the drawn profile frame (design, canvas 「단면」 sheet 2).
PROFILE_ASPECT = 3.4
#: Nearest-centroid cut-off for the global hazard grid (degrees; the grid step is 150").
MAX_CENTROID_DEG = 0.1
#: Global-hazard periods put on the sheet: reanalysis history and the RCP8.5 mid-century.
_GLOBAL_PERIODS = {
    "hist": "historical",
    "rcp85": "rcp85",
}


# --------------------------------------------------------------------------- #
# Pure parts                                                                   #
# --------------------------------------------------------------------------- #
def cut_line(
    lat: float, lon: float, bearing_deg: float, half_km: float, step_km: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample points along a geodesic through ``(lat, lon)``.

    Algorithm:
        $$(\\lambda_i, \\varphi_i) = \\mathrm{fwd}(\\lambda_0, \\varphi_0, \\beta,\\ s_i - h),
        \\quad s_i = i\\,\\Delta,\\ i = 0 \\ldots \\lfloor 2h/\\Delta \\rfloor$$
        ASCII: point_i = geodesic_forward(site, bearing, km_i - half_km), km_i = i * step

    where $\\beta$ is the bearing (deg clockwise from north), $h$ = ``half_km``,
    $\\Delta$ = ``step_km`` (km); the WGS84 ellipsoid is used. ``km`` starts at 0 at the
    western/southern end and grows by exactly ``step_km``; the last sample is at most
    ``2 * half_km``.

    Returns:
        ``(lon, lat, km)`` arrays of equal length.
    """
    from pyproj import Geod

    n = math.floor(2 * half_km / step_km + 1e-9) + 1
    km = np.arange(n, dtype=float) * step_km
    geod = Geod(ellps="WGS84")
    lons, lats, _ = geod.fwd(
        np.full(n, lon), np.full(n, lat), np.full(n, bearing_deg), (km - half_km) * 1000.0
    )
    return np.asarray(lons, dtype=float), np.asarray(lats, dtype=float), km


def review_stamp(classes: dict[str, str], check_depths: dict[str, float | None]) -> dict[str, Any]:
    """The 검토 stamp: does the DEM check depth fall in the official band?

    A return period counts when the site is inside the official map (class != "") and a
    check depth exists. It agrees when the depth lies in the class band.
    ``agree`` if all counted periods agree, ``disagree`` if none does, else ``partial``;
    ``empty`` when nothing could be compared.
    """
    agreed: list[int] = []
    disagreed: list[int] = []
    for k in RP_KEYS:
        code, h = classes.get(k, ""), check_depths.get(k)
        if not code or h is None:
            continue
        (agreed if fd.in_band(code, float(h)) else disagreed).append(int(k[2:]))
    of = len(agreed) + len(disagreed)
    if of == 0:
        return {"state": "empty", "agree": 0, "of": 0, "label": "대조 불가"}
    state = "agree" if not disagreed else ("disagree" if not agreed else "partial")
    parts = []
    if agreed:
        parts.append("·".join(map(str, agreed)) + "년 일치")
    if disagreed:
        parts.append("·".join(map(str, disagreed)) + "년 불일치")
    return {"state": state, "agree": len(agreed), "of": of, "label": ", ".join(parts)}


def vertical_exaggeration(
    length_km: float, z_ground: list[float | None], z_surface: list[float | None]
) -> int:
    """Vertical exaggeration so the profile fills a frame of aspect ``PROFILE_ASPECT``.

    Algorithm:
        $$VE = \\max\\left(1,\\ \\mathrm{round}\\frac{1000\\,L}{A\\,R}\\right),\\quad
        R = \\tfrac12 (\\max z_g - \\min z_g + \\max z_s - \\min z_s)$$
        ASCII: VE = max(1, round(1000 * L / (A * R)))

    $L$ section length (km), $A$ = 3.4 frame width/height, $R$ mean relief (m) of the
    ground and surface profiles. DM-12 (4 km, 23 m / 32 m relief) gives 42.
    """

    def relief(z: list[float | None]) -> float:
        v = [x for x in z if x is not None and math.isfinite(x)]
        return (max(v) - min(v)) if v else 0.0

    r = 0.5 * (relief(z_ground) + relief(z_surface))
    if r <= 0:
        return 1
    return max(1, round(1000.0 * length_km / (PROFILE_ASPECT * r)))


def _clean(v: Any, nd: int = 2) -> float | None:
    """Round to ``nd`` places; None and NaN stay ``None`` (never 0)."""
    if v is None:
        return None
    f = float(v)
    return round(f, nd) if math.isfinite(f) else None


def _band(code: str) -> list[float | None]:
    if not code:
        return [0.0, 0.0]
    lo, hi = fd.BANDS[code]
    return [lo, hi]


def assemble_sheet(
    *,
    sheet_id: str,
    title: str,
    place: str,
    site: dict[str, float],
    cut: dict[str, Any],
    z_ground: list[float | None],
    ground_source: str,
    z_surface: list[float | None],
    surface_source: str,
    section_classes: dict[str, list[str]],
    site_classes: dict[str, str],
    site_z_ground: float | None,
    site_z_surface: float | None,
    check_depths: dict[str, float | None],
    check_dem: str,
    global_depths: dict[str, dict[str, list[float]]] | None,
    date: str,
    use: str = DEFAULT_USE,
    extra_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Put the pieces together as a schema-valid sheet. Missing heights stay ``None``.

    ``cut`` holds ``lon``, ``lat``, ``km`` (sample arrays/lists), ``bearing_deg`` and
    optionally ``length_km`` (default: the last sample's km). ``site.km`` is the distance
    along the cut to the sample nearest the site. ``section.cut.lat`` is the site latitude.
    """
    lons = [float(x) for x in cut["lon"]]
    lats = [float(x) for x in cut["lat"]]
    km = [round(float(x), 3) for x in cut["km"]]
    length_km = float(cut.get("length_km", km[-1]))
    cos_lat = math.cos(math.radians(site["lat"]))
    dist2 = [
        ((lo - site["lon"]) * cos_lat) ** 2 + (la - site["lat"]) ** 2
        for lo, la in zip(lons, lats, strict=True)
    ]
    site_km = km[int(np.argmin(dist2))]
    depth_m = {k: fd.representative_depth(site_classes[k]) for k in RP_KEYS}
    sources = [
        "환경부 홍수위험지도 하천범람지도 (공공누리 제4유형, 겹쳐 읽기만 함)",
        "국토지리정보원 공개 DEM 90 m",
        "Copernicus DEM GLO-30",
    ]
    if global_depths is not None:
        sources.append("CLIMADA Data API river_flood 150″ (ISIMIP)")
    sources += extra_sources or []
    gl: dict[str, Any] = {"tier": "T1", "source": GLOBAL_SOURCE}
    gl.update(global_depths or {})
    return {
        "schema": SCHEMA,
        "sheet_id": sheet_id,
        "hazard": "river_flood",
        "title": title,
        "place": place,
        "use": use,
        "date": date,
        "site": {
            "lat": site["lat"],
            "lon": site["lon"],
            "km": site_km,
            "z_ground_m": _clean(site_z_ground),
            "z_surface_m": _clean(site_z_surface),
            "classes": {k: site_classes[k] for k in RP_KEYS},
            "bands_m": {k: _band(site_classes[k]) for k in RP_KEYS},
            "depth_m": depth_m,
            "check_depth_m": {k: _clean(check_depths.get(k)) for k in RP_KEYS},
            "check_dem": check_dem,
        },
        "section": {
            "cut": {
                "lon0": round(lons[0], 5),
                "lon1": round(lons[-1], 5),
                "lat": round(site["lat"], 5),
                "bearing_deg": float(cut["bearing_deg"]),
                "length_km": length_km,
            },
            "km": km,
            "z_ground_m": [_clean(z) for z in z_ground],
            "ground_source": ground_source,
            "z_surface_m": [_clean(z) for z in z_surface],
            "surface_source": surface_source,
            "classes": {k: list(section_classes[k]) for k in RP_KEYS},
            "vertical_exaggeration": vertical_exaggeration(length_km, z_ground, z_surface),
        },
        "frequency": {
            "return_periods": [100, 200, 500],
            "official": {"tier": "T2", "classes": [site_classes[k] for k in RP_KEYS]},
            "global": gl,
        },
        "stamps": {
            "author": {"state": "T2", "label": "공식 홍수지도 수심 등급"},
            "review": review_stamp(site_classes, check_depths),
            "approve": {"state": "empty", "label": "실측 대조 없음"},
        },
        "sources": sources,
    }


# --------------------------------------------------------------------------- #
# Real data                                                                    #
# --------------------------------------------------------------------------- #
def global_river_flood(
    lat: float, lon: float, roots: list[Path]
) -> dict[str, dict[str, list[float]]] | None:
    """Return period and depth at the nearest CLIMADA river-flood centroid, per period.

    Algorithm:
        $$T_k = 1 \\Big/ \\sum_{j \\le k} f_{(j)}$$
        ASCII: events sorted by depth, deepest first; T_k = 1 / cumulative frequency

    Only depths > 0 are listed (an empty list means no flooding in that period). Reads the
    Korea (``*KOR*``) ``hist`` and ``rcp85`` files under ``roots``; ``None`` if h5py or the
    files are missing.
    """
    try:
        import h5py
        from scipy import sparse
    except ImportError:
        return None
    out: dict[str, dict[str, list[float]]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.glob("**/*KOR*.hdf5")):
            m = re.search(r"150arcsec_(hist|rcp\d+)_(?:KOR_)?(\d{4})_(\d{4})", f.name)
            if not m or m.group(1) not in _GLOBAL_PERIODS:
                continue
            key = f"{_GLOBAL_PERIODS[m.group(1)]}_{m.group(2)}_{m.group(3)}"
            if key in out:
                continue
            with h5py.File(f, "r") as h:
                clat, clon = h["centroids"]["lat"][:], h["centroids"]["lon"][:]
                d2 = ((clon - lon) * math.cos(math.radians(lat))) ** 2 + (clat - lat) ** 2
                j = int(np.argmin(d2))
                if math.sqrt(float(d2[j])) > MAX_CENTROID_DEG:
                    continue
                g = h["intensity"]
                shape = tuple(int(x) for x in g.attrs["shape"])
                mat = sparse.csr_matrix(
                    (g["data"][:], g["indices"][:], g["indptr"][:]), shape=shape
                )
                depth = np.asarray(mat[:, j].todense()).ravel()
                freq = h["frequency"][:]
            order = np.argsort(-depth, kind="stable")
            order = order[depth[order] > 0]
            rp = 1.0 / np.cumsum(freq[order]) if len(order) else np.array([])
            out[key] = {
                "rp": [round(float(x), 1) for x in rp],
                "depth_m": [round(float(x), 2) for x in depth[order]],
            }
    return out or None


def _tally_source(used: list[str], names: dict[str, str]) -> str:
    kinds = sorted({u for u in used if u})
    if not kinds:
        return names["glo"]
    return names[kinds[0]] if len(kinds) == 1 else "mixed"


def build_sheet(
    lat: float,
    lon: float,
    sheet_id: str,
    bearing_deg: float = 90.0,
    half_km: float = 2.0,
    step_km: float = 0.03,
    with_global: bool = True,
) -> dict[str, Any]:
    """Cut a section through ``(lat, lon)`` and read every layer of the sheet from real data.

    Official class: ``classes_at_points`` at every sample and the site. Ground height:
    NGII bare-earth DEM, GLO-30 where it has no value (``ground_source`` names which was
    used, or ``mixed``). Surface height: GLO-30. Check depth at the site per return
    period: FwDET edge depth (strict bare earth first, then GLO-30) in the map file of that
    return period. Nothing but codes and heights is returned.
    """
    from climaterisk_worker._params import offline
    from climaterisk_worker._paths import paths

    root = paths.floodmap_dir()
    if not root.is_dir():
        raise FileNotFoundError(f"홍수위험지도 not found under {root}")
    lons, lats, km = cut_line(lat, lon, bearing_deg, half_km, step_km)
    n = len(km)
    points = [(float(x), float(y)) for x, y in zip(lons, lats, strict=True)] + [(lon, lat)]
    index = fd.map_index(root, paths.cache_dir("floodmap", "rfm_index.json"))
    cidx = fd.ClassIndex()
    res = fd.classes_at_points(points, index, cidx)
    codes = res["codes"]  # [rp][point]
    section_classes = {k: list(codes[i][:n]) for i, k in enumerate(RP_KEYS)}
    site_classes = {k: codes[i][n] for i, k in enumerate(RP_KEYS)}
    xs, ys = np.asarray(res["xy"][0]), np.asarray(res["xy"][1])
    xy = np.column_stack([xs, ys])

    bare_files = sorted(paths.external_dir("ngii_dem").glob("**/*.img"))
    bare = fd.RasterSampler(bare_files) if bare_files else None
    glo = fd.Glo30Sampler(paths.external_dir("copernicus_dem"), allow_download=not offline())
    try:
        z_glo = glo(xy)
        z_bare = bare(xy) if bare is not None else np.full(len(xy), np.nan)
        use_bare = np.isfinite(z_bare)
        z_all = np.where(use_bare, z_bare, z_glo)
        used = [
            "bare" if b else ("glo" if np.isfinite(g) else "")
            for b, g in zip(use_bare, z_glo, strict=True)
        ]
        ground_source = _tally_source(
            used[:n], {"bare": "ngii_bare_earth_90m", "glo": "copernicus_glo30"}
        )
        check_depths, check_dem = _site_check_depths(
            float(xs[n]),
            float(ys[n]),
            site_classes,
            index,
            cidx,
            [
                ("ngii_bare_earth_90m", fd.StrictSampler(bare) if bare is not None else None),
                ("copernicus_glo30", glo),
            ],
        )
    finally:
        glo.close()
        if bare is not None:
            bare.close()

    global_depths = None
    if with_global:
        roots = [
            paths.climada_data_dir() / "hazard" / "river_flood",
            paths.external_dir("climada", "hazard", "river_flood"),
        ]
        global_depths = global_river_flood(lat, lon, roots)
    return assemble_sheet(
        sheet_id=sheet_id,
        title=f"{sheet_id} 하천 범람 삼면도",
        place=f"위도 {lat:.4f}, 경도 {lon:.4f}",
        site={"lat": lat, "lon": lon},
        cut={
            "lon": lons,
            "lat": lats,
            "km": km,
            "bearing_deg": bearing_deg,
            "length_km": round(2 * half_km, 3),
        },
        z_ground=[_clean(v) for v in z_all[:n]],
        ground_source=ground_source,
        z_surface=[_clean(v) for v in z_glo[:n]],
        surface_source="copernicus_glo30",
        section_classes=section_classes,
        site_classes=site_classes,
        site_z_ground=_clean(z_all[n]),
        site_z_surface=_clean(z_glo[n]),
        check_depths=check_depths,
        check_dem=check_dem,
        global_depths=global_depths,
        date=_date.today().isoformat(),
    )


def _site_check_depths(
    x: float,
    y: float,
    site_classes: dict[str, str],
    index: list[dict[str, Any]],
    cidx: fd.ClassIndex,
    dems: list[tuple[str, Callable[[np.ndarray], np.ndarray] | None]],
) -> tuple[dict[str, float | None], str]:
    """Edge depth at the site for each return period, from that period's own map file.

    A return period with no map file covering the site (class carried from a 100-y local
    map, or outside) gets ``None``. The first DEM giving a finite depth is used and named.
    """
    out: dict[str, float | None] = dict.fromkeys(RP_KEYS)
    used: list[str] = []
    cache: dict[str, list[tuple[str, Any]]] = {}
    for k in RP_KEYS:
        if not site_classes[k]:
            continue
        rp = int(k[2:])
        best: tuple[str, str] | None = None  # (code, path)
        for row in index:
            x0, y0, x1, y1 = row["bounds"]
            if row["rp"] != rp or not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            code = cidx.code_at(row["path"], x, y)
            if code and (best is None or code > best[0]):
                best = (code, row["path"])
        if best is None:
            continue
        path = best[1]
        if path not in cache:
            cache[path] = fd._read_polygons(path)
        for name, dem in dems:
            if dem is None:
                continue
            h = fd.edge_depth(x, y, cache[path], dem)
            if np.isfinite(h):
                out[k] = float(h)
                used.append(name)
                break
    return out, (used[0] if used else "none")
