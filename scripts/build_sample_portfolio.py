#!/usr/bin/env python
"""Build the synthetic 1,000-asset Korea test portfolio, balanced across three asset classes.

Every asset is **synthetic**: a random point inside an official SGIS 시군구 boundary, a
random property type of its class and a random value. The portfolio exists so the
portfolio / asset-class / 시도 loss curves can be read on a sample where every class has
hundreds of assets — the 5-asset validation fixture is a set of golden regression cases
(one industrial asset) and must not be reshaped for that.

Everything that shapes the sample lives in ``assets/libraries/sample_portfolio_spec.json``
(seed, size, class property types, value distribution, boundary version); this script
holds no tuning number. Same spec + same boundary files ⇒ byte-identical CSV.

Algorithm:
    $$n_c = \\lfloor N/C \\rfloor + [\\,c < N \\bmod C\\,], \\qquad
      u \\sim \\mathcal{U}\\{1..U\\}, \\quad p \\sim \\mathcal{U}(\\mathrm{polygon}_u),$$
    $$V = \\mathrm{clip}\\big(m_c\\, e^{\\sigma_c Z},\\ V_{min},\\ V_{max}\\big),
      \\quad Z \\sim \\mathcal{N}(0,1)$$
    ASCII: n per class = N // C (+1 for the first N % C classes); unit u uniform over the
    U 시군구; point p uniform inside u's polygon (rejection sampling in EPSG:5179);
    value V = median_c * exp(sigma_c * Z), clipped to [V_min, V_max], rounded.

    ``N``: assets; ``C``: classes; ``m_c``: class median value (USD); ``σ_c``: log-sd of
    value (dimensionless); ``V_min``/``V_max``: clip bounds (USD).

Runs in the CLIMADA worker environment (needs geopandas)::

    ./.climada-env/bin/python scripts/build_sample_portfolio.py \\
        [--source <DATA_ROOT>/external/municipality/sgis] \\
        [--output tests/fixtures/sample_portfolio_1000/portfolio.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from climaterisk import paths  # noqa: E402

SPEC_PATH = REPO / "assets" / "libraries" / "sample_portfolio_spec.json"
DEFAULT_OUTPUT = REPO / "tests" / "fixtures" / "sample_portfolio_1000" / "portfolio.csv"
COLUMNS: tuple[str, ...] = (
    "facility_id",
    "facility_name",
    "latitude",
    "longitude",
    "asset_value_usd",
    "asset_value_currency",
    "property_type",
    "asset_class",
    "province_name",
    "municipality_name",
    "municipality_id",
)


def load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    """The sample specification (seed, size, classes, value distribution)."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def asset_classes(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The class entries of the spec (keys starting with ``_`` are notes, not classes)."""
    return {k: v for k, v in spec["asset_classes"].items() if not k.startswith("_")}


def class_counts(n_assets: int, classes: list[str]) -> dict[str, int]:
    """Split ``n_assets`` across ``classes`` as evenly as possible (first classes get +1)."""
    base, extra = divmod(n_assets, len(classes))
    return {c: base + (1 if i < extra else 0) for i, c in enumerate(classes)}


def _random_point_in(polygon: Any, rng: Any, max_tries: int = 10_000) -> Any:
    """A point uniform inside ``polygon`` (rejection sampling over its bounding box)."""
    from shapely.geometry import Point
    from shapely.prepared import prep

    minx, miny, maxx, maxy = polygon.bounds
    target = prep(polygon)
    for _ in range(max_tries):
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if target.contains(p):
            return p
    raise SystemExit("rejection sampling found no interior point — polygon is degenerate")


def build(spec: dict[str, Any], source_dir: Path) -> list[dict[str, Any]]:
    """The portfolio rows, deterministic in ``spec['seed']``."""
    import geopandas as gpd
    import numpy as np

    version = str(spec["boundary_version"])
    sido = gpd.read_file(source_dir / f"bnd_sido_00_{version}.shp", encoding="utf-8")
    units = gpd.read_file(source_dir / f"bnd_sigungu_00_{version}.shp", encoding="utf-8")
    if units.crs is None or sido.crs is None:
        raise SystemExit("boundary files carry no CRS — refusing to guess one")
    units = units.sort_values("SIGUNGU_CD").reset_index(drop=True)
    province_by_code = {str(r.SIDO_CD): str(r.SIDO_NM) for r in sido.itertuples()}

    rng = np.random.default_rng(int(spec["seed"]))
    spec_classes = asset_classes(spec)
    classes = list(spec_classes)
    counts = class_counts(int(spec["n_assets"]), classes)
    labels = np.array([c for c in classes for _ in range(counts[c])])
    rng.shuffle(labels)

    picks = rng.integers(0, len(units), size=labels.size)
    points = [_random_point_in(units.geometry.iloc[int(u)], rng) for u in picks]
    wgs = gpd.GeoSeries(points, crs=units.crs).to_crs(4326)

    lo, hi = float(spec["value_usd_min"]), float(spec["value_usd_max"])
    step = float(spec["value_rounding_usd"])
    rows: list[dict[str, Any]] = []
    for i, (cls, u) in enumerate(zip(labels, picks, strict=True)):
        c = spec_classes[str(cls)]
        ptype = str(rng.choice(c["property_types"]))
        raw = float(c["value_usd_median"]) * float(
            np.exp(c["value_log_sigma"] * rng.standard_normal())
        )
        value = round(min(max(raw, lo), hi) / step) * step
        unit = units.iloc[int(u)]
        code = str(unit.SIGUNGU_CD)
        rows.append(
            {
                "facility_id": f"SYN-{i + 1:04d}",
                "facility_name": f"Synthetic {ptype} {i + 1:04d}",
                "latitude": round(float(wgs.iloc[i].y), 6),
                "longitude": round(float(wgs.iloc[i].x), 6),
                "asset_value_usd": int(value),
                "asset_value_currency": "USD",
                "property_type": ptype,
                "asset_class": str(cls),
                "province_name": province_by_code.get(code[:2], ""),
                "municipality_name": str(unit.SIGUNGU_NM),
                "municipality_id": f"SGIS-{code}",
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--source",
        default=str(paths.municipality_sgis_dir()),
        help="directory of the bnd_*.shp files (default: <DATA_ROOT>/external/municipality/sgis)",
    )
    ap.add_argument("--spec", default=str(SPEC_PATH))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = ap.parse_args(argv)

    spec = load_spec(Path(args.spec))
    rows = build(spec, Path(args.source))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["asset_class"]] = by_class.get(r["asset_class"], 0) + 1
    print(f"wrote {len(rows)} synthetic assets to {out} — {by_class}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
