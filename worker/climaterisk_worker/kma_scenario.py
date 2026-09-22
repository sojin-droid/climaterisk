"""KMA national standard climate scenario (남한상세 1 km) — the Korean heat-hazard source.

Korean counterpart of :mod:`eobs`. Loads the Korea Meteorological Administration's
**남한상세 (South-Korea high-resolution) daily mean temperature** grids and turns them
into the same :class:`~climaterisk_worker.eobs.SummerDailyMean` arrays the heat perils consume,
so the on-ramp (``heat_mortality.grid_from_summer_tmax`` → ``hazard_convert``) is shared.

Data (기상청 기후변화 시나리오 활용매뉴얼 v5.1, 2024-12; files verified 2026-09-11)
---------------------------------------------------------------------------------
* Analysis grid: 751 (lon) × 601 (lat) at 0.01° (~1 km), origin (33.0 N, 124.5 E),
  regular lat/lon WGS84 — no reprojection needed. Missing value -9990.
* Historical: **MK-PRISM v3.1** observation-based grid, 2000–2019, served as NetCDF on a
  *finer* 1201 × 1501 grid at 0.005° whose even nodes coincide with the 0.01° grid, so it
  is subsampled onto it (:func:`_to_native`).
* Future: SSP1-2.6 / 2-4.5 / 3-7.0 / 5-8.5, 2021–2100, 5-RCM **ensemble mean** (5ENSMN),
  in 10-year archives. The 남한상세 cards offer **ASCII only** — no NetCDF: headerless,
  one line per day, 751 × 601 values per line in row-major order with latitude ascending,
  366 lines in a leap year. Orientation was confirmed against the MK-PRISM land mask
  (98.2 % agreement; 85.4 % with latitude flipped).
* Variable: ``TA`` (평균기온, daily mean). Daily **mean** temperature is deliberate — the
  citable Korean threshold and exposure-response estimates are all in daily mean
  (Kim 2020, doi:10.3390/ijerph17165720; see ``docs/HEAT_MORTALITY_PROVENANCE.md``).
* Distribution: 기후변화 상황지도 (climate.go.kr/atlas/ana/cdd), login required, as
  ``*.tar.gz`` archives whose members are **one calendar year each**::

      MKPRISM_MKPRISMv31_TA_gridraw_daily_2000_2019_nc.tar.gz   # → …_daily_2000.nc, …
      AR6_SSP585_5ENSMN_skorea_TA_gridraw_daily_2021_2030_asc.tar.gz  # → …_2021.txt, …

  Note the two spellings: the AR6 products carry a ``skorea`` token and MK-PRISM does not.
  Drop the archives under ``~/climada/data/kma/`` or point ``CLIMATERISK_KMA_DIR`` at the
  folder. NetCDF archives are unpacked by :func:`extract_archives`; **ASCII archives never
  are** — one ASCII year is 1.3 GB and a scenario 13 GB, so :func:`_iter_asc_seasons`
  streams the tar and parses only the 122 Jun–Sep lines, writing nothing to disk.

Honest caveats
--------------
* **Ensemble-mean daily fields damp extremes.** Averaging five models day by day removes
  each model's own hot spells, so exceedance degree-days from 5ENSMN are biased *low*
  relative to any single realisation. Treat future heat loads as a lower bound; the
  historical MK-PRISM grid has no such damping.
* The 1 km product is a statistical downscale (observed 1 km climatology + model
  anomaly). Its spatial detail is climatological, not weather.
"""

from __future__ import annotations

import os
import re
import tarfile
import warnings
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import numpy as np

from climaterisk_worker.eobs import SEASON_DAYS, SEASON_END, SEASON_START, SummerDailyMean

_HOME_KMA = Path.home() / "climada" / "data" / "kma"

#: File-name token of the variable the heat model consumes. ``TA`` is 평균기온 (daily mean):
#: every citable threshold and exposure-response estimate is expressed in daily mean
#: temperature (``docs/HEAT_MORTALITY_PROVENANCE.md``). ``TAMAX`` (최고평균기온) is what the
#: model used before 2026-09-09 and is no longer read by default.
DEFAULT_VARIABLE = "TA"

