# Physical Risk Assessment — user guide (no CLIMADA knowledge needed)

**A CLIMADA-based physical-risk assessment tool for portfolios of real-estate assets.** It combines global- and country-level hazards (the CLIMADA Data API global sets and their country cuts — not domestic source data) with asset values to produce probabilistic financial loss (EAL, Potential Loss) for Flood and Tropical Cyclone; Heatwave is reported as hazard exposure only, because no applicable CLIMADA Impact Function exists.

**Select your assets → Select hazards → Run → Download Excel.** That is the whole workflow.
Everything below explains what you will see; nothing here changes how the numbers are made.

## The screen (Models tab)

1. **Select assets** — tick the assets to assess. Search by name, id or type; *Select all* /
   *Clear*; the pill says "n of m assets selected". Assets come from the Map tab.
2. **Select hazards** — Flood, Tropical Cyclone, Heatwave. One line under each says what is
   modeled. Heatwave is *hazard only*: it reports how hot, not how much money.
3. **Analysis** — leave **Recommended**. The tool picks, for each hazard, the most local data
   that can actually run (today: the Korea country dataset for flood and typhoon; KMA data
   for heat). Nothing large is downloaded. **Custom** lets you add the Global reference (for
   a data comparison) or the historical baseline (for a climate-change multiplier); anything
   that cannot run is greyed out with the reason.
4. **RUN ASSESSMENT** — one batch for every asset × hazard. The line under the button counts
   the calculations; a progress line shows "n / N calculations complete".

## Reading the results

- **Physical Risk Summary** — how many asset-hazard results are High / Medium / Low, how many
  are hazard-only, how many are not available. *These counts are not a portfolio score.*
- **Asset risk matrix** — one row per asset: asset value and the level for each hazard.
  **High / Medium / Low** are computed risk levels; **Hazard only** means the hazard is known
  but no loss can be priced; **Unavailable / N/A** means nothing could be calculated (the
  reason is in the detail).
- **Asset detail** (click a row) — for each hazard: Risk, **EAL** (expected annual loss),
  **EAL / Asset value**, **Potential loss** (the 100-year loss), Data source and Impact
  function. **Why High?** shows the ratio next to the band's rule, the method chain, the
  impact function and the dataset that produced the number.
- **Financial loss: Not available** is *not* $0. A computed zero (a dry site) is shown as $0
  with status Calculated; "Not available" means the loss was not calculated, and the reason
  says why (for heat: no applicable CLIMADA impact function exists).
- **Global vs Country** appears when both were run. They use the same impact function; the
  country dataset is a spatial subset of the same Data API family, so a near-zero difference
  is expected. It is a pipeline-consistency check, not a measure of Korea localization.
- **Recent assessments** — reopen or re-download any earlier run.

## The Excel (Download Excel)

`Physical_Risk_Report_<N>_Assets_<YYYYMMDD>.xlsx`

| sheet | what it is for |
|---|---|
| Portfolio Summary | one line per asset: value, each hazard's status / risk / EAL / EAL ÷ assets / data source, counts of priced / hazard-only / not-available hazards, Overall EAL (a plain sum, not a score) |
| Asset Risk Matrix | the on-screen matrix |
| Hazard Results | every asset × hazard × data scope row with full provenance (for analysts) |
| Global vs Country | the data comparison, where both were run |
| Global vs Korea Local | kept for structure; today it says NOT_IMPLEMENTED for flood/typhoon |
| Climate Change | only when a historical baseline was run |
| Methodology | what was assessed, data sources, CLIMADA method, impact functions, EAL, potential loss, risk levels, climate multiplier, limitations, statuses |
| Run Info | scenario, year, country, readiness |

Rules you can rely on: a blank cell is *not calculated* (its status column says why);
`0` is a computed zero; currency is `$1,234`; ratios are `0.746%`; risk cells are coloured
but the word is always there.

## What the tool does not do (today)

- No Korea-local flood or typhoon dataset yet (domestic source adapters not implemented).
- Heatwave has no financial impact function — hazard only.
- No Korea-specific impact functions; published CLIMADA functions only.
- No overall portfolio risk score.
- Values are **modeled** expected losses under the selected CLIMADA hazard and impact-function
  assumptions — not observed damage, not a safety guarantee.

Command line equivalent (same calculation):

```bash
./.climada-env/bin/python scripts/physical_risk_batch.py assets.csv \
    --hazards flood,typhoon,heatwave --models recommended --output report.xlsx
```
