"""Storm surge on the 2D hydraulic model (AGENTS.md step 9B).

The Petals bathtub (:func:`climaterisk_worker.twin.coastal.surge_hazard`) fills every cell
below the peak sea level, however briefly the sea stays up. Here the same peak sea level
drives the sea boundary (row 0 of the city raster) as a short raised-cosine wave, and
:func:`climaterisk_worker.twin.hydraulics.run` moves the water over the terrain in time, so a
surge that lasts hours floods less than the bathtub. The peak depth at each asset is the CF
intensity, priced by the same engine (:func:`climaterisk_worker.twin.coastal.price_surge`).
Repo-specific method; every parameter is an assumption in the spec.
"""

from __future__ import annotations

from typing import Any

from climaterisk_worker.twin.inundation import _wave_run, asset_cells, city_raster


def coast_stage_peaks(spec: dict[str, Any]) -> Any:
    """Peak sea level (m above datum) at the coast for every synthetic typhoon track.

    Algorithm:
        $$\\eta_p = 0.1023\\,\\max(v - 26.8224,\\,0) + 1.8288 + s \\quad (v > 0),
          \\qquad \\eta_p = s \\quad (v = 0)$$
        ASCII: eta = 0.1023*max(v-26.8224,0) + 1.8288 + slr if v > 0 else slr

    This is the Xu (2010) wind-surge line that CLIMADA Petals' ``TCSurgeBathtub`` uses:
    $v$ is the largest 1-min sustained wind (m/s) that the wind hazard gives on the
    coastline (one point per grid column), $s$ the sea-level rise (m).

    Returns:
        Array of shape ``(n_tracks,)`` in :func:`typhoon.synthetic_tracks` order (m).
    """
    import numpy as np

    from climaterisk_worker.twin import coastal, typhoon
    from climaterisk_worker.twin.city import _to_latlon

    tracks, _ = typhoon.synthetic_tracks(spec)
    grid = spec["grid"]
    spacing = float(grid["spacing_m"])
    lon = np.array([_to_latlon(c * spacing, 0.0, grid)[1] for c in range(int(grid["cols"]))])
    lat = np.full(lon.size, coastal.coast_latitude(spec))
    wind = typhoon.wind_hazard(lat, lon, tracks, np.ones(tracks.size)).intensity.toarray()
    v = wind.max(axis=1)
    slr = float(spec["coast"]["sea_level_rise_m"])
    return np.where(v > 0, 0.1023 * np.fmax(v - 26.8224, 0.0) + 1.8288, 0.0) + slr


def surge_depths_2d(
    graph: Any,
    assets: list[dict[str, Any]],
    spec: dict[str, Any],
    snapshot_every_s: float | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Peak surge depth per typhoon track at every asset, from the 2D model.

    One run per track: row 0 is sea (bed ``sea_bed_m``), starting at ``sea_level_rise_m``
    and following :func:`inundation.stage_hydrograph` to the track's peak and back over
    ``surge_duration_h``; the run continues ``surge_recession_h`` more.

    Returns:
        ``(depth, diagnostics)``: depth (m), shape ``(n_tracks, n_assets)``, and per track
        ``peak_stage_m``, ``max_froude``, ``boundary_volume_m3``, ``stored_volume_m3`` and,
        if ``snapshot_every_s`` is given, ``snapshots`` (list of ``(t_s, depth_raster)``).
    """
    import numpy as np

    co = spec["coast"]
    bed, cell, k = city_raster(graph, spec)
    bed = bed.copy()
    bed[0, :] = float(co["sea_bed_m"])
    rows_a, cols_a = asset_cells(assets, k)
    base = float(co["sea_level_rise_m"])
    duration = float(co["surge_duration_h"]) * 3600.0
    t_end = duration + float(co["surge_recession_h"]) * 3600.0

    depths, diags = [], []
    for peak in coast_stage_peaks(spec):
        res, diag = _wave_run(
            bed, cell, spec, base, float(peak), duration, t_end, snapshot_every_s=snapshot_every_s
        )
        if snapshot_every_s is not None:
            diag["snapshots"] = res["snapshots"]
        depths.append(res["h_max"][rows_a, cols_a])
        diags.append(diag)
    return np.vstack(depths), diags


def surge_hazard_2d(graph: Any, assets: list[dict[str, Any]], spec: dict[str, Any]) -> Any:
    """CLIMADA surge ``Hazard`` (``CF``, m) holding the 2D peak depths, one event per track."""
    import numpy as np
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    from climaterisk_worker.twin import typhoon

    depth, _ = surge_depths_2d(graph, assets, spec)
    tracks, frequency = typhoon.synthetic_tracks(spec)
    n = depth.shape[0]
    names = [str(getattr(d, "name", "") or f"tc{i + 1}") for i, d in enumerate(tracks.data)]
    return Hazard(
        haz_type="CF",
        units="m",
        centroids=Centroids(
            lat=np.array([a["latitude"] for a in assets]),
            lon=np.array([a["longitude"] for a in assets]),
        ),
        event_id=np.arange(1, n + 1),
        event_name=names,
        date=np.zeros(n, dtype=int),
        frequency=np.asarray(frequency, dtype=float),
        frequency_unit="1/year",
        intensity=sparse.csr_matrix(depth),
        fraction=sparse.csr_matrix((depth > 0).astype(float)),
    )
