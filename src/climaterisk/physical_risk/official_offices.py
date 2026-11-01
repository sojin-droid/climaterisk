"""V0.3 POC — government-published official office points for the 7 metropolitan city halls.

Pure, stdlib-only (the worker imports it too). The dataset is
``assets/libraries/korea_official_offices.csv``, produced by
``scripts/build_official_offices.py`` from the source registry
``assets/libraries/official_office_sources.json`` and the raw government files under
``<DATA_ROOT>/external/municipality/source/``.

What a row **is**: the office location **as published by a government dataset** — office
name, latitude and longitude all taken from the same official row
(``coordinate_method = GOVERNMENT_PUBLISHED``). Nothing is geocoded, estimated or copied
from a map; a city whose coordinate is not published stays
``OFFICIAL_COORDINATE_NOT_AVAILABLE`` and is never replaced by its representative point.

The official office point is a **second spatial anchor** next to the V0.2
``BOUNDARY_INTERIOR_POINT`` (which stays canonical and unchanged). Neither is "more
accurate" — they are different places, and the distance between them is descriptive only.
No asset value is attached to an office; a financial number appears only when the caller
supplies ``asset_value_usd``.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from climaterisk.physical_risk.metrics import _REPO_ROOT, config_path

#: ``point_type`` of an office-anchored result row.
POINT_TYPE_OFFICIAL_OFFICE = "OFFICIAL_OFFICE_POINT"
#: Suffix that turns a municipality id into the office point's facility id.
OFFICE_FACILITY_SUFFIX = "#OFFICIAL_OFFICE"
#: The only accepted coordinate method.
COORDINATE_METHOD = "GOVERNMENT_PUBLISHED"
#: ``coordinate_crs`` when the source does not state a coordinate reference system. The CRS is
#: never inferred (not even from a successful polygon check).
CRS_UNSPECIFIED = "UNSPECIFIED"
#: Accepted coordinate formats and transformations.
COORDINATE_FORMATS: tuple[str, ...] = ("DECIMAL_DEGREES",)
TRANSFORMATION_NONE = "NONE"
#: UI wording for a source whose CRS is not stated.
CRS_NOT_SPECIFIED_LABEL = "Source CRS: Not specified"
#: How the polygon check reads the published numbers — an empirical spatial check only.
POLYGON_CHECK_BASIS = (
    "empirical check: the published decimal degrees are placed on the SGIS polygon as "
    "longitude/latitude (read as EPSG:4326 for the test only); a pass is not evidence of the "
    "source CRS"
)

#: Anchors a municipality run can request.
ANCHOR_REPRESENTATIVE = "REPRESENTATIVE_POINT"
ANCHOR_OFFICIAL_OFFICE = "OFFICIAL_OFFICE_POINT"
ANCHORS: tuple[str, ...] = (ANCHOR_REPRESENTATIVE, ANCHOR_OFFICIAL_OFFICE)

#: Office statuses.
STATUS_AVAILABLE = "AVAILABLE"
STATUS_NOT_AVAILABLE = "OFFICIAL_COORDINATE_NOT_AVAILABLE"
STATUS_AMBIGUOUS = "AMBIGUOUS_OFFICE"
STATUSES: tuple[str, ...] = (STATUS_AVAILABLE, STATUS_NOT_AVAILABLE, STATUS_AMBIGUOUS)

#: Polygon containment of the official point in the V0.2 SGIS boundary.
POLYGON_CHECKS: tuple[str, ...] = ("INSIDE", "OUTSIDE", "UNKNOWN")

#: The V0.3 POC target: exactly one primary office per metropolitan city (SGIS 2025_2Q ids).
TARGETS: dict[str, str] = {
    "SGIS-11": "서울특별시청",
    "SGIS-21": "부산광역시청",
    "SGIS-22": "대구광역시청",
    "SGIS-23": "인천광역시청",
    "SGIS-24": "광주광역시청",
    "SGIS-25": "대전광역시청",
    "SGIS-26": "울산광역시청",
}

#: Label shown where an office point cannot be selected.
NOT_AVAILABLE_LABEL = "Not available in V0.3 POC"
#: Provenance label for an accepted coordinate.
PROVENANCE_LABEL = "Government-published latitude/longitude"
#: The sentence every office-point result carries (counterpart of the V0.2 warning).
OFFICE_POINT_WARNING = (
    "This result represents hazard conditions at the selected municipality's official office "
    "point (government-published). It is not a municipality-wide spatial aggregation."
)

COLUMNS: tuple[str, ...] = (
    "municipality_id",
    "municipality_name",
    "office_name",
    "office_status",
    "address",
    "latitude",
    "longitude",
    "coordinate_method",
    "coordinate_crs",
    "coordinate_format",
    "transformation",
    "source_dataset",
    "source_dataset_id",
    "source_provider",
    "source_url",
    "source_last_modified",
    "source_licence",
    "source_row_name",
    "polygon_check",
    "distance_to_representative_km",
    "note",
)

#: Korea bounding box (same as the V0.2 dataset check).
_KOREA_BBOX = (33.0, 124.5, 39.0, 131.0)
#: Mean Earth radius (IUGG), km — for the descriptive distance only.
EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two latitude/longitude points on a sphere of mean radius.

    Descriptive only (how far apart the two anchors are); no result depends on it.

    Algorithm:
        $$d = 2R \\arcsin\\sqrt{\\sin^2\\frac{\\varphi_2-\\varphi_1}{2}
            + \\cos\\varphi_1\\cos\\varphi_2\\sin^2\\frac{\\lambda_2-\\lambda_1}{2}}$$

        ASCII: d = 2 R asin( sqrt( sin²(Δφ/2) + cos φ1 cos φ2 sin²(Δλ/2) ) )

        φ latitude [rad], λ longitude [rad], R = 6371.0088 km (IUGG mean radius), d [km].
        Error vs the ellipsoid ≤ 0.5 %.

    Args:
        lat1: Latitude of point 1 [degrees].
        lon1: Longitude of point 1 [degrees].
        lat2: Latitude of point 2 [degrees].
        lon2: Longitude of point 2 [degrees].

    Returns:
        Distance [km].
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


@dataclass(frozen=True)
class OfficialOffice:
    """One V0.3 target row. Coordinates are None unless ``office_status == AVAILABLE``."""

    municipality_id: str
    municipality_name: str
    office_name: str
    office_status: str
    address: str | None
    latitude: float | None
    longitude: float | None
    coordinate_method: str | None
    coordinate_crs: str | None
    coordinate_format: str | None
    transformation: str | None
    source_dataset: str | None
    source_dataset_id: str | None
    source_provider: str | None
    source_url: str | None
    source_last_modified: str | None
    source_licence: str | None
    source_row_name: str | None
    polygon_check: str | None
    distance_to_representative_km: float | None
    note: str

    @property
    def available(self) -> bool:
        return self.office_status == STATUS_AVAILABLE

    @property
    def facility_id(self) -> str:
        return office_facility_id(self.municipality_id)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "available": self.available, "facility_id": self.facility_id}


def office_facility_id(municipality_id: str) -> str:
    """``SGIS-21`` → ``SGIS-21#OFFICIAL_OFFICE`` (keeps the two anchors apart in results)."""
    return f"{municipality_id}{OFFICE_FACILITY_SUFFIX}"


