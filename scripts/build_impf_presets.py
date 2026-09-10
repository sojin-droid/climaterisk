"""Regenerate ``assets/libraries/impact_function_presets.json`` from CLIMADA.

These are *authentic* published impact-function presets the Vulnerability studio
offers as one-click starting points:

  - Tropical cyclone: regionally-calibrated Emanuel ``v_half`` (Eberenz et al. 2021),
    median quantile, ``v_thresh = 25.7 m/s``. Mapped onto the studio's ``tc_v_half``.
  - River flood: JRC continental depth-damage curves (Huizinga et al. 2017),
    residential sector, sampled at the studio's flood-depth breakpoints. Mapped
    onto the studio's ``flood_mdr``.

The values are baked into the committed JSON so the backend (which never imports
CLIMADA — GPL boundary) and the frontend can read them offline. Re-run in the
worker env to refresh:

    ./.climada-env/bin/python scripts/build_impf_presets.py

Run from the repo root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

# Depth breakpoints must match assets/libraries/impact_functions.json "flood_depth_m".
FLOOD_DEPTHS_M = [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
TC_V_THRESH_MS = 25.7  # Emanuel (2011) threshold used by the calibrated regional set.

# Earthquake MMI breakpoints must match impact_functions.json "eq_mmi". CLIMADA ships no
# earthquake impf, so these are indicative MMI mean-damage-ratio curves ordered by seismic
# robustness (EMS-98 / HAZUS-style vulnerability classes) — NOT a single published library.
EQ_MMI = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
EQ_CLASSES = {
    "urm": ("Unreinforced masonry (high vuln.)", [0.0, 0.08, 0.25, 0.55, 0.85, 1.0]),
    "rc_frame": ("Reinforced-concrete frame (medium)", [0.0, 0.02, 0.08, 0.25, 0.50, 0.80]),
    "wood_frame": ("Wood frame (ductile)", [0.0, 0.02, 0.06, 0.18, 0.40, 0.70]),
    "modern_code": ("Modern seismic code (low vuln.)", [0.0, 0.01, 0.03, 0.10, 0.25, 0.55]),
}

# Eberenz et al. (2021) region codes → human labels.
TC_REGION_LABELS = {
    "NA1": "Caribbean & Mexico",
    "NA2": "USA & Canada",
    "NI": "North Indian",
    "OC": "Oceania (AU/NZ/Pacific)",
    "SI": "South Indian",
    "WP1": "South East Asia",
    "WP2": "Philippines",
    "WP3": "China mainland",
    "WP4": "North West Pacific",
    "ROW": "Rest of the world",
}

# JRC continental curves (Huizinga et al. 2017), residential sector.
FLOOD_REGIONS = {
    "Africa": "Africa",
    "Asia": "Asia",
    "Europe": "Europe",
    "NorthAmerica": "North America",
    "Oceania": "Oceania",
    "SouthAmerica": "South America",
}


def _tc_countries_per_region() -> dict[str, list[str]]:
    """CLIMADA's own Eberenz region membership (ISO3 lists), keyed by region code."""
    from climada.entity.impact_funcs.trop_cyclone import get_countries_per_region

    regions, _impf_ids, countries, _names = get_countries_per_region()
    return {code: sorted(str(c) for c in countries[i]) for i, code in enumerate(regions)}


def _flood_countries_per_region() -> dict[str, list[str]]:
    """CLIMADA's own JRC region membership from ``NatRegIDs.csv`` (column ``impf_RF``)."""
    import pandas as pd
    from climada.util.constants import RIVER_FLOOD_REGIONS_CSV

    # impf_RF 1..6 = Africa, Asia, Europe, North America, Oceania, South America — the
    # ordering petals ``ImpfRiverFlood`` uses (REGION_CO_ID // 10).
    id_to_code = dict(enumerate(FLOOD_REGIONS, start=1))
    info = pd.read_csv(RIVER_FLOOD_REGIONS_CSV)
    out: dict[str, list[str]] = {code: [] for code in FLOOD_REGIONS}
    for iso, rid in zip(info["ISO"], info["impf_RF"], strict=True):
        if isinstance(iso, str) and int(rid) in id_to_code:
            out[id_to_code[int(rid)]].append(iso)
    return {k: sorted(v) for k, v in out.items()}


