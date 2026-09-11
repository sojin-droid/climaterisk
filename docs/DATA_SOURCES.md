# 데이터 소스 — 필요한 것, 링크, 접근 조건

이 프로젝트가 쓰는 모든 외부 데이터의 **단일 목록**입니다. 세 종류로 나뉩니다.

| 표시 | 뜻 |
|---|---|
| ✅ 무료 | 로그인·키 없이 받습니다. 앱의 **Data 탭**이나 스크립트가 자동으로 받는 것도 많습니다 |
| 🔑 가입 필요 | 무료지만 계정·인증키가 필요합니다 — **사람이 직접** 받아야 합니다 |
| 💰 상업 유료 | 학술·비영리는 무료, 영리 이용은 별도 라이선스 |

아래 표는 `assets/libraries/data_sources.json`에서 생성했습니다. 그 파일이 코드가 실제로
참조하는 원본이므로, 표와 코드가 어긋날 수 없습니다.

---

## 1. 지금 당장 필요한 것 (우선순위)

| # | 데이터 | 왜 필요한가 | 접근 | 어디서 |
|---|---|---|---|---|
| 1 | **기상청 남한상세 1 km — 평균기온(TA) 일자료 nc** | 국내 폭염 사망 해저드. 이것만 있으면 `heat_korea.py register` 한 줄로 끝납니다 | 🔑 기후변화 상황지도 계정 | [climate.go.kr/atlas/ana/cdd](https://climate.go.kr/atlas/ana/cdd) · 파일 ID는 [KMA_DOWNLOAD_LIST.md](KMA_DOWNLOAD_LIST.md) |
| 2 | ~~**KOSIS 연령별 조사망률**~~ ✅ 2026-09-11 반영 | 한국 기저사망률 2개를 DT_1B34E01(2024) 실측으로 교체함 — 상수로 인용·고정, 실행 시 API 호출 없음 | 🔑 KOSIS Open API 키 (자동 승인, 재조회용) | [kosis.kr/openapi](https://kosis.kr/openapi/) |
| 3 | **행안부 재해연보 지역별 자연재난 피해** | 취약성 보정 — 불확실성의 83 %가 여기서 나옴 | 🔑 공공데이터포털 인증키 | [data.go.kr 15107316](https://www.data.go.kr/data/15107316/openapi.do) · 명세는 [OBSERVED_LOSSES_KR_SPEC.md](OBSERVED_LOSSES_KR_SPEC.md) |
| 4 | GPW v4.11 인구 격자 | LitPop 노출(로그인 게이트). WorldPop으로 대체 가능 | 🔑 NASA Earthdata | [Earthdata](https://www.earthdata.nasa.gov/data/projects/gpw) |

1–3은 **사람이 로그인해야** 하는 것이고, 제가 대신 받을 수 없습니다. 그 외는 대부분 자동입니다.

## 2. 이미 확보한 것 (이 PC)

| 데이터 | 위치 | 크기 |
|---|---|---|
| E-OBS 일평균기온 `tg` | `~/climada/data/tg_ens_mean_0.25deg_reg_v31.0e.nc` | 788 MB |
| IBTrACS 태풍 트랙 | `~/climada/data/IBTrACS.ALL.v04r01.nc` | — |
| WorldPop 1 km (ESP·KOR·JPN) | `~/climada/data/*_ppp_2020_1km_Aggregated.tif` | — |
| Natural Earth 110m 경계 | `data/downloads/ne_110m_admin_0_countries.geojson` | 0.8 MB |
| 해저드 카탈로그 (KOR 태풍·홍수·산불·지진, ESP 폭염) | `data/hazard_db/` | ~50 MB |
| RSMC Tokyo best track | `~/climada/data/rsmc/bst_all.zip` | 0.7 MB |
| OSM 한국 | `~/climada/data/osm/south-korea-latest.osm.pbf` | 286 MB |
| 환경부 홍수위험지도 SHP (100/200/500년) | `~/climada/data/floodmap/` | 4.9 GB |

⚠️ 홍수위험지도는 **공공누리 제4유형**(비상업·변경금지)입니다. 내부 검증 전용이고, 상업 납품
산출물의 입력으로 쓰려면 한강홍수통제소와 협의해야 합니다.

## 3. 전체 목록 (코드가 참조하는 원본에서 생성)

### 해저드 (`hazard`)

| 데이터 | 쓰이는 곳 | 접근 | 링크 | 저장 위치 |
|---|---|---|---|---|
| **WRI Aqueduct Floods (riverine GeoTIFFs)** | River flood (real depth maps, refined to a CLIMADA hazard) | ✅ 무료 | [aqueduct](https://www.wri.org/data/aqueduct-floods-hazard-maps) | — |
| **WRI Aqueduct Floods (coastal / sea-level-rise GeoTIFFs)** | Coastal flood / SLR (real inundation maps, refined to a CLIMADA hazard) | ✅ 무료 | [aqueduct_coastal](https://www.wri.org/data/aqueduct-floods-hazard-maps) | — |
| **Copernicus DEM GLO-30 (AWS)** | DEM for TC storm surge (TCSurgeBathtub) | ✅ 무료 | [copdem](https://registry.opendata.aws/copernicus-dem/) | — |
| **CORDEX regional climate projections (CDS)** | Future heat / climate drivers | 🔑 가입 필요 | [cordex](https://cds.climate.copernicus.eu/datasets/projections-cordex-domains-single-levels) | — |
| **CLIMADA Data API — Earthquake (observed)** | Earthquake (cache locally for offline runs) | ✅ 무료 | [dataapi_eq](https://climada.ethz.ch/data-types/) | — |
| **CLIMADA Data API — River flood (ISIMIP)** | River flood (cache locally for offline runs) | ✅ 무료 | [dataapi_rf](https://climada.ethz.ch/data-types/) | — |
| **CLIMADA Data API — Tropical cyclone (synthetic)** | Tropical cyclone (cache locally for offline runs) | ✅ 무료 | [dataapi_tc](https://climada.ethz.ch/data-types/) | — |
| **CLIMADA Data API — Wildfire (historical)** | Wildfire (cache locally for offline runs) | ✅ 무료 | [dataapi_wf](https://climada.ethz.ch/data-types/) | — |
| **E-OBS daily mean temperature (ECA&D, 0.25° regular grid)** | heat_mortality + heatwave hazard over Europe (observed daily mean temperature) | ✅ 무료 | [eobs](https://www.ecad.eu/download/ensembles/download.php) | `~/climada/data/` |
| **Copernicus CDS ERA5-HEAT (UTCI)** | Heat (productivity/health, not asset damage) | 🔑 가입 필요 | [era5_heat](https://cds.climate.copernicus.eu/stac-browser/collections/derived-utci-historical) | — |
| **GloFAS reanalysis (Copernicus CEMS)** | River discharge → low-flow / river flood | 🔑 가입 필요 | [glofas](https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical?tab=overview) | — |
| **ISIMIP repository (crop & hydrology drivers)** | Future drought / crop yield / low-flow | ✅ 무료 | [isimip](https://data.isimip.org/) | — |
| **MeteoSwiss radar hail climatology (MESHS/POH)** | Hail (Europe only) | ✅ 무료 | [meteoswiss_hail](https://www.nccs.admin.ch/nccs/en/home/data-and-media-library/data/hail-climate-datasets.html) | — |
| **NASA Global Landslide Catalog (COOLR)** | Landslide (petals from_hist) | ✅ 무료 | [nasa_landslide](https://gpm.nasa.gov/landslides/data.html) | — |
| **SPEI Global Drought Monitor (CSIC)** | Drought index | ✅ 무료 | [spei](https://spei.csic.es/) | — |
| **TCRain — generate TC rainfall hazard (R-CLIPER)** | Generate a physical TC rainfall hazard (climada_petals TCRain, R-CLIPER) — replaces the indicative tc_rain ramp | ✅ 무료 | [tcrain](https://www.ncei.noaa.gov/products/international-best-track-archive) | — |
| **TCTracks — generate TC hazard from IBTrACS** | Generate your own TC wind hazard (IBTrACS historical + synthetic perturbation) | ✅ 무료 | [tctracks](https://www.ncei.noaa.gov/products/international-best-track-archive) | — |

### 노출 (`exposure`)

| 데이터 | 쓰이는 곳 | 접근 | 링크 | 저장 위치 |
|---|---|---|---|---|
| **Geofabrik OSM extracts (.osm.pbf)** | OSM exposure via climada_petals osm-flex | ✅ 무료 | [geofabrik](https://download.geofabrik.de/) | — |
| **Google Open Buildings** | Polygon exposure (Africa/Asia/LatAm) | ✅ 무료 | [google_buildings](https://sites.research.google/gr/open-buildings/) | — |
| **Microsoft Global Building Footprints** | Polygon exposure (~1.4B buildings) | ✅ 무료 | [ms_buildings](https://github.com/microsoft/GlobalMLBuildingFootprints) | — |
| **Natural Earth 110m country boundaries** | Region/national boundary reference layer | ✅ 무료 | [naturalearth](https://www.naturalearthdata.com/) | `data/downloads/` |

### 인구·자산 격자 (`litpop`)

| 데이터 | 쓰이는 곳 | 접근 | 링크 | 저장 위치 |
|---|---|---|---|---|
| **GHSL GHS-BUILT-S (built-up surface)** | Asset-value proxy raster | ✅ 무료 | [ghs_built](https://data.jrc.ec.europa.eu/dataset/9f06f36f-4b11-47ec-abb0-4f8b7b1d72ea) | — |
| **GHSL GHS-POP R2023A (EU JRC)** | Population raster (scriptable, no login) | ✅ 무료 | [ghs_pop](https://human-settlement.emergency.copernicus.eu/datasets.php) | — |
| **GPW v4.11 Population Count (2020, 30 arc-sec)** | LitPop population layer (required) | 🔑 가입 필요 | [gpw](https://beta.sedac.ciesin.columbia.edu/data/set/gpw-v4-population-count-rev11/data-download) | — |
| **WorldPop 1 km population (no login)** | GPW alternative for LitPop / from_raster | ✅ 무료 | [worldpop](https://hub.worldpop.org/geodata/listing?id=64) | `~/climada/data/` |

### 보정·검증 (`calibration`)

| 데이터 | 쓰이는 곳 | 접근 | 링크 | 저장 위치 |
|---|---|---|---|---|
| **EEA economic-loss indicators (EU)** | EU country-year loss anchors | ✅ 무료 | [eea_losses](https://www.eea.europa.eu/en/analysis/indicators/economic-losses-from-climate-related/economic-losses-and-fatalities-caused) | — |
| **EM-DAT international disaster losses** | Calibration loss anchors | 💰 상업 유료 | [emdat](https://www.emdat.be/) | — |
| **FEMA OpenFEMA NFIP Redacted Claims (US flood)** | Impact-function calibration (US flood) | ✅ 무료 | [openfema_nfip](https://www.fema.gov/openfema-data-page/fima-nfip-redacted-claims-v2) | — |

### 예보 (`forecast`)

| 데이터 | 쓰이는 곳 | 접근 | 링크 | 저장 위치 |
|---|---|---|---|---|
| **DWD ICON-EU-EPS (windstorm forecast)** | European windstorm — StormEurope.from_icon_grib | ✅ 무료 | [dwd_icon](https://opendata.dwd.de/weather/nwp/icon-eu-eps/grib/) | — |
| **ECMWF Open Data (ensemble TC tracks)** | TC forecast — TCForecast.fetch_ecmwf() | ✅ 무료 | [ecmwf_open](https://www.ecmwf.int/en/forecasts/datasets/open-data) | — |
| **IBTrACS last-3-years (AOML ERDDAP)** | Best-track replay (off-season demo) | ✅ 무료 | [ibtracs](https://erddap.aoml.noaa.gov/hdb/erddap/tabledap/IBTRACS_last3years.html) | — |
| **NOAA NHC GIS (Atlantic/E-Pacific cones)** | TC forecast cone/track (parse to TCTracks) | ✅ 무료 | [nhc_gis](https://www.nhc.noaa.gov/gis/) | — |

---

## 4. 논문·통계 (데이터셋이 아닌 인용 출처)

폭염 사망 모델의 상수 출처와 그 상태는 **[HEAT_MORTALITY_PROVENANCE.md](HEAT_MORTALITY_PROVENANCE.md)**
에 파라미터별로 정리돼 있습니다. 현재 인용이 붙은 것:

| 값 | 출처 |
|---|---|
| 적응 기울기 0.8 °C/°C | Tobías A, et al. (2021) *Environ Epidemiol* 5:e169, [doi:10.1097/EE9.0000000000000169](https://doi.org/10.1097/EE9.0000000000000169) |
| 한국 폭염 임계 93백분위 | Kim (2020) *IJERPH* 17:5720, [doi:10.3390/ijerph17165720](https://doi.org/10.3390/ijerph17165720) |
| 적응 시나리오 +1/+2/+3 °C | Lee JY, et al. (2019) *IJERPH* 16:1026, [doi:10.3390/ijerph16061026](https://doi.org/10.3390/ijerph16061026) |
| 연중 MMP 교차검증 (KOR 89th) | Gasparrini A, et al. (2015) *Lancet* 386:369, [doi:10.1016/S0140-6736(14)62114-0](https://doi.org/10.1016/S0140-6736\(14\)62114-0) |

## 5. 관련 문서

| 문서 | 내용 |
|---|---|
| [KMA_DOWNLOAD_LIST.md](KMA_DOWNLOAD_LIST.md) | 기상청 포털에서 어느 행을 받는지, 파일 ID까지 |
| [RISK_REGISTER.md](RISK_REGISTER.md) §E | 국내 소스 14곳의 접근성 실사 (라이선스·게이트·포맷) |
| [OBSERVED_LOSSES_KR_SPEC.md](OBSERVED_LOSSES_KR_SPEC.md) | 재해연보 적재 명세 (스키마·단위·함정) |
| [USER_GUIDE.md](USER_GUIDE.md) §7 | 자동/무료/게이트 요약 |
| [HEAT_MORTALITY_PROVENANCE.md](HEAT_MORTALITY_PROVENANCE.md) | 상수별 출처와 상태 |
| README "Reproduce the analyses" | 빈 클론에서 결과까지의 명령 순서 |
