"""Step 9 gate (written by the reviewer before the step; agents may not edit it).

Coastal flood from typhoon storm surge, bathtub first: the synthetic city's bank (row 0
side) is treated as a sea coast, the step-7 typhoon winds drive CLIMADA Petals
``TCSurgeBathtub`` (Xu 2010 wind-surge line, Pielke & Pielke 1997 inland decay), and the
surge depth is priced by the engine as ``CF`` (JRC depth-damage, same curve as RF).

CLIMADA would read the distance to the coast from NASA's real-world raster (a download, and
meaningless for an imaginary city), so the coast is synthetic. Module under test:
``worker/climaterisk_worker/twin/coastal.py`` with:

- ``coast_latitude(spec)`` -> latitude (deg) of the straight east-west coastline,
  ``spec["coast"]["coastline_offset_m"]`` metres south of grid row 0
  (``origin_lat - offset / 111320``).
- ``coast_centroids(lat, lon, spec)`` -> a ``climada.hazard.Centroids`` (subclass allowed)
  whose ``get_dist_coast(signed=True)`` returns ``-(lat - coast_lat) * 111320`` metres
  (negative on land, CLIMADA's convention) and the absolute value when ``signed=False``.
- ``write_dem(graph, spec, path)`` -> writes a float32 GeoTIFF (EPSG:4326) of ground
  elevation (m): the city terrain from ``inundation.city_raster`` (land rows, cell centres on
  the street-grid nodes) plus at least ``spec["coast"]["sea_rows"]`` rows of sea south of
  the coast at ``spec["coast"]["sea_bed_m"]`` (negative). Returns the path.
- ``surge_hazard(assets, graph, spec, dem_path)`` -> CLIMADA ``Hazard`` with
  ``haz_type == "CF"``, ``units == "m"``: ``TCSurgeBathtub.from_tc_winds`` on the step-7
  wind field (``typhoon.synthetic_tracks``) computed on ``coast_centroids`` at the asset
  points, with ``inland_decay_rate = spec["coast"]["inland_decay_m_per_km"]`` and
  ``add_sea_level_rise = spec["coast"]["sea_level_rise_m"]``.
- ``price_surge(assets, hazard)`` -> one engine ``ResultRow`` per asset from
  ``engine.calculate(facility, hazard, "CF", model_id=..., flood_region="Asia")``.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "worker", REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("climada")
pytest.importorskip("climada_petals")
pytest.importorskip("networkx")

import numpy as np  # noqa: E402
from climaterisk_worker.twin.city import build_city  # noqa: E402
from climaterisk_worker.twin.spec import load_spec  # noqa: E402

coastal = pytest.importorskip("climaterisk_worker.twin.coastal")

M_PER_DEG = 111320.0


@pytest.fixture(scope="module")
def city() -> tuple:
    spec = load_spec()
    graph, assets = build_city(spec)
    return spec, graph, assets


def test_synthetic_coast_distance(city: tuple) -> None:
    spec, _, assets = city
    c_lat = coastal.coast_latitude(spec)
    want_lat = spec["grid"]["origin_lat"] - spec["coast"]["coastline_offset_m"] / M_PER_DEG
    assert c_lat == pytest.approx(want_lat, abs=1e-12)
    lat = np.array([a["latitude"] for a in assets])
    lon = np.array([a["longitude"] for a in assets])
    cent = coastal.coast_centroids(lat, lon, spec)
    signed = cent.get_dist_coast(signed=True)
    np.testing.assert_allclose(signed, -(lat - c_lat) * M_PER_DEG, rtol=1e-12)
    assert np.all(signed < 0)  # the whole city is on land
    np.testing.assert_allclose(cent.get_dist_coast(signed=False), np.abs(signed), rtol=1e-12)


def test_dem_matches_the_city_ground(city: tuple, tmp_path: Path) -> None:
    import rasterio
    from climada.util.coordinates import read_raster_sample

    spec, graph, assets = city
    path = coastal.write_dem(graph, spec, tmp_path / "dem.tif")
    with rasterio.open(path) as ds:
        assert ds.crs.to_epsg() == 4326
        assert ds.dtypes[0] == "float32"
        z = ds.read(1)
    assert (z == spec["coast"]["sea_bed_m"]).all(axis=1).sum() >= spec["coast"]["sea_rows"]
    nodes = [a for a in assets if "node" in a]
    sampled = read_raster_sample(
        str(path),
        np.array([a["latitude"] for a in nodes]),
        np.array([a["longitude"] for a in nodes]),
    )
    np.testing.assert_allclose(sampled, [a["ground_m"] for a in nodes], rtol=0, atol=1e-3)


def _hand_surge(assets: list, spec: dict, wind: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Xu (2010) line minus ground and inland decay (Petals' bathtub), clipped at 0."""
    co = spec["coast"]
    c_lat = coastal.coast_latitude(spec)
    d_km = np.array([(a["latitude"] - c_lat) * M_PER_DEG / 1000.0 for a in assets])
    slr = co["sea_level_rise_m"]
    eligible = (d_km <= 50.0) & (z >= 0.0) & (z <= 10.0 + slr)
    height = 0.1023 * np.fmax(wind - 26.8224, 0.0) + 1.8288
    surge = height - (z + co["inland_decay_m_per_km"] * d_km - slr)[None, :]
    surge = np.where((wind > 0) & eligible[None, :], np.fmax(surge, 0.0), 0.0)
    return surge