def _tc_presets() -> list[dict[str, Any]]:
    from climada.entity.impact_funcs.trop_cyclone import ImpfSetTropCyclone

    v_half = ImpfSetTropCyclone.calibrated_regional_vhalf(q=0.5)
    countries = _tc_countries_per_region()
    prov = f"Eberenz et al. (2021) regional calibration, v_thresh={TC_V_THRESH_MS} m/s"
    presets = [
        {
            "id": "tc_eberenz_emanuel_usa",
            "peril": "tc",
            "label": "TC — Emanuel default (USA)",
            "tc_v_half": 74.7,
            "provenance": "Emanuel (2011), ImpfTropCyclone.from_emanuel_usa default v_half",
        }
    ]
    for i, (code, label) in enumerate(TC_REGION_LABELS.items(), start=1):
        if code not in v_half:
            continue
        presets.append(
            {
                "id": f"tc_eberenz_{code.lower()}",
                "peril": "tc",
                "label": f"TC — {label} ({code})",
                "tc_v_half": round(float(v_half[code]), 1),
                "provenance": prov,
                # Geography-aware default selection (worker/climaterisk_worker/vulnerability.py)
                "region_code": code,
                "climada_impf_id": i,
                "countries": countries.get(code, []),
            }
        )
    return presets


def _flood_presets() -> list[dict[str, Any]]:
    from climada_petals.entity.impact_funcs.river_flood import ImpfRiverFlood

    countries = _flood_countries_per_region()
    presets = []
    for i, (code, label) in enumerate(FLOOD_REGIONS.items(), start=1):
        f = ImpfRiverFlood.from_jrc_region_sector(code, "residential")
        mdr = [round(float(np.interp(d, f.intensity, f.mdd * f.paa)), 3) for d in FLOOD_DEPTHS_M]
        presets.append(
            {
                "id": f"flood_jrc_{code.lower()}",
                "peril": "flood",
                "label": f"Flood — {label} (JRC residential)",
                "flood_mdr": mdr,
                "provenance": "Huizinga et al. (2017) JRC global depth-damage, residential",
                # Geography-aware default selection (worker/climaterisk_worker/vulnerability.py)
                "region_code": label,
                "climada_impf_rf_id": i,
                "countries": countries.get(code, []),
            }
        )
    return presets


def _eq_presets() -> list[dict[str, Any]]:
    return [
        {
            "id": f"eq_{key}",
            "peril": "eq",
            "label": f"EQ — {label}",
            "eq_mdr": mdr,
            "provenance": "Indicative MMI damage by construction class (EMS-98/HAZUS ordering)",
        }
        for key, (label, mdr) in EQ_CLASSES.items()
    ]


def build() -> dict[str, Any]:
    """Build the presets payload (TC + flood + earthquake)."""
    return {
        "_meta": {
            "description": (
                "Impact-function presets the Vulnerability studio applies with one click. "
                "TC sets tc_v_half (Emanuel form, v_thresh=25.7 m/s, Eberenz 2021 regional); "
                "flood sets flood_mdr (Huizinga/JRC); earthquake sets eq_mdr (indicative "
                "construction-class MMI curves — CLIMADA ships no earthquake impf)."
            ),
            "flood_depth_m": FLOOD_DEPTHS_M,
            "eq_mmi": EQ_MMI,
            "generator": "scripts/build_impf_presets.py (run in the CLIMADA worker env)",
        },
        "presets": _tc_presets() + _flood_presets() + _eq_presets(),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out = root / "assets" / "libraries" / "impact_function_presets.json"
    payload = build()
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(payload['presets'])} presets → {out}")


if __name__ == "__main__":
    main()
