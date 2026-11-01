"""Fetch real NGFS Phase 5 carbon-price trajectories and freeze them to the
bundled library (``assets/libraries/ngfs_carbon_prices.json``).

Requires ``pyam`` (``pip install pyam-iamc``) and network access to the public
IIASA NGFS Scenario Explorer (anonymous read). Re-run to refresh the snapshot.

    python scripts/fetch_ngfs.py
"""

from __future__ import annotations

import json
from pathlib import Path

import _console  # noqa: F401  (UTF-8 console; see scripts/_console.py)
from pyam.iiasa import Connection

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "assets" / "libraries" / "ngfs_carbon_prices.json"

# Our scenario id -> NGFS Phase 5 scenario name (matched by prefix to dodge unicode).
SCENARIO_PREFIX = {
    "net_zero_2050": "Net Zero 2050",
    "below_2c": "Below 2",
    "delayed_transition": "Delayed transition",
    "current_policies": "Current Policies",
}
# NGFS reference IAMs, in preference order (first one with full coverage wins).
MODEL_PREFERENCE = [
    "REMIND-MAgPIE 3.3-4.8",
    "GCAM 6.0 NGFS",
    "MESSAGEix-GLOBIOM 2.0-M-R12-NGFS",
]
YEARS = [2025, 2030, 2035, 2040, 2045, 2050]


def main() -> None:
    conn = Connection("ngfs_phase_5")
    data = conn.query(variable="Price|Carbon", region="World").data
    data = data[data["year"].isin(YEARS)]

    unit = sorted(data["unit"].unique())[0]
    model = next(
        (m for m in MODEL_PREFERENCE if (data["model"] == m).any()),
        sorted(data["model"].unique())[0],
    )
    md = data[data["model"] == model]
    scenarios = list(md["scenario"].unique())

    prices: dict[str, dict[str, float]] = {}
    for sid, prefix in SCENARIO_PREFIX.items():
        name = next((s for s in scenarios if s.startswith(prefix)), None)
        if name is None:
            print(f"WARN: no NGFS scenario for {sid} (prefix '{prefix}')")
            continue
        sub = md[md["scenario"] == name]
        prices[sid] = {
            str(int(y)): round(float(v), 2) for y, v in zip(sub["year"], sub["value"], strict=False)
        }

    out = {
        "_meta": {
            "description": "Shadow carbon-price trajectories (real NGFS Phase 5) by scenario and year.",
            "provenance": (
                f"NGFS Phase 5 (Nov 2024), model '{model}', region World, variable Price|Carbon, "
                "retrieved from the public IIASA NGFS Scenario Explorer via pyam. "
                "Frozen snapshot — re-run scripts/fetch_ngfs.py to refresh."
            ),
            "units": unit,
            "model": model,
            "source": "https://data.ene.iiasa.ac.at/ngfs/",
        },
        "prices": prices,
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} (model={model}, unit={unit})")
    for sid, series in prices.items():
        print(f"  {sid}: {series}")


if __name__ == "__main__":
    main()
