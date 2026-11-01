"""Korea municipality representative points — the official-boundary anchors for screening.

Pure, stdlib-only (the worker imports it too). The dataset is
``assets/libraries/korea_municipalities.csv``, produced by
``scripts/build_korea_municipalities.py`` from the 국가데이터처 SGIS 행정구역 경계
(data.go.kr 15129688). One row per 시도 and per SGIS 시군구 unit.

What a row **is**: a point guaranteed to lie inside the unit's official boundary
(``point_type = BOUNDARY_INTERIOR_POINT``), used to read the hazard grid cell there. What
it is **not**: a city-hall location, an asset, or an area aggregate. The result at that point
is a *representative-point assessment* — never "the whole municipality's risk"
(``docs/municipality-physical-risk.md``).

No asset value is attached to a municipality. A financial number appears only when the
caller supplies ``asset_value_usd`` for it; otherwise the engine reports hazard screening
with status ``NO_EXPOSURE_DATA`` — never ``0``.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from climaterisk.physical_risk.metrics import _REPO_ROOT, config_path

#: Column order of the CSV.
COLUMNS: tuple[str, ...] = (
    "municipality_id",
    "municipality_name",
    "municipality_level",
    "province_name",
    "office_name",
    "address",
    "latitude",
    "longitude",
    "point_type",
    "admin_code",
    "source",
    "source_layer",
    "source_version",
    "source_licence",
    "source_crs",
)

#: The point is an interior point of the official boundary polygon (today's dataset).
POINT_TYPE_BOUNDARY_INTERIOR = "BOUNDARY_INTERIOR_POINT"
#: The point is the municipality office as published by a government dataset (V0.3 POC,
#: ``official_offices``) — a second anchor, never a replacement of the boundary point.
POINT_TYPE_OFFICE = "OFFICIAL_OFFICE_POINT"
POINT_TYPES: tuple[str, ...] = (POINT_TYPE_BOUNDARY_INTERIOR, POINT_TYPE_OFFICE)

#: Levels, derived from the unit's official name suffix.
LEVELS: tuple[str, ...] = (
    "METROPOLITAN",  # 특별시 · 광역시 · 특별자치시 (시도 tier)
    "PROVINCE",  # 도 · 특별자치도 (시도 tier)
    "CITY",  # 시 (시군구 tier)
    "COUNTY",  # 군 (시군구 tier)
    "DISTRICT",  # 구 (시군구 tier)
)
#: Which levels belong to which tier.
TIER_OF_LEVEL: dict[str, str] = {
    "METROPOLITAN": "SIDO",
    "PROVINCE": "SIDO",
    "CITY": "SIGUNGU",
    "COUNTY": "SIGUNGU",
    "DISTRICT": "SIGUNGU",
}

#: The exact sentence every municipality result carries (spec §37). Do not reword.
REPRESENTATIVE_POINT_WARNING = (
    "This result represents hazard conditions at the selected municipality's representative "
    "point. It is not a municipality-wide spatial aggregation."
)
#: Map legend / point label.
REPRESENTATIVE_POINT_LABEL = "Representative point for municipality-level hazard screening."

#: Korea bounding box (incl. 이어도/독도 margins) — a coordinate outside it is a data error.
_KOREA_BBOX = (33.0, 124.5, 39.0, 131.0)  # lat_min, lon_min, lat_max, lon_max


def level_for_name(name: str, tier: str) -> str:
    """Level from the official name suffix.

    시도 tier: METROPOLITAN / PROVINCE; 시군구 tier: CITY / COUNTY / DISTRICT.
    """
    if tier == "SIDO":
        return "PROVINCE" if name.endswith("도") else "METROPOLITAN"
    if name.endswith("군"):
        return "COUNTY"
    if name.endswith("구"):
        return "DISTRICT"
    return "CITY"


@dataclass(frozen=True)
class Municipality:
    """One representative point. ``office_name`` / ``address`` are None for boundary points."""

    municipality_id: str
    municipality_name: str
    municipality_level: str
    province_name: str
    office_name: str | None
    address: str | None
    latitude: float
    longitude: float
    point_type: str
    admin_code: str
    source: str
    source_layer: str
    source_version: str
    source_licence: str
    source_crs: str

    @property
    def tier(self) -> str:
        return TIER_OF_LEVEL.get(self.municipality_level, "SIGUNGU")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "tier": self.tier}

    def as_facility(
        self, asset_value_usd: float | None = None, property_type: str | None = None
    ) -> dict[str, Any]:
        """The engine's facility dict for this point.

        Without ``asset_value_usd`` the engine reports hazard screening only
        (``NO_EXPOSURE_DATA``); it never invents a value.
        """
        return {
            "facility_id": self.municipality_id,
            "facility_name": self.municipality_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "asset_value_usd": asset_value_usd,
            "asset_value_currency": "USD",
            "property_type": property_type,
            "assessment_target": "MUNICIPALITY",
            "municipality_id": self.municipality_id,
            "municipality_name": self.municipality_name,
            "municipality_level": self.municipality_level,
            "province_name": self.province_name,
            "point_type": self.point_type,
        }


def dataset_path() -> Path:
    """``assets/libraries/korea_municipalities.csv`` (next to the physical-risk config)."""
    return config_path().parent / "korea_municipalities.csv"


def validate(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Problems with a candidate dataset — an empty list means valid.

    Checks: required columns, unique ids, numeric coordinates inside Korea, known level
    and point type, non-empty name/source/version.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(rows, start=1):
        missing = [c for c in COLUMNS if c not in r]
        if missing:
            problems.append(f"row {i}: missing columns {missing}")
            continue
        mid = str(r["municipality_id"] or "")
        if not mid:
            problems.append(f"row {i}: empty municipality_id")
        elif mid in seen:
            problems.append(f"row {i}: duplicate municipality_id {mid}")
        seen.add(mid)
        if not str(r["municipality_name"] or ""):
            problems.append(f"row {i}: empty municipality_name")
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (TypeError, ValueError):
            problems.append(f"row {i}: non-numeric coordinates")
            continue
        lat0, lon0, lat1, lon1 = _KOREA_BBOX
        if not (lat0 <= lat <= lat1 and lon0 <= lon <= lon1):
            problems.append(f"row {i} ({mid}): ({lat}, {lon}) is outside Korea")
        if r["municipality_level"] not in LEVELS:
            problems.append(f"row {i}: unknown level {r['municipality_level']!r}")
        if r["point_type"] not in POINT_TYPES:
            problems.append(f"row {i}: unknown point_type {r['point_type']!r}")
        if not str(r["source"] or "") or not str(r["source_version"] or ""):
            problems.append(f"row {i}: source / source_version must be stated")
    return problems


def read_dataset(path: str | Path | None = None) -> list[Municipality]:
    """Parse and validate the CSV; raises ``ValueError`` listing every problem."""
    p = Path(path) if path else dataset_path()
    with p.open(encoding="utf-8-sig", newline="") as fh:
        raw = list(csv.DictReader(fh))
    problems = validate(raw)
    if problems:
        raise ValueError(f"{p}: " + "; ".join(problems[:10]))
    out: list[Municipality] = []
    for r in raw:
        out.append(
            Municipality(
                municipality_id=str(r["municipality_id"]),
                municipality_name=str(r["municipality_name"]),
                municipality_level=str(r["municipality_level"]),
                province_name=str(r["province_name"] or ""),
                office_name=str(r["office_name"]) if r.get("office_name") else None,
                address=str(r["address"]) if r.get("address") else None,
                latitude=float(r["latitude"]),
                longitude=float(r["longitude"]),
                point_type=str(r["point_type"]),
                admin_code=str(r["admin_code"] or ""),
                source=str(r["source"]),
                source_layer=str(r["source_layer"] or ""),
                source_version=str(r["source_version"]),
                source_licence=str(r["source_licence"] or ""),
                source_crs=str(r["source_crs"] or ""),
            )
        )
    return out


@lru_cache(maxsize=1)
def load_municipalities() -> tuple[Municipality, ...]:
    """The bundled dataset, read once."""
    return tuple(read_dataset())


def by_id(ids: Iterable[str]) -> list[Municipality]:
    """Municipalities for the given ids, in the given order; unknown ids raise ``KeyError``."""
    index = {m.municipality_id: m for m in load_municipalities()}
    out: list[Municipality] = []
    for i in ids:
        if i not in index:
            raise KeyError(i)
        out.append(index[i])
    return out


def dataset_summary() -> dict[str, Any]:
    """Counts and provenance for the API / Excel run info."""
    ms = load_municipalities()
    levels: dict[str, int] = {}
    for m in ms:
        levels[m.municipality_level] = levels.get(m.municipality_level, 0) + 1
    return {
        "total": len(ms),
        "by_level": levels,
        "point_types": sorted({m.point_type for m in ms}),
        "source": sorted({m.source for m in ms}),
        "source_version": sorted({m.source_version for m in ms}),
        "source_licence": sorted({m.source_licence for m in ms}),
        "representative_point_definition": (
            "interior point of the official boundary polygon (shapely representative_point, "
            "computed in the source CRS, transformed to WGS84) — not a city-hall location, "
            "not an area aggregate"
        ),
        "path": str(dataset_path().relative_to(_REPO_ROOT))
        if dataset_path().is_relative_to(_REPO_ROOT)
        else str(dataset_path()),
        "warning": REPRESENTATIVE_POINT_WARNING,
    }