MISSING_VALUE = -9990.0
GRID_RES_DEG = 0.01
GRID_ORIGIN_LAT_LON = (33.0, 124.5)
GRID_SHAPE_LAT_LON = (601, 751)


def native_grid() -> tuple[np.ndarray, np.ndarray]:
    """Return (lat, lon) node coordinates of the common 0.01 deg 남한상세 grid."""
    lat0, lon0 = GRID_ORIGIN_LAT_LON
    n_lat, n_lon = GRID_SHAPE_LAT_LON
    return (
        lat0 + GRID_RES_DEG * np.arange(n_lat),
        lon0 + GRID_RES_DEG * np.arange(n_lon),
    )


#: Platform ``climate_scenario`` key → KMA scenario token(s) in the file name.
SCENARIO_TOKENS: dict[str, tuple[str, ...]] = {
    "historical": ("MKPRISMv21", "MKPRISMv31", "MKPRISMv12", "MKPRISMv11"),
    "rcp26": ("SSP126",),
    "rcp45": ("SSP245",),
    "rcp60": ("SSP370",),
    "rcp85": ("SSP585",),
}

#: File-name grammar of the portal's members and archives. Both spellings occur in the
#: wild: the AR6 futures carry a ``skorea`` token and the MK-PRISM observations do not,
#: and members are one calendar year while archives carry the ``y0_y1`` span.
_NAME_RE = re.compile(
    r"^(?P<prefix>AR6|MKPRISM)_(?P<scen>[A-Za-z0-9]+)_(?:(?P<model>(?!skorea_)[A-Za-z0-9]+)_)?"
    r"(?:skorea_)?(?P<var>[A-Z]+)_gridraw_(?P<step>daily|monthly|yearly)_"
    r"(?P<y0>\d{4})(?:_(?P<y1>\d{4}))?(?:_(?:nc|asc))?\.(?P<ext>nc|txt|tar\.gz)$"
)


class KmaUnavailable(Exception):
    """Raised when no KMA scenario file is present; carries actionable help."""

    HELP = (
        "No KMA 남한상세 TA (daily mean) NetCDF found. Log in to 기후변화 상황지도 "
        "(https://climate.go.kr/atlas/ana/cdd), filter 남한상세 › 격자 › 평균기온 › "
        "일자료 › nc, "
        "download the tar.gz archives and drop them under ~/climada/data/kma/ "
        "(or set CLIMATERISK_KMA_DIR). Run `scripts/heat_korea.py files` to see what is "
        "expected and what is present."
    )

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.HELP
        super().__init__(self.detail)


@dataclass(frozen=True)
class KmaFile:
    """One KMA scenario NetCDF and the metadata encoded in its file name."""

    path: Path
    scenario_token: str
    model: str | None
    variable: str
    step: str
    year_start: int
    year_end: int
    #: ``nc`` for a NetCDF on disk, ``txt`` for an ASCII year inside an ``_asc`` archive.
    ext: str = "nc"
    #: Member name inside :attr:`path` when :attr:`ext` is ``txt`` (archives are never
    #: unpacked: one ASCII year is 1.3 GB and a scenario is 13 GB).
    member: str | None = None

    @property
    def platform_scenario(self) -> str | None:
        """Platform ``climate_scenario`` key this file belongs to (None if unknown)."""
        for key, tokens in SCENARIO_TOKENS.items():
            if self.scenario_token in tokens:
                return key
        return None


def kma_dir() -> Path:
    """Resolve the drop folder (``CLIMATERISK_KMA_DIR`` → ``~/climada/data/kma``)."""
    env = os.environ.get("CLIMATERISK_KMA_DIR")
    return Path(env) if env else _HOME_KMA


def parse_name(path: Path) -> KmaFile | None:
    """Parse a KMA file name into a :class:`KmaFile`, or None if it is not one."""
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    y0 = int(m["y0"])
    return KmaFile(
        path=path,
        scenario_token=m["scen"],
        model=m["model"],
        variable=m["var"],
        step=m["step"],
        year_start=y0,
        year_end=int(m["y1"]) if m["y1"] else y0,
        ext="nc" if m["ext"] == "nc" else "txt",
    )


