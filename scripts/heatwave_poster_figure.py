#!/usr/bin/env python
"""Poster figure for the Spain heat-mortality analysis (PLANiT brand).

Four panels, reading order:
    (1) WHERE — map of per-capita heat mortality (colour) and population (size)
    (2) ADAPTATION — each province's minimum-mortality comfort band (hazard side)
    (3) AGEING — deaths per 100k against the share of population aged 65+ (vulnerability side)
    (4) FUTURE — the increase to 2050 split into warming, ageing and their amplification

Reads ``data/heatwave_europe/results.json`` written by ``heatwave_europe.py`` and writes a
PNG. Run in the worker env (needs cartopy)::

    ./.climada-env/bin/python scripts/heatwave_poster_figure.py --out data/heatwave_europe/poster_figure.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "worker"))

NAVY = "#0C356A"
BLUE = "#0174BE"
YELLOW = "#FFC436"
GREY = "#8D8D8D"
# Heat is RED on this poster — the same reds as the original heatwave_europe figure
# (matplotlib "Reds" ramp, #d62728 bands, #b03a2e highlights); blue means cold/neutral.
HEAT = "#d62728"
HEAT_DARK = "#b03a2e"
POINT_GREY = "#9aa4b0"
POINT_GREY_EDGE = "#5b6570"
HEAT_EDGE = "#5b1a12"
ORANGE = "#EC8305"  # PLANiT brand orange — panels 2 and 4 (bands, age split); 1 and 3 stay red
LIGHT = "#D3D3D3"
INK = "#000000"
MUTED = "#666666"


def _style() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": ["Helvetica Neue", "Arial", "DejaVu Sans"],
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.titlecolor": NAVY,
            "axes.labelsize": 11,
            "axes.labelcolor": INK,
            "axes.edgecolor": LIGHT,
            "axes.linewidth": 1.0,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "grid.color": LIGHT,
            "grid.linewidth": 0.8,
            "legend.frameon": False,
            "legend.fontsize": 10,
        }
    )


def _despine(ax) -> None:  # type: ignore[no-untyped-def]
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def make_poster_figure(results: dict, decomposition: dict, out_path: Path) -> dict[str, float]:  # type: ignore[type-arg]
    _style()
    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt
    from heatwave_europe import _draw_basemap

    rows = results["per_province"]
    fig = plt.figure(figsize=(14, 10.6))
    gs = fig.add_gridspec(
        2, 2, left=0.06, right=0.98, bottom=0.07, top=0.95, wspace=0.28, hspace=0.42
    )

    # (1) Map — sequential single hue (pale → navy), size = population
    ax = fig.add_subplot(gs[0, 0], projection=ccrs.PlateCarree())
    _draw_basemap(ax, rows)
    cmap = "Reds"
    pop_max = max(r["population"] for r in rows)
    sizes = [60 + 700 * (r["population"] / pop_max) for r in rows]
    sc = ax.scatter(
        [r["lon"] for r in rows],
        [r["lat"] for r in rows],
        c=[r["eai_per_100k"] for r in rows],
        s=sizes,
        cmap=cmap,
        edgecolor="#3f3f3f",
        linewidth=0.8,
        transform=ccrs.PlateCarree(),
        zorder=5,
    )
    cb = fig.colorbar(sc, ax=ax, fraction=0.04, pad=0.03)
    cb.set_label(
        "Heat deaths per 100,000 per year", fontsize=10, color=HEAT_DARK, fontweight="bold"
    )
    cb.ax.tick_params(labelsize=9, colors=MUTED)
    cb.outline.set_edgecolor(LIGHT)
    label = {
        q["province"]: q
        for q in sorted(rows, key=lambda r: -r["eai_per_100k"])[:4]
        + sorted(rows, key=lambda r: -r["population"])[:3]
        + sorted(rows, key=lambda r: r["eai_per_100k"])[:2]
    }
    map_off = {
        "Zamora": (-52, 6),
        "Valladolid": (8, 8),
        "Toledo": (8, -12),
        "Madrid": (12, 6),
        "Sevilla": (-48, 8),
        "Malaga": (8, -12),
    }
    for r in label.values():
        ax.annotate(
            r["province"],
            xy=(r["lon"], r["lat"]),
            xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
            fontsize=9,
            color=INK,
            xytext=map_off.get(r["province"], (7, 5)),
            textcoords="offset points",
            zorder=6,
        )
    ax.set_title(
        "A  Where: per-capita heat mortality\n(colour = deaths per 100k, size = population)",
        loc="left",
    )

    # (2) Comfort bands — one hue, sorted by heat-onset temperature
    ax = fig.add_subplot(gs[0, 1])
    prov = sorted(rows, key=lambda r: r["comfort_high"])
    for i, r in enumerate(prov):
        ax.plot(
            [r["comfort_low"], r["comfort_high"]],
            [i, i],
            color=ORANGE,
            lw=6,
            solid_capstyle="round",
        )
        ax.plot([r["tmax_jja_mean"]], [i], marker="|", color="#333333", ms=14, mew=2.5, zorder=6)
    ax.set_yticks(range(len(prov)))
    ax.set_yticklabels([r["province"] for r in prov], fontsize=9)
    ax.set_xlabel("Daily maximum temperature (°C)")
    ax.grid(axis="x", alpha=0.6)
    ax.set_axisbelow(True)
    _despine(ax)
    ax.plot([], [], color=ORANGE, lw=6, label="minimum-mortality comfort band")
    ax.plot(
        [], [], marker="|", color="#333333", ms=12, mew=2.5, ls="none", label="mean summer Tmax"
    )
    ax.legend(loc="lower right", fontsize=9)
    ax.set_title(
        "B  Adaptation: heat deaths start above each province's own comfort band\n(hotter climate → higher, wider band)",
        loc="left",
    )

    # (3) Ageing — share 65+ vs per-capita deaths
    ax = fig.add_subplot(gs[1, 0])
    x = np.array([r["share_over65"] * 100 for r in rows])
    y = np.array([r["eai_per_100k"] for r in rows])
    r_age = float(np.corrcoef(x, y)[0, 1])
    sc_off = {
        "Zamora": (-54, -4),
        "Valladolid": (10, 4),
        "Madrid": (-58, -22),
        "Barcelona": (-78, 14),
        "Murcia": (-2, 16),
        "Malaga": (14, 10),
        "Sevilla": (14, -14),
    }
    hi = np.array([r["province"] in sc_off for r in rows])
    sz = np.array(sizes)
    ax.scatter(
        x[~hi], y[~hi], s=sz[~hi], c=POINT_GREY, edgecolor=POINT_GREY_EDGE, linewidth=0.8, zorder=4
    )
    ax.scatter(x[hi], y[hi], s=sz[hi], c=HEAT_DARK, edgecolor=HEAT_EDGE, linewidth=0.9, zorder=5)
    b, a = np.polyfit(x, y, 1)
    xx = np.linspace(x.min() - 0.5, x.max() + 0.5, 50)
    ax.plot(xx, a + b * xx, color=HEAT_DARK, lw=1.6, ls="--", alpha=0.85, zorder=3)
    for r in rows:
        if r["province"] in sc_off:
            ax.annotate(
                r["province"],
                (r["share_over65"] * 100, r["eai_per_100k"]),
                fontsize=9,
                xytext=sc_off[r["province"]],
                textcoords="offset points",
                color=INK,
            )
    ax.set_xlabel("Share of population aged 65+ (%)")
    ax.set_ylabel("Heat deaths per 100,000 per year", color=HEAT_DARK, fontweight="bold")
    ax.grid(alpha=0.6)
    ax.set_axisbelow(True)
    _despine(ax)
    ax.text(
        0.02,
        0.95,
        f"Pearson r = {r_age:+.2f}",
        transform=ax.transAxes,
        fontsize=11,
        fontweight="bold",
        color=HEAT_DARK,
        va="top",
    )
    ax.set_title(
        "C  Ageing: older provinces carry the higher per-capita risk\n(size = population)",
        loc="left",
    )

    # (4) Future — climate vs ageing decomposition (2x2 experiment, see heatwave_europe)
    ax = fig.add_subplot(gs[1, 1])
    d = decomposition
    base, clim, age, inter = (
        d["baseline"],
        d["climate_effect"],
        d["ageing_effect"],
        d["interaction"],
    )
    total = d["both_total"]
    steps = [
        ("Today\n2020 climate\n2020 people", 0.0, base, NAVY),
        (f"Warming only\n+{d['warming_c']:.1f} °C", base, clim, HEAT),
        (f"Ageing only\n{d['demography_year']} age\nstructure", base + clim, age, ORANGE),
        (
            "Extra from\nboth together\n(new elderly ×\nnew hot days)",
            base + clim + age,
            inter,
            POINT_GREY,
        ),
        (f"{d['demography_year']}\nboth", 0.0, total, NAVY),
    ]
    xs = np.arange(len(steps))
    for i, (_lab, bottom, height, col) in enumerate(steps):
        ax.bar(i, height, bottom=bottom, color=col, width=0.62, edgecolor="#FFFFFF", linewidth=1)
        top = bottom + height
        txt = f"{height:,.0f}" if i in (0, 4) else f"+{height:,.0f}"
        ax.text(
            i,
            top + total * 0.012,
            txt,
            ha="center",
            va="bottom",
            fontsize=10.5,
            fontweight="bold",
            color=INK,
        )
        if 0 < i < 4:
            ax.plot([i - 1 + 0.31, i - 0.31], [bottom, bottom], color=LIGHT, lw=1.2, zorder=1)
    ax.set_xticks(xs)
    ax.set_xticklabels([s_[0] for s_ in steps], fontsize=9, linespacing=1.15)
    ax.set_ylabel("Heat deaths per year (Spain, modelled)", color=HEAT_DARK, fontweight="bold")
    ax.set_ylim(0, total * 1.14)
    ax.grid(axis="y", alpha=0.6)
    ax.set_axisbelow(True)
    _despine(ax)
    share_age = age / (total - base)
    share_clim = clim / (total - base)
    ax.set_title(
        f"D  Future, 2020→{d['demography_year']}: warming adds {share_clim:.0%} of the increase, "
        f"ageing {share_age:.0%}, both together another {inter / (total - base):.0%}"
        "\n(same weather draws in all four runs; scenario result, not a forecast)",
        loc="left",
    )

    fig.savefig(out_path, dpi=150, facecolor="#FFFFFF")
    plt.close(fig)
    return {"r_age": r_age}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--results", type=Path, default=REPO / "data" / "heatwave_europe" / "results.json"
    )
    ap.add_argument(
        "--decomposition",
        type=Path,
        default=REPO / "data" / "heatwave_europe" / "decomposition_esp.json",
    )
    ap.add_argument(
        "--warming-c", type=float, default=1.5, help="summer Tmax warming to the target year, degC"
    )
    ap.add_argument("--demography-year", type=int, default=2050)
    ap.add_argument("--seasons", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--recompute", action="store_true")
    ap.add_argument(
        "--out", type=Path, default=REPO / "data" / "heatwave_europe" / "poster_figure.png"
    )
    args = ap.parse_args(argv)
    if args.recompute or not args.decomposition.is_file():
        from heatwave_europe import PROVINCES, decompose_climate_vs_ageing

        esp = [p for p in PROVINCES if p.country == "ESP"]
        d = decompose_climate_vs_ageing(
            esp, args.seasons, args.seed, args.warming_c, args.demography_year
        )
        args.decomposition.write_text(json.dumps(d, indent=2))
        print(
            f"decomposition: baseline {d['baseline']:,.0f} → {d['both_total']:,.0f}; "
            f"climate +{d['climate_effect']:,.0f}, ageing +{d['ageing_effect']:,.0f}, interaction +{d['interaction']:,.0f}"
        )
    decomposition = json.loads(args.decomposition.read_text())
    stats = make_poster_figure(json.loads(args.results.read_text()), decomposition, args.out)
    print(f"wrote {args.out}  r_age={stats['r_age']:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