def test_surge_is_petals_bathtub_on_the_typhoon_winds(city: tuple, tmp_path: Path) -> None:
    from climada.util.coordinates import read_raster_sample
    from climaterisk_worker.twin import typhoon

    spec, graph, assets = city
    dem = coastal.write_dem(graph, spec, tmp_path / "dem.tif")
    hazard = coastal.surge_hazard(assets, graph, spec, dem)
    assert hazard.haz_type == "CF" and hazard.units == "m"
    wind = typhoon.build_tc_hazard(assets, spec).intensity.toarray()
    lat = np.array([a["latitude"] for a in assets])
    lon = np.array([a["longitude"] for a in assets])
    z = read_raster_sample(str(dem), lat, lon)
    want = _hand_surge(assets, spec, wind, z)
    np.testing.assert_allclose(hazard.intensity.toarray(), want, rtol=1e-6, atol=1e-6)
    assert want.max() > 0.5  # the city does flood
    np.testing.assert_allclose(hazard.frequency, typhoon.synthetic_tracks(spec)[1], rtol=1e-12)


def test_engine_cf_eal_equals_hand_integral(city: tuple, tmp_path: Path) -> None:
    from climaterisk_worker.physical_risk import registry

    from climaterisk.physical_risk.metrics import jrc_sector_for

    spec, graph, assets = city
    hazard = coastal.surge_hazard(
        assets, graph, spec, coastal.write_dem(graph, spec, tmp_path / "d.tif")
    )
    rows = coastal.price_surge(assets, hazard)
    depth = hazard.intensity.toarray()
    frac = hazard.fraction.toarray()
    for k, (a, row) in enumerate(zip(assets, rows, strict=True)):
        f = registry.coastal_flood_impact_function("Asia", jrc_sector_for(a["property_type"]))
        mdr = np.interp(depth[:, k], f.intensity, f.mdd) * np.interp(
            depth[:, k], f.intensity, f.paa
        )
        want = float(hazard.frequency @ (a["asset_value_usd"] * mdr * frac[:, k]))
        np.testing.assert_allclose(row.eal_usd or 0.0, want, rtol=1e-9, atol=1e-6)


def test_sea_level_rise_raises_the_loss(city: tuple, tmp_path: Path) -> None:
    spec, graph, assets = city
    dem = coastal.write_dem(graph, spec, tmp_path / "dem.tif")
    base = sum(
        r.eal_usd or 0.0
        for r in coastal.price_surge(assets, coastal.surge_hazard(assets, graph, spec, dem))
    )
    risen = copy.deepcopy(spec)
    risen["coast"]["sea_level_rise_m"] = spec["coast"]["sea_level_rise_m"] + 0.5
    more = sum(
        r.eal_usd or 0.0
        for r in coastal.price_surge(assets, coastal.surge_hazard(assets, graph, risen, dem))
    )
    assert more > base > 0.0
