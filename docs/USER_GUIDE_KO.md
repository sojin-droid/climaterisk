# climaterisk — 사용 안내 (한국어 요약)

영어 상세판은 `USER_GUIDE.md`, 수식은 `METHODOLOGY.md`, API는 `API.md`를 보세요.

## 설치 (한 번)

```bash
git clone https://github.com/sojin-droid/climaterisk.git && cd climaterisk
uv sync --all-extras                                  # 백엔드
npm --prefix frontend/climaterisk install             # 프론트엔드
conda env create -f worker/climaterisk_worker/env_climada.yml --prefix ./.climada-env   # CLIMADA 워커, 20–25분
```

필요: Python 3.11+ 와 `uv`, Node.js 18+, conda(또는 mamba), 디스크 약 10 GB. 기본 사용에는 계정·API 키가 필요 없습니다.

## 실행

macOS/Linux는 `./run.command`, Windows는 `run.bat` 더블클릭. 백엔드(8099)와 프론트(5174)를 띄우고 브라우저를 엽니다.

## 화면 순서

1. **Map** — 지도를 클릭해 자산을 놓고 업종·가치·통화·**인원(headcount)** 을 입력합니다. 국가 전체를 보려면 *Modeled exposure*에서 기준도시 인구(데이터 불필요) 또는 WorldPop(무료 다운로드)을 선택합니다.
2. **Scenarios** — 기후 경로(RCP2.6/4.5/6.0/8.5), 전환 경로(NGFS), 기준 연도.
3. **Results** — 재해를 고르고 **Run analysis**. 연평균 피해, 미래 변화율, 자산별 지도, 재현주기 곡선. 재현주기는 기록 길이의 절반까지만 표시합니다. 사망·생산성 같은 비화폐 결과는 통화 합계에서 제외됩니다. CSV/GeoJSON/PDF 내보내기 가능.
4. **Adapt** 적응 비용편익 · **Finance** DSCR·등급·NPV · **Supply** 공급망 간접손실 · **Data** 해저드 카탈로그와 데이터 수집 · **Method** 방법론.

## 한국 포트폴리오

- 태풍·하천홍수·산불·지진은 첫 실행 때 CLIMADA Data API에서 자동으로 받아 `data/hazard_db/`에 캐시합니다.
- 폭염 사망은 기상청 남한상세 1 km 파일이 필요합니다(로그인). 절차와 파일 목록: `KMA_DOWNLOAD_LIST.md` → 파일을 `~/climada/data/kma/`에 넣고 `./.climada-env/bin/python scripts/heat_korea.py register`.
- 홍수위험지도 SHP는 `python3 scripts/fetch_floodmap_kor.py`로 받되 비상업 라이선스입니다.
- 취약성 곡선은 국내 손실자료로 보정하기 전까지 글로벌 기본값입니다(`RISK_REGISTER.md` C절).

## 자주 겪는 문제

| 증상 | 조치 |
|---|---|
| "worker env not found" | `./.climada-env` 생성(설치 3단계) |
| `heat_mortality has no local hazard` | 해저드를 먼저 등록(README 3단계 또는 `heat_korea.py register`) |
| LitPop이 GPW를 요구 | 로그인 필요 — WorldPop 또는 기준도시 인구로 대체 |
| 결과가 0 | 카드의 해석문 확인: footprint 밖 / 임계 미만 / 데이터 없음 |
| 새로 고침 후 결과 사라짐 | 결과는 브라우저 세션 단위로 복원됩니다. 다른 브라우저·프로필은 새 세션입니다 |
| 포트 충돌 | `.env`의 `CLIMATERISK_BACKEND_PORT` / `CLIMATERISK_FRONTEND_PORT` |
