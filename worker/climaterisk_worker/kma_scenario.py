"""KMA national standard climate scenario (남한상세 1 km) — the Korean heat-hazard source.

Korean counterpart of :mod:`eobs`. Loads the Korea Meteorological Administration's
**남한상세 (South-Korea high-resolution) daily mean temperature** grids and turns them
into the same :class:`~climaterisk_worker.eobs.SummerDailyMean` arrays the heat perils consume,
so the on-ramp (``heat_mortality.grid_from_summer_tmax`` → ``hazard_convert``) is shared.

Data (기상청 기후변화 시나리오 활용매뉴얼 v5.1, 2024-12; verified 2026-09-02)
-----------------------------------------------------------------------------
* Grid: 751 (lon) × 601 (lat) at 0.01° (~1 km), origin (33.0 N, 124.5 E), regular
  lat/lon WGS84 — no reprojection needed. Missing value -9990. Gregorian calendar.
* Historical: MK-PRISM v2.1 observation-based grid, 2000–2019.
* Future: SSP1-2.6 / 2-4.5 / 3-7.0 / 5-8.5, 2021–2100, 5-RCM **ensemble mean** (5ENSMN),
  served in 10-year daily files.
* Variable: ``TA`` (평균기온, daily mean). Daily **mean** temperature is deliberate — the
  citable Korean threshold and exposure-response estimates are all in daily mean
  (Kim 2020, doi:10.3390/ijerph17165720; see ``docs/HEAT_MORTALITY_PROVENANCE.md``).
* Distribution: 기후변화 상황지도 (climate.go.kr/atlas/ana/cdd), login required, as
  ``*.tar.gz`` archives, e.g.::

      AR6_SSP585_5ENSMN_skorea_TA_gridraw_daily_2021_2030_nc.tar.gz
      MKPRISM_MKPRISMv21_skorea_TA_gridraw_daily_2000_2019_nc.tar.gz

  Drop them (archives or the extracted ``.nc``) under ``~/climada/data/kma/`` or point
  ``CLIMATERISK_KMA_DIR`` at the folder; :func:`extract_archives` unpacks what it finds.

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
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from climaterisk_worker.eobs import SEASON_DAYS, SEASON_END, SEASON_START, SummerDailyMean

_HOME_KMA = Path.home() / "climada" / "data" / "kma"

MISSING_VALUE = -9990.0
GRID_RES_DEG = 0.01
GRID_ORIGIN_LAT_LON = (33.0, 124.5)
GRID_SHAPE_LAT_LON = (601, 751)

#: Platform ``climate_scenario`` key → KMA scenario token(s) in the file name.
SCENARIO_TOKENS: dict[str, tuple[str, ...]] = {
    "historical": ("MKPRISMv21", "MKPRISMv31", "MKPRISMv12", "MKPRISMv11"),
    "rcp26": ("SSP126",),
    "rcp45": ("SSP245",),
    "rcp60": ("SSP370",),
    "rcp85": ("SSP585",),
}

_NAME_RE = re.compile(
    r"^(?P<prefix>AR6|MKPRISM)_(?P<scen>[A-Za-z0-9]+)_(?:(?P<model>[A-Za-z0-9]+)_)?"
    r"skorea_(?P<var>[A-Z]+)_gridraw_(?P<step>daily|monthly|yearly)_"
    r"(?P<y0>\d{4})_(?P<y1>\d{4})(?:_nc)?\.nc$"
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
    return KmaFile(
        path=path,
        scenario_token=m["scen"],
        model=m["model"],
        variable=m["var"],
        step=m["step"],
        year_start=int(m["y0"]),
        year_end=int(m["y1"]),
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
    variable: str = "TA",
    step: str = "daily",
    scenario: str | None = None,
) -> list[KmaFile]:
    """List KMA NetCDFs in the drop folder, optionally for one platform scenario key.

    Archives are unpacked first so a freshly downloaded ``tar.gz`` is enough.
    """
    d = directory or kma_dir()
    extract_archives(d)
    files = [f for p in sorted(d.glob("*.nc")) if (f := parse_name(p)) is not None]
    files = [f for f in files if f.variable == variable and f.step == step]
    if scenario is not None:
        files = [f for f in files if f.platform_scenario == scenario]
    return sorted(files, key=lambda f: (f.year_start, f.path.name))


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


def load_summer_tmax(
    scenario: str = "historical",
    year_start: int | None = None,
    year_end: int | None = None,
    coarsen: int = 5,
    variable: str = "TA",
    directory: Path | None = None,
    max_missing_frac: float = 0.02,
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
        max_missing_frac: Cell drop threshold.

    Returns:
        A :class:`~climaterisk_worker.eobs.SummerDailyMean` with calendar ``years``.

    Raises:
        KmaUnavailable: no matching file, or no complete season in range.
    """
    import xarray as xr

    files = list_files(directory, variable=variable, step="daily", scenario=scenario)
    if not files:
        raise KmaUnavailable()

    seasons: list[np.ndarray] = []
    years: list[int] = []
    lat_c: np.ndarray | None = None
    lon_c: np.ndarray | None = None
    for f in files:
        with xr.open_dataset(f.path) as ds:
            var, tname, latname, lonname = _find_names(ds)
            da = ds[var]
            months = da[tname].dt.month
            da = da.sel({tname: (months >= SEASON_START[0]) & (months <= SEASON_END[0])})
            yrs_here = np.unique(da[tname].dt.year.values)
            for y in yrs_here:
                y = int(y)
                if (year_start is not None and y < year_start) or (
                    year_end is not None and y > year_end
                ):
                    continue
                sel = da.sel({tname: da[tname].dt.year == y})
                if sel.sizes[tname] != SEASON_DAYS:
                    continue  # incomplete season (partial file) — skip, never pad
                arr = sel.transpose(tname, latname, lonname).values.astype(np.float32)
                fill = da.attrs.get("_FillValue", da.encoding.get("_FillValue"))
                bad = arr <= MISSING_VALUE + 1.0
                if fill is not None:
                    bad |= arr == np.float32(fill)
                arr[bad] = np.nan
                seasons.append(_block_mean(arr, coarsen))
                years.append(y)
                if lat_c is None:
                    lat_c = _block_mean(
                        np.broadcast_to(
                            ds[latname].values[:, None].astype(float),
                            (ds.sizes[latname], ds.sizes[lonname]),
                        ),
                        coarsen,
                    )
                    lon_c = _block_mean(
                        np.broadcast_to(
                            ds[lonname].values[None, :].astype(float),
                            (ds.sizes[latname], ds.sizes[lonname]),
                        ),
                        coarsen,
                    )
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

    missing = np.isnan(flat)
    keep = missing.mean(axis=(1, 2)) <= max_missing_frac
    if not keep.any():
        raise KmaUnavailable(f"no land cells with data for {scenario}")
    flat, lat_f, lon_f = flat[keep], lat_c.ravel()[keep], lon_c.ravel()[keep]
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
