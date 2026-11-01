"""Offline readiness — what runs without a network, and what will not.

EIOPA's CLIMADA-App ships an offline mode: cached Data API metadata, bundled map tiles, and
uploaded exposure / hazard files. This platform cannot bundle the OpenStreetMap tiles (their
usage policy forbids it) and does not try; instead it says, precisely, which features are
online-only and what each does when ``CLIMATERISK_OFFLINE=1``, and which perils already
have a local hazard layer and therefore run without the Data API.

Everything here is a reading of local state — file names, the catalog manifest, the cached
Data API index — never a network call.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from climaterisk import paths
from climaterisk.config import get_settings
from climaterisk.data.hazard_catalog import read_catalog

#: Where the worker's ``dataapi_index`` job writes the cached Data API dataset listing.
DATAAPI_INDEX_NAME = "dataapi_index.json"

#: Data API types the index job lists by default (the ones the runners fetch).
DATAAPI_INDEX_TYPES: tuple[str, ...] = (
    "tropical_cyclone",
    "river_flood",
    "wildfire",
    "earthquake",
    "storm_europe",
)

#: Features that need the network, with their offline behaviour. Words only.
ONLINE_ONLY: tuple[dict[str, str], ...] = (
    {
        "feature": "Basemap tiles (OpenStreetMap)",
        "offline": "Maps draw assets, municipalities and results on a plain background; "
        "OSM's tile usage policy does not allow bundling tiles.",
    },
    {
        "feature": "Place search (OSM Nominatim)",
        "offline": "Disabled; place assets by clicking the map or import a coordinates file.",
    },
    {
        "feature": "Data tab → Download here",
        "offline": "Refused with a message; copy the files in from an online machine "
        "(docs/USER_GUIDE.md, Offline section).",
    },
    {
        "feature": "CLIMADA Data API in the worker",
        "offline": "Never contacted. Datasets fetched on an earlier online run are read from "
        "CLIMADA's own download cache (same files, same numbers); anything never fetched "
        "fails fast with 'fetch it once while online' instead of a network timeout.",
    },
    {
        "feature": "Fetch & ingest (Data tab), Data API index refresh",
        "offline": "Refused; run them while online, the results are files in the data root.",
    },
)


def dataapi_index_path() -> Path:
    return paths.cache_dir(DATAAPI_INDEX_NAME)


def read_dataapi_index() -> dict[str, Any]:
    """The cached Data API listing: ``{present, path, fetched_at, age_days, counts}``."""
    path = dataapi_index_path()
    if not path.is_file():
        return {
            "present": False,
            "path": str(path),
            "fetched_at": None,
            "age_days": None,
            "counts": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "present": False,
            "path": str(path),
            "fetched_at": None,
            "age_days": None,
            "counts": {},
            "error": f"{type(exc).__name__}: {exc}",
        }
    fetched = data.get("fetched_at")
    age: float | None = None
    if fetched:
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(fetched)).total_seconds() / 86400.0
        except ValueError:
            age = None
    counts: dict[str, int] = {}
    for d in data.get("datasets", []):
        counts[str(d.get("data_type"))] = counts.get(str(d.get("data_type")), 0) + 1
    return {
        "present": True,
        "path": str(path),
        "fetched_at": fetched,
        "age_days": round(age, 1) if age is not None else None,
        "counts": counts,
        "n_datasets": len(data.get("datasets", [])),
        "data_types": list(data.get("data_types", [])),
    }


def _local_files() -> list[dict[str, Any]]:
    """Presence of the drop-in files runs read without the network."""
    settings = get_settings()
    climada_dir = paths.climada_data_dir()
    worldpop = sorted(p.name for p in climada_dir.glob("*_ppp_2020_1km_Aggregated.tif"))
    eobs = sorted(p.name for p in climada_dir.glob("tg_ens_mean_*deg_reg_*.nc"))
    gpw = sorted(p.name for p in climada_dir.glob("gpw_v4_population_count*.tif"))
    natural_earth = settings.data_path / "downloads" / "ne_110m_admin_0_countries.geojson"
    kma = paths.external_dir("kma")
    return [
        {
            "name": "Natural Earth 110m countries (clip layer for country grids)",
            "path": str(natural_earth),
            "present": natural_earth.is_file(),
        },
        {
            "name": "WorldPop 1 km population rasters (population exposure)",
            "path": str(climada_dir),
            "present": bool(worldpop),
            "files": worldpop,
        },
        {
            "name": "E-OBS daily mean temperature (European heat perils)",
            "path": str(climada_dir),
            "present": bool(eobs),
            "files": eobs,
        },
        {
            "name": "GPW v4 population (LitPop; login-gated download)",
            "path": str(climada_dir),
            "present": bool(gpw),
            "files": gpw,
        },
        {
            "name": "KMA 남한상세 archives (Korean heat perils)",
            "path": str(kma),
            "present": kma.is_dir() and any(kma.glob("*.tar.gz")),
        },
    ]


def offline_status() -> dict[str, Any]:
    """Everything the Data tab's offline card shows, as one JSON-serialisable dict."""
    settings = get_settings()
    catalog = read_catalog()
    entries = list(catalog["entries"])
    by_peril: dict[str, dict[str, Any]] = {}
    for e in entries:
        peril = str(e.get("peril"))
        slot = by_peril.setdefault(peril, {"layers": 0, "regions": set(), "scenarios": set()})
        slot["layers"] += 1
        slot["regions"].add(str(e.get("region")))
        slot["scenarios"].add(str(e.get("climate_scenario")))
    perils = {
        p: {
            "layers": v["layers"],
            "regions": sorted(v["regions"]),
            "scenarios": sorted(v["scenarios"]),
        }
        for p, v in sorted(by_peril.items())
    }
    return {
        "offline": settings.offline,
        "env_var": "CLIMATERISK_OFFLINE",
        "catalog": {
            "dir": catalog["dir"],
            "n_layers": len(entries),
            "perils": perils,
            "note": "Results-tab runners read this catalog first. The Models tab reads the "
            "CLIMADA Data API datasets themselves, from CLIMADA's download cache when "
            "offline — run each analysis once while online to fill it.",
        },
        "dataapi_index": read_dataapi_index(),
        "local_files": _local_files(),
        "online_only": list(ONLINE_ONLY),
        "how_to_prepare": (
            "While online: ingest the hazards you need (Data tab → Fetch & ingest, or "
            "scripts/build_hazard.py cache/ingest), download the drop-ins listed above, and "
            "refresh the Data API index. Then set CLIMATERISK_OFFLINE=1 (or copy the data "
            "root to the offline machine — docs/DATA_LAYOUT.md)."
        ),
    }
