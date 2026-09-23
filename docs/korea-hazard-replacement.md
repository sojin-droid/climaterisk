# Korea hazard replacement — what `KOREA_LOCAL` means and what it takes to fill it

Companion to [`physical-risk-models.md`](physical-risk-models.md). That document defines
the three models; this one is about the third, `KOREA_LOCAL`, hazard by hazard: what
exists, what does not, and the contract a domestic adapter has to meet before its rows may
carry a number.

```text
KOREA_LOCAL = domestic-source calculation
            = a Korean hazard dataset  +  the same exposure  +  the same CLIMADA impact function
```

**Korea-local results are reported only when a domestic hazard dataset and compatible
adapter are actually available.** Until then the adapter exists, `load()` returns `None`,
and every `KOREA_LOCAL` row is `NOT_IMPLEMENTED` with null numbers. The UI shows
"Korea Local — Not available". Nothing is estimated to fill the gap.

## 1. What replacement does and does not mean

| | replaced | kept |
|---|---|---|
| hazard | Data API → domestic dataset | — |
| exposure | — | user portfolio (`lat`, `lon`, `asset_value_usd`) |
| impact function | — | the published CLIMADA function, unmodified |
| metrics | — | EAL = `aai_agg`, PML = `calc_freq_curve`, bands from config |

Because the impact function is held fixed, **a Korea-local row is not a "Korean
vulnerability"** — it is the international curve applied to Korean hazard data. Any future
Korea-specific impact function is a separate phase with its own calibration evidence
(`physical-risk-methodology.md` §20) and is out of scope here.

`DATA_API_COUNTRY` is **not** a Korea-local model. It is the Data API product cut to
`country_iso3alpha=KOR`. Measured 2026-09-23 for TC rcp45 2040 at a Seoul facility, the
country cut and the global set give identical EAL, PML and intensity (Δ = 0.0 %), because
one is a spatial subset of the other. Calling that "Korean data" would be a labelling
error; the platform names it for what it is.

## 2. Status by hazard

### River flood — `KoreaLocalFloodAdapter`, NOT_IMPLEMENTED

Candidate source: **환경부 홍수위험지도** (한강홍수통제소 flood-risk map). Investigated on
2026-09-23 — full findings in [`KOREA_FLOODMAP_INVESTIGATION.md`](KOREA_FLOODMAP_INVESTIGATION.md):

* **access is open** (no login; list/download API on `data.floodmap.go.kr`), but the
  licence is **공공누리 제4유형: 출처표시 + 상업적 이용금지 + 변경금지**. Converting the
  polygons into a CLIMADA hazard grid is a derivative work and a portfolio risk assessment
  is commercial use, so the adapter stays `NOT_IMPLEMENTED` until 한강홍수통제소 grants
  separate permission. Nothing is downloaded, copied or computed;
* format (from two structure samples): shapefile per 중권역 × return period, **EPSG:5186**,
  five rows per file = five **depth classes** (`SEG_CODE` N330–N334; official classes
  ≤0.5 / 0.5–1 / 1–2 / 2–5 / ≥5 m, code↔class mapping still to be confirmed), `FLDLV_FREQ`
  = return period. **No continuous depth value** — using the JRC depth-damage curve would
  require a documented class→representative-depth decision;
* return periods: national rivers 100/200/500 (+ 기왕최대, not a return period);
* the map is a **design-flood scenario under an assumed levee failure**, not a probabilistic
  hydrological hazard like the Data API's ISIMIP set — a *definition* difference the
  comparison must label as such.

What the adapter must produce once the licence question is settled:

| field | requirement |
|---|---|
| `Hazard.haz_type` | `"RF"` — same tag as the JRC curves expect |
| `units` | `"m"` (inundation depth); the JRC curve's x-axis |
| events | one event per return-period layer, `frequency` = incremental exceedance (the platform's existing `_incremental_frequency` rule in `ingest.py` for Aqueduct layers), `frequency_unit = "1/year"` |
| centroids | the map's own grid; no resampling onto the Data API 150-arcsec grid |
| `describe()` | `hazard_source = "환경부 홍수위험지도"`, `hazard_dataset` = file/version, `served_scenario` = what the map actually represents (design floods are **not** RCP scenarios — record that honestly) |
| status | `READY` only after the licence permits the use |

Then `readiness()["RF"]["KOREA_LOCAL"]` changes to `READY`, the worker cross-check test
forces the two tables to move together, and `global_vs_korea_local` becomes `available`.

### Tropical cyclone — `KoreaLocalTCAdapter`, NOT_IMPLEMENTED

No domestic wind-field or track dataset is connected. A candidate pipeline is KMA / RSMC
Tokyo best tracks → `TCTracks` → `TropCyclone.from_tracks` on a Korean grid, but that is a
*derivation* the platform would author, and its intensity semantics (1-min vs 10-min
sustained wind, in `m/s`) must match the Eberenz curves' calibration before it may be
priced with them. Until a source and that check exist the adapter returns `None`.

### Heat — `KoreaLocalHeatAdapter`, HAZARD_ONLY

The one domestic source that is connected: KMA 남한상세 (MK-PRISM v3.1 observed 2000–2019;
AR6 SSP 5ENSMN futures) through the local catalog's KOR `heatwave` / `heat_mortality`
layers. Rows carry hazard intensity and **no financial field**, because CLIMADA ships no
heat impact function and this project does not author one.

Decisions and open items from `KOREA_1KM_IMPLEMENTATION_GAP.md`:

* **S7 — decided 2026-09-23, Option C.** The `heatwave` layer is built from **TAMAX**
  (daily maximum; season p95), so its "daily Tmax" label is now true; `heat_mortality`
  stays on **TA** (daily mean, the metric its exposure-response estimates use). The two
  layers never share a variable and the heatwave layer is never approximated from TA.
  What did **not** change: no CLIMADA impact function was created, the indicative ramp
  was not re-defined, and `KOREA_LOCAL` heat rows remain `HAZARD_ONLY` with null financial
  fields. TAMAX archives: MK-PRISM 2000–2019 and AR6 SSP245/SSP585 2021–2060, in
  `~/climada/data/kma/`.
* Stored resolution is 0.05° (coarsened from 1 km by choice), so a `KOREA_LOCAL` heat row
  is 5 km, not 1 km.

## 3. Adapter contract (any hazard)

```python
class HazardAdapter(Protocol):
    hazard_type: str          # CLIMADA tag: "RF" | "TC" | "HW" | "HM"
    model_id: str             # ModelId.KOREA_LOCAL.value
    status: str               # READY | HAZARD_ONLY | NO_HAZARD_DATA | NOT_IMPLEMENTED
    def describe(self) -> HazardDescription: ...
    def load(self, bbox=None) -> Hazard | None: ...
```

Rules a new adapter is held to:

1. `describe()` is pure — no disk, no network — and states `requested_scenario` and
   `served_scenario` separately. If the domestic product is not scenario-based, say so in
   `served_scenario` rather than echoing the request.
2. `load()` returns a CLIMADA `Hazard` whose `haz_type` and `units` match the fixed impact
   function, or `None`. It never returns a fabricated or interpolated set.
3. The adapter's `status` must equal the cell in `climaterisk.physical_risk.models.READINESS`
   (`test_adapter_statuses_match_the_backend_readiness_table`).
4. The comparison against `DATA_API_COUNTRY` must have `same_impact_function = True`; a
   difference is then attributable to the data. Nothing else may change between the rows.
5. Licence and access are resolved **before** the first byte is copied.

## 4. Comparison once a domestic set exists

`compare_models(rows, "DATA_API_COUNTRY", "KOREA_LOCAL", facility_id, hazard_type)` needs
no new code — it is the same source-independent function used for global vs country. The
`global_vs_korea_local` export sheet already exists with `available = False` lines and
will fill in without a schema change. Relative change is `change_pct`, the EAL/assets
difference is `change_pp`, and neither is a climate-change multiplier.

## 5. Not done in this phase, on purpose

* No new damage or vulnerability curve, no Korea-specific impact function, no heatwave
  energy-cost function.
* No download of the flood-risk map, no TC derivation, no TAMAX wiring.
* No Korean asset-value dataset; no population-as-value proxy.
* No change to the legacy runners, the KOR catalog layers or their results.