def dataset_path() -> Path:
    """``assets/libraries/korea_official_offices.csv``."""
    return config_path().parent / "korea_official_offices.csv"


def registry_path() -> Path:
    """``assets/libraries/official_office_sources.json`` — the source registry."""
    return config_path().parent / "official_office_sources.json"


def _opt(value: Any) -> str | None:
    s = "" if value is None else str(value).strip()
    return s or None


def validate(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Problems with a candidate office dataset — an empty list means valid.

    Rules: exactly the 7 targets, once each, with the expected office names; an AVAILABLE
    row has ``GOVERNMENT_PUBLISHED`` coordinates inside Korea, a named source dataset, a
    source URL and date, a polygon check; any other status carries **no** coordinates.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(rows, start=1):
        missing = [c for c in COLUMNS if c not in r]
        if missing:
            problems.append(f"row {i}: missing columns {missing}")
            continue
        mid = str(r["municipality_id"] or "")
        if mid not in TARGETS:
            problems.append(f"row {i}: {mid!r} is not a V0.3 POC target")
            continue
        if mid in seen:
            problems.append(f"row {i}: duplicate {mid}")
        seen.add(mid)
        if str(r["office_name"]) != TARGETS[mid]:
            problems.append(f"row {i} ({mid}): office_name must be {TARGETS[mid]!r}")
        status = str(r["office_status"] or "")
        if status not in STATUSES:
            problems.append(f"row {i} ({mid}): unknown office_status {status!r}")
            continue
        has_coords = bool(_opt(r["latitude"])) or bool(_opt(r["longitude"]))
        if status != STATUS_AVAILABLE:
            if has_coords:
                problems.append(f"row {i} ({mid}): {status} must not carry coordinates")
            if not _opt(r["note"]):
                problems.append(f"row {i} ({mid}): {status} needs a note saying why")
            continue
        if str(r["coordinate_method"]) != COORDINATE_METHOD:
            problems.append(f"row {i} ({mid}): coordinate_method must be {COORDINATE_METHOD}")
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (TypeError, ValueError):
            problems.append(f"row {i} ({mid}): non-numeric coordinates")
            continue
        lat0, lon0, lat1, lon1 = _KOREA_BBOX
        if not (lat0 <= lat <= lat1 and lon0 <= lon <= lon1):
            problems.append(f"row {i} ({mid}): ({lat}, {lon}) is outside Korea")
        for col in ("source_dataset", "source_url", "source_last_modified", "coordinate_crs"):
            if not _opt(r[col]):
                problems.append(f"row {i} ({mid}): {col} must be stated")
        if str(r["coordinate_format"] or "") not in COORDINATE_FORMATS:
            problems.append(
                f"row {i} ({mid}): coordinate_format must be one of {COORDINATE_FORMATS}"
            )
        transformation = str(r["transformation"] or "")
        if not transformation:
            problems.append(f"row {i} ({mid}): transformation must be stated (NONE if none)")
        elif transformation != TRANSFORMATION_NONE and str(r["coordinate_crs"]) == CRS_UNSPECIFIED:
            problems.append(
                f"row {i} ({mid}): a transformation needs a source CRS the source documents"
            )
        if str(r["polygon_check"] or "") not in POLYGON_CHECKS:
            problems.append(f"row {i} ({mid}): polygon_check must be one of {POLYGON_CHECKS}")
    absent = sorted(set(TARGETS) - seen)
    if absent:
        problems.append(f"targets without a row: {absent}")
    return problems


def read_dataset(path: str | Path | None = None) -> list[OfficialOffice]:
    """Parse and validate the CSV; raises ``ValueError`` listing every problem."""
    p = Path(path) if path else dataset_path()
    with p.open(encoding="utf-8-sig", newline="") as fh:
        raw = list(csv.DictReader(fh))
    problems = validate(raw)
    if problems:
        raise ValueError(f"{p}: " + "; ".join(problems[:10]))
    out = []
    for r in raw:
        lat, lon, dist = (
            _opt(r["latitude"]),
            _opt(r["longitude"]),
            _opt(r["distance_to_representative_km"]),
        )
        out.append(
            OfficialOffice(
                municipality_id=str(r["municipality_id"]),
                municipality_name=str(r["municipality_name"]),
                office_name=str(r["office_name"]),
                office_status=str(r["office_status"]),
                address=_opt(r["address"]),
                latitude=float(lat) if lat else None,
                longitude=float(lon) if lon else None,
                coordinate_method=_opt(r["coordinate_method"]),
                coordinate_crs=_opt(r["coordinate_crs"]),
                coordinate_format=_opt(r["coordinate_format"]),
                transformation=_opt(r["transformation"]),
                source_dataset=_opt(r["source_dataset"]),
                source_dataset_id=_opt(r["source_dataset_id"]),
                source_provider=_opt(r["source_provider"]),
                source_url=_opt(r["source_url"]),
                source_last_modified=_opt(r["source_last_modified"]),
                source_licence=_opt(r["source_licence"]),
                source_row_name=_opt(r["source_row_name"]),
                polygon_check=_opt(r["polygon_check"]),
                distance_to_representative_km=float(dist) if dist else None,
                note=str(r["note"] or ""),
            )
        )
    order = list(TARGETS)
    return sorted(out, key=lambda o: order.index(o.municipality_id))


@lru_cache(maxsize=1)
def load_offices() -> tuple[OfficialOffice, ...]:
    """The bundled POC dataset, read once."""
    return tuple(read_dataset())


def offices_by_id() -> dict[str, OfficialOffice]:
    return {o.municipality_id: o for o in load_offices()}


def supported_ids() -> list[str]:
    """Municipality ids whose official office point can be assessed (AVAILABLE rows)."""
    return [o.municipality_id for o in load_offices() if o.available]


def office_facility(
    municipality: Any, office: OfficialOffice, asset_value_usd: float | None = None
) -> dict[str, Any]:
    """The engine's facility dict for a municipality's official office point.

    Same shape as ``Municipality.as_facility`` — the same exposure definition, the same
    user-supplied value (or none) — only the location and ``point_type`` differ.

    Raises:
        ValueError: the office has no government-published coordinate.
    """
    if not office.available or office.latitude is None or office.longitude is None:
        raise ValueError(
            f"{office.municipality_id}: official office point {NOT_AVAILABLE_LABEL.lower()} "
            f"({office.office_status})"
        )
    base = municipality.as_facility(asset_value_usd)
    return {
        **base,
        "facility_id": office.facility_id,
        "facility_name": office.office_name,
        "latitude": office.latitude,
        "longitude": office.longitude,
        "point_type": POINT_TYPE_OFFICIAL_OFFICE,
    }


def dataset_summary() -> dict[str, Any]:
    """Coverage and provenance for the API / Excel run info."""
    offices = load_offices()
    available = [o for o in offices if o.available]
    return {
        "target": "7 metropolitan city halls (V0.3 POC)",
        "targets": len(offices),
        "official_coordinates_found": len(available),
        "government_published": sum(o.coordinate_method == COORDINATE_METHOD for o in available),
        "unsupported": len(offices) - len(available),
        "supported_ids": [o.municipality_id for o in available],
        "coordinate_method": COORDINATE_METHOD,
        "coordinate_crs": sorted({o.coordinate_crs or "" for o in available}),
        "coordinate_format": sorted({o.coordinate_format or "" for o in available}),
        "transformation": sorted({o.transformation or "" for o in available}),
        "polygon_check_basis": POLYGON_CHECK_BASIS,
        "crs_not_specified_label": CRS_NOT_SPECIFIED_LABEL,
        "sources": sorted({f"{o.source_dataset} ({o.source_url})" for o in available}),
        "path": str(dataset_path().relative_to(_REPO_ROOT))
        if dataset_path().is_relative_to(_REPO_ROOT)
        else str(dataset_path()),
        "warning": OFFICE_POINT_WARNING,
        "definition": (
            "office location as published by a government dataset (name, latitude and "
            "longitude from the same official row); never geocoded, estimated or substituted "
            "by the representative point"
        ),
    }