def extract_archives(directory: Path | None = None) -> list[Path]:
    """Unpack every ``*.tar.gz`` in the drop folder whose ``.nc`` members are not yet present.

    Idempotent: an archive whose members already exist is skipped, so re-running after a
    partial download is safe. Only ``.nc`` members are extracted (flat, into the folder).

    Returns:
        Paths of the ``.nc`` files extracted by this call.
    """
    d = directory or kma_dir()
    if not d.is_dir():
        return []
    out: list[Path] = []
    for archive in sorted(d.glob("*.tar.gz")):
        if archive.name.endswith("_asc.tar.gz"):
            continue  # ASCII scenarios are streamed in place, never unpacked
        try:
            with tarfile.open(archive, "r:gz") as tf:
                members = [m for m in tf.getmembers() if m.isfile() and m.name.endswith(".nc")]
                for m in members:
                    target = d / Path(m.name).name
                    if target.is_file() and target.stat().st_size == m.size:
                        continue
                    src = tf.extractfile(m)
                    if src is None:  # pragma: no cover - defensive
                        continue
                    with open(target, "wb") as dst:
                        while chunk := src.read(1 << 24):
                            dst.write(chunk)
                    out.append(target)
        except (tarfile.TarError, OSError) as exc:  # corrupt/partial download
            raise KmaUnavailable(f"could not read {archive.name}: {exc}") from exc
    return out


def list_files(
    directory: Path | None = None,
    variable: str = DEFAULT_VARIABLE,
    step: str = "daily",
    scenario: str | None = None,
) -> list[KmaFile]:
    """List KMA NetCDFs in the drop folder, optionally for one platform scenario key.

    Archives are unpacked first so a freshly downloaded ``tar.gz`` is enough.
    """
    d = directory or kma_dir()
    extract_archives(d)
    files = [f for p in sorted(d.glob("*.nc")) if (f := parse_name(p)) is not None]
    for archive in sorted(d.glob("*_asc.tar.gz")):
        files.extend(asc_entries(archive))
    files = [f for f in files if f.variable == variable and f.step == step]
    if scenario is not None:
        files = [f for f in files if f.platform_scenario == scenario]
    return sorted(files, key=lambda f: (f.year_start, f.path.name, f.member or ""))


def asc_entries(archive: Path) -> list[KmaFile]:
    """Expand an ``*_asc.tar.gz`` into one :class:`KmaFile` per calendar year it spans.

    The member names are *derived* from the archive name rather than read from the tar
    index: indexing a gzipped tar means decompressing all 13 GB of it, and the loader
    walks the members sequentially anyway, skipping any year that turns out to be absent.
    """
    info = parse_name(archive)
    if info is None:
        return []
    stem = archive.name[: -len("_asc.tar.gz")]
    span = f"_{info.year_start}_{info.year_end}"
    base = stem[: -len(span)] if stem.endswith(span) else stem
    return [
        KmaFile(
            path=archive,
            scenario_token=info.scenario_token,
            model=info.model,
            variable=info.variable,
            step=info.step,
            year_start=y,
            year_end=y,
            ext="txt",
            member=f"{base}_{y}.txt",
        )
        for y in range(info.year_start, info.year_end + 1)
    ]


def available(scenario: str = "historical", directory: Path | None = None) -> bool:
    """True when at least one daily TA (daily mean) file exists for ``scenario``."""
    try:
        return bool(list_files(directory, scenario=scenario))
    except KmaUnavailable:
        return False


def _find_names(ds) -> tuple[str, str, str, str]:  # type: ignore[no-untyped-def]
    """Return (variable, time, lat, lon) names in an opened dataset."""
    lat = next((n for n in ("latitude", "lat", "y") if n in ds.coords or n in ds.dims), None)
    lon = next((n for n in ("longitude", "lon", "x") if n in ds.coords or n in ds.dims), None)
    time = next((n for n in ("time", "t") if n in ds.coords or n in ds.dims), None)
    if lat is None or lon is None or time is None:
        raise KmaUnavailable(f"cannot find time/lat/lon in {list(ds.dims)}")
    var = next((v for v in ds.data_vars if ds[v].ndim == 3), None)
    if var is None:
        raise KmaUnavailable(f"no 3-D variable in {list(ds.data_vars)}")
    return var, time, lat, lon


