"""Country mask for cached hazards — geometry from CLIMADA, events untouched.

Needs CLIMADA (country geometries, Hazard / Centroids); skipped in the backend env. The
synthetic hazard has centroids in Seoul, Busan and off Incheon (Korea), in Fukuoka (Japan)
and in the open Yellow Sea, so the mask has something to keep and something to drop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("climada")
pytest.importorskip("shapely")

WORKER = Path(__file__).resolve().parents[1] / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from climaterisk_worker import _params, hazard_mask, ingest  # noqa: E402

LAT = np.array([37.5, 35.18, 37.45, 33.59, 36.0])  # Seoul, Busan, off Incheon, Fukuoka, sea
LON = np.array([127.0, 129.08, 126.45, 130.4, 123.0])
KOREA = [0, 1, 2]


def _hazard():  # type: ignore[no-untyped-def]
    from climada.hazard import Centroids, Hazard
    from scipy import sparse

    intensity = np.arange(1.0, 11.0).reshape(2, 5)
    return Hazard(
        haz_type="TC",
        units="m/s",
        centroids=Centroids(lat=LAT, lon=LON),
        event_id=np.array([1, 2]),
        event_name=["a", "b"],
        date=np.array([20000101, 20010101]),
        frequency=np.array([0.25, 0.75]),
        intensity=sparse.csr_matrix(intensity),
        fraction=sparse.csr_matrix(np.ones((2, 5))),
    )


def test_mask_keeps_korean_centroids_and_every_event() -> None:
    haz = _hazard()
    out, rec = hazard_mask.mask_hazard_to_country(haz, "kor", buffer_deg=0.0)
    assert rec["iso3"] == "KOR" and rec["buffer_deg"] == 0.0
    assert rec["n_centroids_before"] == 5 and rec["n_centroids_after"] == 3
    assert out.centroids.size == 3 and out.size == 2  # events never dropped
    np.testing.assert_allclose(out.centroids.lat, LAT[KOREA])
    np.testing.assert_allclose(out.centroids.lon, LON[KOREA])
    np.testing.assert_allclose(out.frequency, haz.frequency)  # frequencies untouched
    np.testing.assert_allclose(out.intensity.toarray(), haz.intensity.toarray()[:, KOREA])
    np.testing.assert_allclose(out.fraction.toarray(), np.ones((2, 3)))
    assert haz.centroids.size == 5  # the input is not modified


def test_buffer_is_a_superset_of_the_strict_polygon_and_the_default_is_configuration() -> None:
    haz = _hazard()
    strict = hazard_mask.centroid_mask(haz, hazard_mask.country_geometry("KOR", 0.0))
    wide = hazard_mask.centroid_mask(haz, hazard_mask.country_geometry("KOR", 0.5))
    assert strict.sum() <= wide.sum() and np.all(wide[strict])
    assert not wide[4]  # the open-sea point stays out even with half a degree
    _, rec = hazard_mask.mask_hazard_to_country(haz, "KOR")
    assert rec["buffer_deg"] == _params.MASK_BUFFER_DEG
    with pytest.raises(ValueError, match=">= 0"):
        hazard_mask.country_geometry("KOR", -1.0)


def test_an_empty_mask_is_an_error_not_an_empty_hazard() -> None:
    with pytest.raises(ValueError, match="no centroid"):
        hazard_mask.mask_hazard_to_country(_hazard(), "ISL")  # Iceland: nothing nearby


def test_dataapi_ingest_masks_a_global_fallback_to_the_country(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A global set cached under KOR loses the Japanese and sea centroids; opt-out keeps them."""
    monkeypatch.setenv("CLIMATERISK_HAZARD_DB", str(tmp_path))
    from climada.hazard import Hazard

    calls: list[dict[str, str]] = []

    class FakeClient:
        def get_hazard(self, data_type: str, properties: dict[str, str]):  # type: ignore[no-untyped-def]
            calls.append(dict(properties))
            if properties.get("spatial_coverage") == "country":
                raise RuntimeError("no country product for this one")
            return _hazard()

    monkeypatch.setattr("climada.util.api_client.Client", FakeClient)
    request = {
        "points": [[37.5, 127.0]],
        "peril": "tropical_cyclone",
        "scenario": "rcp45",
        "year": 2050,
    }
    entry = ingest.ingest_dataapi(request)
    assert entry["spatial_coverage"] == "global" and entry["region"] == "KOR"
    assert entry["mask"]["n_centroids_after"] == 3 and entry["n_centroids"] == 3
    assert "masked to KOR land" in entry["source"]
    cached = Hazard.from_hdf5(str(tmp_path / entry["file"]))
    assert cached.centroids.size == 3 and cached.size == 2
    # opt out, or a strict buffer, through the request
    entry2 = ingest.ingest_dataapi({**request, "mask_country": False})
    assert "mask" not in entry2 and entry2["n_centroids"] == 5
    entry3 = ingest.ingest_dataapi({**request, "mask_buffer_deg": 0.0})
    assert entry3["mask"]["buffer_deg"] == 0.0
