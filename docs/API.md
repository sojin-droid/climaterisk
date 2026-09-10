# HTTP API

Base path: `/api` (the Vite dev server proxies it to the backend on port 8099).

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness probe → `{status, service, version}` |
| POST | `/api/session` | Create a fresh session; returns the empty `Portfolio` (id == session id) |
| GET | `/api/session/{id}` | Return the portfolio for a session (404 if unknown) |
| PUT | `/api/session/{id}` | Replace the whole portfolio (full-model sync); returns it |
| DELETE | `/api/session/{id}` | Delete a session (204; 404 if unknown) |
| POST | `/api/session/{id}/run` | Submit a physical-risk run (spawns the CLIMADA worker; poll `GET …/run/{run_id}`) |
| GET | `/api/libraries` | All bundled libraries (sectors, perils, scenarios, impact_functions) |
| GET | `/api/libraries/{name}` | A single named library (404 if unknown) |

## Model shape

`Portfolio` = `{ id, name, depth_level, assets[], scenario, run_config }`. See
`src/climaterisk/core/entities.py` for the authoritative schema, and the OpenAPI docs at
`http://127.0.0.1:8099/docs` when the backend is running.

## Run contract

The backend writes `data/runs/<id>/request.json` (shape: `PhysicalRunRequest`, or the
`CostBenefitRequest` / `UncertaintyRequest` / `LitPopRequest` / `IngestRequest` /
`SupplyChainRequest` / `CalibrationRequest` / `ForecastRequest` / `HazardPreviewRequest`
variants selected by `mode`) and the CLIMADA worker writes `data/runs/<id>/result.json`
(`PhysicalRunOutput` etc.). All are defined in `src/climaterisk/engines/base.py`.

> The table above is **incomplete** (2026-09-07 audit): cost-benefit, uncertainty, litpop,
> hazard-preview, forecast, calibration, supplychain, ingest, finance, latest-runs, run
> detail/preview/export, transition, report, method-figure, data-fetch and hazard-catalog routes
> exist under `src/climaterisk/api/routers/`. Use the live OpenAPI page for the authoritative list.