def _block_mean(arr: np.ndarray, k: int) -> np.ndarray:
    """Mean over k×k blocks of the trailing two axes (NaN-aware), trimming the remainder.

    Algorithm:
        $$ \\bar T_{d,I,J} = \\frac{1}{|B|}\\sum_{(i,j)\\in B_{I,J}} T_{d,i,j} $$
        ASCII: block mean of T over each k-by-k cell block B, ignoring NaN.
    """
    if k <= 1:
        return arr
    *lead, n_lat, n_lon = arr.shape
    n_lat_b, n_lon_b = n_lat // k, n_lon // k
    arr = arr[..., : n_lat_b * k, : n_lon_b * k]
    arr = arr.reshape(*lead, n_lat_b, k, n_lon_b, k)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN (sea) blocks → NaN
        return np.nanmean(arr, axis=(-3, -1))


#: One Jun-Sep season: ``(year, (SEASON_DAYS, n_lat, n_lon), lat, lon)``.
_Season = tuple[int, np.ndarray, np.ndarray, np.ndarray]


def _season_bounds(year: int) -> tuple[int, int]:
    """Return [start, end) zero-based day-of-year indices of the Jun-Sep season.

    KMA daily files carry a real Gregorian calendar (366 rows in a leap year, verified
    on SSP2-4.5 2024), so the season offset is leap-aware.
    """
    from datetime import date

    i0 = date(year, *SEASON_START).timetuple().tm_yday - 1
    i1 = date(year, *SEASON_END).timetuple().tm_yday
    return i0, i1


