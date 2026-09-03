"""E-OBS observed daily temperature — the real gridded climate behind the heat perils.

E-OBS (ECA&D / Copernicus) is the European daily observational gridded dataset. This module
loads its **daily maximum temperature** (``tx``) field and turns it into the arrays the
heat-health model consumes, replacing the synthetic season generator with observations:

* real spatial structure (no interpolation from a handful of reference cities),
* real interannual variability — including the 2003 and 2022 European heatwaves,
* real **calendar years**, so a hazard event set can be labelled with the year it happened.

Data
----
Open access, no login: the ECA&D S3 mirror serves the whole-domain NetCDF.
``tx_ens_mean_0.25deg_reg_v<version>.nc`` (~0.8 GB) is the 0.25° regular-grid ensemble mean.
Drop it under ``~/climada/data/`` (or point ``CLIMATERISK_EOBS_TX`` at it) and every heat
run picks it up. The 0.1° product works too and is resolved by the same glob.

Why the file is not subset server-side: the KNMI OPeNDAP endpoint is not reliably
reachable, so the reference dataset is downloaded once and subset locally — the same
drop-in pattern the platform already uses for WorldPop and GPW.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_HOME_CLIMADA = Path.home() / "climada" / "data"

# Jun 1 - Sep 30 warm season. Always 122 days, leap year or not (30+31+31+30).
SEASON_START = (6, 1)
SEASON_END = (9, 30)
SEASON_DAYS = 122


class EobsUnavailable(Exception):
    """Raised when no E-OBS file is present; carries actionable download help."""

    HELP = (
        "No E-OBS daily-Tmax NetCDF found. Download the open ECA&D ensemble-mean file "
        "(no login) into ~/climada/data/, e.g.\n"
        "  curl -sSfL -o ~/climada/data/tx_ens_mean_0.25deg_reg_v31.0e.nc \\\n"
        "    https://knmi-ecad-assets-prd.s3.amazonaws.com/ensembles/data/"
        "Grid_0.25deg_reg_ensemble/tx_ens_mean_0.25deg_reg_v31.0e.nc\n"
        "or set CLIMATERISK_EOBS_TX to an existing file."
    )

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.HELP
        super().__init__(self.detail)


def resolve_tx_file() -> Path | None:
    """Locate the E-OBS daily-Tmax NetCDF, or None.

    Resolution order: ``CLIMATERISK_EOBS_TX`` → the newest ``tx_ens_mean_*deg_reg_*.nc``
    under ``~/climada/data`` (so a fresher version wins automatically).
    """
    explicit = os.environ.get("CLIMATERISK_EOBS_TX")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    if not _HOME_CLIMADA.is_dir():
        return None
    candidates = sorted(_HOME_CLIMADA.glob("tx_ens_mean_*deg_reg_*.nc"))
    return candidates[-1] if candidates else None


def available() -> bool:
    """True when an E-OBS Tmax file is present (callers fall back to the synthetic model)."""
    return resolve_tx_file() is not None


@dataclass(frozen=True)
class SummerTmax:
    """Observed summer daily-Tmax for a set of land grid cells.

    Attributes:
        lat: Cell latitudes, shape ``(n_cells,)``, degrees north.
        lon: Cell longitudes, shape ``(n_cells,)``, degrees east.
        years: Calendar years of each season, shape ``(n_years,)``.
        tmax: Daily maximum temperature, shape ``(n_cells, n_years, SEASON_DAYS)``, degC.
        source: Provenance string for the hazard/catalog entry.
    """

    lat: np.ndarray
    lon: np.ndarray
    years: np.ndarray
    tmax: np.ndarray
    source: str

    @property
    def n_cells(self) -> int:
        return int(self.lat.size)

    @property
    def n_years(self) -> int:
        return int(self.years.size)


def load_summer_tmax(
    bbox: tuple[float, float, float, float],
    year_start: int = 1980,
    year_end: int | None = None,
    max_missing_frac: float = 0.02,
) -> SummerTmax:
    """Load observed Jun-Sep daily Tmax for a bounding box and year range.

    Cells that are sea or have too many gaps are dropped, and the remaining gaps are
    filled per cell-season by that season's mean — heat-load integrals must not be biased
    low by a handful of missing days.

    Args:
        bbox: ``(lon_min, lon_max, lat_min, lat_max)`` in degrees.
        year_start: First season to include.
        year_end: Last season to include (default: the last complete season in the file).
        max_missing_frac: Drop a cell if more than this fraction of its season days are
            missing across the whole record.

    Returns:
        A :class:`SummerTmax`.

    Raises:
        EobsUnavailable: when no E-OBS file is present or the box contains no land cells.
    """
    import xarray as xr

    path = resolve_tx_file()
    if path is None:
        raise EobsUnavailable()

    lon_min, lon_max, lat_min, lat_max = bbox
    with xr.open_dataset(path, chunks={"time": 365}) as ds:
        var = "tx" if "tx" in ds.data_vars else next(iter(ds.data_vars))
        da = ds[var].sel(
            longitude=slice(lon_min, lon_max),
            latitude=slice(lat_min, lat_max),
        )
        # Warm season only, whole seasons only.
        months = da["time"].dt.month
        da = da.sel(time=(months >= SEASON_START[0]) & (months <= SEASON_END[0]))
        yrs = np.unique(da["time"].dt.year.values)
        last = int(yrs.max()) if year_end is None else int(year_end)
        keep_years = [int(y) for y in yrs if year_start <= int(y) <= last]
        counts = {int(y): int((da["time"].dt.year.values == y).sum()) for y in keep_years}
        keep_years = [y for y in keep_years if counts[y] == SEASON_DAYS]
        if not keep_years:
            raise EobsUnavailable(
                f"E-OBS file {path.name} has no complete Jun-Sep season in {year_start}..{last}"
            )
        da = da.sel(time=np.isin(da["time"].dt.year.values, keep_years)).load()

    # (time, lat, lon) -> (cell, year, day)
    n_y = len(keep_years)
    arr = da.values.reshape(n_y, SEASON_DAYS, da.sizes["latitude"], da.sizes["longitude"])
    arr = np.moveaxis(arr, (0, 1), (-2, -1))  # (lat, lon, year, day)
    lat2d, lon2d = np.meshgrid(da["latitude"].values, da["longitude"].values, indexing="ij")
    flat = arr.reshape(-1, n_y, SEASON_DAYS)

    missing = np.isnan(flat)
    keep = missing.mean(axis=(1, 2)) <= max_missing_frac
    if not keep.any():
        raise EobsUnavailable(f"no land cells with data in bbox {bbox} (E-OBS {path.name})")
    flat, lat_f, lon_f = flat[keep], lat2d.ravel()[keep], lon2d.ravel()[keep]

    # Fill residual gaps with each cell-season's own mean (never with 0 degC).
    if np.isnan(flat).any():
        season_mean = np.nanmean(flat, axis=2, keepdims=True)
        season_mean = np.where(np.isnan(season_mean), np.nanmean(flat), season_mean)
        flat = np.where(np.isnan(flat), season_mean, flat)

    return SummerTmax(
        lat=lat_f.astype(float),
        lon=lon_f.astype(float),
        years=np.array(keep_years, dtype=int),
        tmax=flat.astype(float),
        source=f"E-OBS observed daily Tmax ({path.name}, {keep_years[0]}-{keep_years[-1]})",
    )