def _to_native(
    arr: np.ndarray, lat: np.ndarray, lon: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Subsample a grid finer than 0.01 deg onto the common 남한상세 nodes.

    MK-PRISM v3.1 is served on 1201x1501 at 0.005 deg whose even nodes coincide exactly
    with the 0.01 deg grid the AR6 scenario files use, so nearest-node selection is
    lossless co-registration. A block mean would instead place cell centres 0.0025 deg
    off the scenario grid, and baseline-band inheritance in ``heat_mortality`` rejects a
    hazard whose coordinates do not match the window it inherits its comfort band from.

    Grids already at 0.01 deg (the AR6 products, and test fixtures) pass through.

    Returns:
        ``(arr, lat, lon)`` on the 0.01 deg spacing.
    """
    lat = lat.astype(float)
    lon = lon.astype(float)
    step = float(np.median(np.abs(np.diff(lat)))) if lat.size > 1 else GRID_RES_DEG
    if step >= GRID_RES_DEG * 0.9:
        return arr, lat, lon
    k = round(GRID_RES_DEG / step)
    if k < 2 or abs(k * step - GRID_RES_DEG) > 0.05 * GRID_RES_DEG:
        raise KmaUnavailable(
            f"grid spacing {step:g} deg is not an integer refinement of {GRID_RES_DEG} deg"
        )
    lat0, lon0 = GRID_ORIGIN_LAT_LON
    snapped = []
    for name, c, origin in (("latitude", lat, lat0), ("longitude", lon, lon0)):
        node = (c[::k] - origin) / GRID_RES_DEG
        if np.abs(node - np.round(node)).max() > 0.05:
            raise KmaUnavailable(
                f"{name} starts at {c[0]:g}, off the {GRID_RES_DEG} deg grid from {origin:g} "
                "— subsampling would not co-register with the scenario files"
            )
        # Snap to the exact node values. The file stores coordinates as float32, whose
        # error reaches 2e-6 deg near 132 E — enough that a block mean of the file's own
        # numbers and one of the computed grid differ in the 6th decimal, which is how a
        # footprint intersection silently lost 57 % of the cells.
        snapped.append(origin + GRID_RES_DEG * np.round(node))
    return arr[..., ::k, ::k], snapped[0], snapped[1]


def _mask_missing(arr: np.ndarray, fill: float | None = None) -> np.ndarray:
    """Replace KMA no-data (-9990) and any declared ``_FillValue`` with NaN, in place."""
    bad = arr <= MISSING_VALUE + 1.0
    if fill is not None:
        bad |= arr == np.float32(fill)
    arr[bad] = np.nan
    return arr


def _iter_nc_seasons(f: KmaFile, wanted: range) -> Iterator[_Season]:
    """Yield ``(year, (SEASON_DAYS, n_lat, n_lon), lat, lon)`` Jun-Sep slices from a NetCDF."""
    import xarray as xr

    with xr.open_dataset(f.path) as ds:
        var, tname, latname, lonname = _find_names(ds)
        da = ds[var]
        months = da[tname].dt.month
        da = da.sel({tname: (months >= SEASON_START[0]) & (months <= SEASON_END[0])})
        lat, lon = ds[latname].values, ds[lonname].values
        flip = lat.size > 1 and lat[1] < lat[0]
        fill = da.attrs.get("_FillValue", da.encoding.get("_FillValue"))
        for y in (int(v) for v in np.unique(da[tname].dt.year.values)):
            if y not in wanted:
                continue
            sel = da.sel({tname: da[tname].dt.year == y})
            if sel.sizes[tname] != SEASON_DAYS:
                continue  # incomplete season (partial file) — skip, never pad
            arr = sel.transpose(tname, latname, lonname).values.astype(np.float32)
            if flip:
                arr = arr[:, ::-1, :]
            yield (y, *_to_native(_mask_missing(arr, fill), lat[::-1] if flip else lat, lon))


def _iter_lines(src: IO[bytes], chunk: int = 1 << 22) -> Iterator[bytes]:
    """Yield newline-delimited records from a non-seekable byte stream.

    A tar opened in stream mode (``r|gz``) hands back a member whose ``seekable()`` raises,
    so :class:`io.TextIOWrapper` cannot wrap it; one ASCII day is ~3.6 MB, hence the large
    chunk.
    """
    buf = b""
    while data := src.read(chunk):
        buf += data
        parts = buf.split(b"\n")
        buf = parts.pop()
        yield from parts
    if buf:
        yield buf


def _iter_asc_seasons(archive: Path, members: dict[str, int], wanted: range) -> Iterator[_Season]:
    """Stream Jun-Sep days out of an ``*_asc.tar.gz`` without unpacking it.

    Each ASCII year is one line per day of 751x601 values (~1.3 GB); only the 122 season
    lines are parsed and the rest are discarded, so nothing is written to disk.
    """
    n_lat, n_lon = GRID_SHAPE_LAT_LON
    #: Walking past a member in stream mode still costs its full decompression (~1.3 GB),
    #: so stop as soon as every requested year has been read instead of draining the tar.
    remaining = {y for y in members.values() if y in wanted}
    with tarfile.open(archive, "r|gz") as tf:  # sequential stream — no random access
        for m in tf:
            if not remaining:
                break
            year = members.get(Path(m.name).name)
            if not m.isfile() or year is None or year not in remaining:
                continue
            remaining.discard(year)
            src = tf.extractfile(m)
            if src is None:  # pragma: no cover - defensive
                continue
            i0, i1 = _season_bounds(year)
            rows: list[np.ndarray] = []
            for i, line in enumerate(_iter_lines(src)):
                if i < i0:
                    continue  # counted, never parsed
                if i >= i1:
                    break
                rows.append(np.fromstring(line.decode("ascii"), sep=" ", dtype=np.float32))
            if len(rows) != SEASON_DAYS:
                continue  # truncated member — skip, never pad
            arr = np.stack(rows)
            if arr.shape[1] != n_lat * n_lon:
                raise KmaUnavailable(
                    f"{m.name}: {arr.shape[1]} values per day, expected {n_lat * n_lon} "
                    f"({n_lon}x{n_lat} at {GRID_RES_DEG} deg)"
                )
            lat_grid, lon_grid = native_grid()
            yield year, _mask_missing(arr.reshape(SEASON_DAYS, n_lat, n_lon)), lat_grid, lon_grid


#: Decimals a coordinate is rounded to before two grids' cells are matched. 1e-4 deg is
#: ~11 m — far below the 0.05 deg (~5 km) analysis cell, and far above float32 coordinate
#: error (~2e-6 deg), so it separates genuine neighbours without splitting identical nodes.
_COORD_DP = 4


def _keys(lat: np.ndarray, lon: np.ndarray) -> list[tuple[float, float]]:
    """Cell identities for ``lat``/``lon``, rounded so float noise cannot split a node."""
    return [
        (round(float(a), _COORD_DP), round(float(b), _COORD_DP))
        for a, b in zip(lat, lon, strict=True)
    ]


def _locate(
    lat: np.ndarray, lon: np.ndarray, wanted: tuple[np.ndarray, np.ndarray], scenario: str
) -> np.ndarray:
    """Indices of ``wanted`` cells within a grid, in the order ``wanted`` gives them."""
    have = {key: i for i, key in enumerate(_keys(lat, lon))}
    idx, absent = [], 0
    for key in _keys(*wanted):
        i = have.get(key)
        if i is None:
            absent += 1
        else:
            idx.append(i)
    if absent:
        raise KmaUnavailable(
            f"{absent} of {len(wanted[0])} requested cells are outside the {scenario} grid "
            "— derive the footprint with common_footprint() over every scenario first"
        )
    return np.array(idx, dtype=int)


def footprint(
    scenario: str = "historical",
    coarsen: int = 5,
    variable: str = DEFAULT_VARIABLE,
    directory: Path | None = None,
    max_missing_frac: float = 0.02,
) -> tuple[np.ndarray, np.ndarray]:
    """Land-cell ``(lat, lon)`` of one scenario, read from its first available season.

    Cheap relative to a full load (one season instead of all of them) because it exists
    only to be intersected by :func:`common_footprint`.
    """
    files = list_files(directory, variable=variable, step="daily", scenario=scenario)
    if not files:
        raise KmaUnavailable()
    first = min(f.year_start for f in files)
    obs = load_summer_tmax(
        scenario,
        year_start=first,
        year_end=first,
        coarsen=coarsen,
        variable=variable,
        directory=directory,
        max_missing_frac=max_missing_frac,
    )
    return obs.lat, obs.lon


def common_footprint(
    scenarios: Sequence[str],
    coarsen: int = 5,
    variable: str = DEFAULT_VARIABLE,
    directory: Path | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Cells present in every listed scenario, in the first scenario's grid order.

    MK-PRISM (observation-based) and the AR6 5-RCM ensemble mean carry different land
    masks, so an observed window and an SSP window loaded independently do not describe
    the same set of cells. Comparing them directly would fold a footprint change into the
    scenario delta, and ``heat_mortality.grid_from_summer_tmax`` refuses the mismatched
    baseline band outright. Pin every window to this intersection instead.
    """
    if not scenarios:
        raise ValueError("scenarios must not be empty")
    lat, lon = footprint(scenarios[0], coarsen, variable, directory)
    for other in scenarios[1:]:
        o_lat, o_lon = footprint(other, coarsen, variable, directory)
        shared = set(_keys(o_lat, o_lon))
        keep = np.array([key in shared for key in _keys(lat, lon)])
        if not keep.any():
            raise KmaUnavailable(f"{scenarios[0]} and {other} share no cell — check coarsen")
        lat, lon = lat[keep], lon[keep]
    return lat, lon


def load_summer_tmax(
    scenario: str = "historical",
    year_start: int | None = None,
    year_end: int | None = None,
    coarsen: int = 5,
    variable: str = DEFAULT_VARIABLE,
    directory: Path | None = None,
    max_missing_frac: float = 0.02,
    restrict_to: tuple[np.ndarray, np.ndarray] | None = None,
) -> SummerDailyMean:
    """Load Jun–Sep daily **mean** temperature from KMA 남한상세 files for one scenario.

    Files are processed one season at a time (a 1 km season slice is ~220 MB), block-
    averaged to ``coarsen`` × 0.01° (default 0.05° ≈ 5 km → ~18k cells before the sea is
    dropped), and stacked into ``(n_cells, n_years, SEASON_DAYS)``. Sea and no-data cells
    (missing in more than ``max_missing_frac`` of season-days) are dropped; residual gaps
    are filled with that cell-season's own mean so heat loads are not biased low.

    Args:
        scenario: Platform key — ``historical`` (MK-PRISM) or ``rcp26/45/60/85``
            (mapped to SSP126/245/370/585 per :data:`SCENARIO_TOKENS`).
        year_start: First season to include (default: first available).
        year_end: Last season to include (default: last available).
        coarsen: Block size in 0.01° cells (1 keeps the native grid, ~450k cells).
        variable: File-name variable token (``TA`` = 평균기온, daily mean).
        directory: Drop folder override.
        max_missing_frac: Cell drop threshold. Ignored when ``restrict_to`` is given.
        restrict_to: ``(lat, lon)`` of the cells to keep, in that order — normally from
            :func:`common_footprint`. MK-PRISM and the AR6 ensemble do not share a land
            mask (4691 vs 4326 cells at 0.05 deg over Korea), so windows meant to be
            compared must be pinned to one footprint: otherwise a scenario delta mixes a
            change in heat load with a change in which cells were counted.

    Returns:
        A :class:`~climaterisk_worker.eobs.SummerDailyMean` with calendar ``years``.

    Raises:
        KmaUnavailable: no matching file, or no complete season in range.
    """
    files = list_files(directory, variable=variable, step="daily", scenario=scenario)
    if not files:
        raise KmaUnavailable()

    wanted = range(
        year_start if year_start is not None else min(f.year_start for f in files),
        (year_end if year_end is not None else max(f.year_end for f in files)) + 1,
    )
    seasons: list[np.ndarray] = []
    years: list[int] = []
    lat_c: np.ndarray | None = None
    lon_c: np.ndarray | None = None

    def take(season: _Season) -> None:
        nonlocal lat_c, lon_c
        y, arr, lat, lon = season
        if lat_c is None:
            shape = arr.shape[-2:]
            lat_c = _block_mean(np.broadcast_to(lat[:, None], shape), coarsen)
            lon_c = _block_mean(np.broadcast_to(lon[None, :], shape), coarsen)
        coarse = _block_mean(arr, coarsen)
        if coarse.shape[-2:] != lat_c.shape:
            raise KmaUnavailable(
                f"season {y} is on a {arr.shape[-2:]} grid but earlier seasons are on "
                f"{lat_c.shape} after coarsening — mixed products cannot be stacked"
            )
        seasons.append(coarse)
        years.append(y)

    for archive in sorted({f.path for f in files if f.ext == "txt"}):
        members = {f.member: f.year_start for f in files if f.path == archive and f.member}
        for season in _iter_asc_seasons(archive, members, wanted):
            take(season)
    for f in [f for f in files if f.ext == "nc"]:
        for season in _iter_nc_seasons(f, wanted):
            take(season)

    if not seasons or lat_c is None or lon_c is None:
        span = f"{year_start}..{year_end}" if (year_start or year_end) else "any year"
        raise KmaUnavailable(
            f"no complete Jun-Sep season for {scenario} in {span} across {len(files)} file(s)"
        )

    # (year, day, lat, lon) -> (cell, year, day)
    stack = np.stack(seasons, axis=0)
    order = np.argsort(years)
    stack, years_sorted = stack[order], [years[i] for i in order]
    n_y = len(years_sorted)
    flat = np.moveaxis(stack, (0, 1), (-2, -1)).reshape(-1, n_y, SEASON_DAYS)

    all_lat, all_lon = lat_c.ravel(), lon_c.ravel()
    if restrict_to is None:
        keep = np.isnan(flat).mean(axis=(1, 2)) <= max_missing_frac
        if not keep.any():
            raise KmaUnavailable(f"no land cells with data for {scenario}")
    else:
        keep = _locate(all_lat, all_lon, restrict_to, scenario)
    flat, lat_f, lon_f = flat[keep], all_lat[keep], all_lon[keep]
    if np.isnan(flat).any():
        season_mean = np.nanmean(flat, axis=2, keepdims=True)
        season_mean = np.where(np.isnan(season_mean), np.nanmean(flat), season_mean)
        flat = np.where(np.isnan(flat), season_mean, flat)

    tokens = sorted({f.scenario_token for f in files})
    model = sorted({f.model for f in files if f.model})
    res = GRID_RES_DEG * max(coarsen, 1)
    return SummerDailyMean(
        lat=lat_f.astype(float),
        lon=lon_f.astype(float),
        years=np.array(years_sorted, dtype=int),
        tmax=flat.astype(float),
        source=(
            f"KMA 남한상세 {variable} {'/'.join(tokens)}"
            + (f" {'/'.join(model)}" if model else "")
            + f" ({years_sorted[0]}-{years_sorted[-1]}, {res:.2f} deg block mean"
            + (", ensemble-mean daily fields: extremes damped" if model else "")
            + ")"
        ),
    )
